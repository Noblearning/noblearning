"""
Notion Hour Tracker Sync
=========================
Syncs hour-tracking comments to the "Actual Hours" property across your
Notion workspace.

Only queries databases that have an "Actual Hours" property, skipping
everything else in your workspace.

Comment format:
  0.5H: onQ review
  1.25H: fixed login bug
  2H: client call prep

Run:
  python notion_hour_sync.py            # live run
  python notion_hour_sync.py --dry-run  # preview only, no changes made

Exit code is non-zero if ANY Notion API call failed, so a broken run shows
up as a red X in GitHub Actions instead of a misleading green check.
"""

import re
import sys
import os
import json
import time
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

DRY_RUN = "--dry-run" in sys.argv

# ─────────────────────────────────────────────
# CONFIGURATION
# API key is read from the NOTION_API_KEY environment variable.
# Set it in GitHub Secrets, or locally by running:
#   Windows:   set NOTION_API_KEY=secret_xxxx
#   Mac/Linux: export NOTION_API_KEY=secret_xxxx
# ─────────────────────────────────────────────
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "").strip()
HOURS_PROPERTY = "Actual Hours"   # exact property name in your Notion databases
STATUS_PROPERTY = "Status"        # name of the status property (if a database has one)
# Pages with these statuses are skipped. Compared case-insensitively.
EXCLUDED_STATUSES = {"complete", "up for discussion", "not started"}
# ─────────────────────────────────────────────

NOTION_VERSION = "2022-06-28"
BASE_URL       = "https://api.notion.com/v1"
HOUR_PATTERN   = re.compile(r"^(\d+(?:\.\d+)?)H:", re.IGNORECASE)

# Every failed API call is recorded here so main() can fail the run at the end.
API_ERRORS = []


def notion_request(method, path, body=None, retries=3):
    url  = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None

    for attempt in range(retries + 1):
        req = Request(url, data=data, method=method, headers={
            "Authorization":  f"Bearer {NOTION_API_KEY}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type":   "application/json",
        })
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except HTTPError as e:
            msg = e.read().decode(errors="replace")
            # Rate limited or transient server error: wait and retry
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                try:
                    wait = float(e.headers.get("Retry-After", 2 ** attempt))
                except (TypeError, ValueError):
                    wait = 2 ** attempt
                time.sleep(wait)
                continue
            print(f"  X API error {e.code} on {method} {path}: {msg}")
            API_ERRORS.append((method, path, e.code, msg))
            return None
        except URLError as e:
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            print(f"  X Network error on {method} {path}: {e.reason}")
            API_ERRORS.append((method, path, "network", str(e.reason)))
            return None


def plain_title(rich_text_list, fallback="Untitled"):
    text = "".join(t.get("plain_text", "") for t in rich_text_list or [])
    return text or fallback


def get_relevant_databases():
    """Return [(id, title)] for databases that have an 'Actual Hours' property."""
    found, cursor, total_seen = [], None, 0
    print("  Finding databases with 'Actual Hours' property...")
    while True:
        body = {"filter": {"value": "database", "property": "object"}, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_request("POST", "/search", body)
        if not result:
            break
        for db in result.get("results", []):
            total_seen += 1
            props = db.get("properties", {})
            title = plain_title(db.get("title"))
            if HOURS_PROPERTY not in props:
                continue
            ptype = props[HOURS_PROPERTY].get("type")
            if ptype != "number":
                # Writing a number to a formula/rollup/text property fails, so say so up front.
                print(f"  ! '{title}': '{HOURS_PROPERTY}' is type '{ptype}', not 'number' - updates will fail")
            found.append((db["id"], title))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    print(f"  Integration can see {total_seen} database(s); {len(found)} have '{HOURS_PROPERTY}'")
    return found


def get_status_name(page):
    """Return the page's status (or select) name, or None if it has no status."""
    prop = page.get("properties", {}).get(STATUS_PROPERTY) or {}
    value = prop.get("status") or prop.get("select") or {}
    return value.get("name")


def is_active(page):
    status = get_status_name(page)
    return status is None or status.strip().lower() not in EXCLUDED_STATUSES


def get_pages_from_database(db_id):
    """Fetch all pages, then drop completed/inactive ones client-side.

    Filtering on the client (instead of a Status filter in the query) means the
    query can't fail if a database has no 'Status' property, or if the Status
    property or its options have changed.
    """
    pages, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_request("POST", f"/databases/{db_id}/query", body)
        if not result:
            break
        pages.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return [p for p in pages if is_active(p)], len(pages)


def get_comments(page_id):
    """Fetch ALL comments on a page (the API returns them in pages)."""
    comments, cursor = [], None
    while True:
        path = f"/comments?block_id={page_id}&page_size=100"
        if cursor:
            path += f"&start_cursor={cursor}"
        result = notion_request("GET", path)
        if not result:
            break
        comments.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return comments


def extract_hours_from_comments(comments):
    total, entries = 0.0, []
    for comment in comments:
        # Join the pieces first: a comment with a mention, link or formatting is split
        # into several rich_text blocks, and we want to match the comment as a whole.
        text  = "".join(b.get("plain_text", "") for b in comment.get("rich_text", [])).strip()
        match = HOUR_PATTERN.match(text)
        if match:
            hours = float(match.group(1))
            total += hours
            entries.append((hours, text))
    return total, entries


def get_page_title(page):
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            texts = prop.get("title", [])
            if texts:
                return plain_title(texts)
    return "Untitled"


def get_current_hours(page):
    prop = page.get("properties", {}).get(HOURS_PROPERTY, {})
    return prop.get("number") or 0.0


def update_hours(page_id, total_hours):
    body = {"properties": {HOURS_PROPERTY: {"number": total_hours}}}
    return notion_request("PATCH", f"/pages/{page_id}", body)


def main():
    if not NOTION_API_KEY:
        print("\nNOTION_API_KEY environment variable is not set.")
        print("   Windows:   set NOTION_API_KEY=secret_xxxx")
        print("   Mac/Linux: export NOTION_API_KEY=secret_xxxx\n")
        sys.exit(1)

    if DRY_RUN:
        print("\nDRY RUN MODE - no changes will be made to Notion\n")

    # Only look at databases that have the "Actual Hours" property
    databases = get_relevant_databases()
    if not databases:
        print("\n  No databases with 'Actual Hours' found.")
        if API_ERRORS:
            print("  The search request failed (see error above) - check that the")
            print("  NOTION_API_KEY secret is current and the integration still exists.\n")
        else:
            print("  Make sure your integration is connected to your databases, and that")
            print(f"  the property is named exactly '{HOURS_PROPERTY}'.\n")
        sys.exit(1)

    # Collect pages from relevant databases only
    all_pages = []
    for db_id, db_title in databases:
        pages, fetched = get_pages_from_database(db_id)
        print(f"  {db_title}: {fetched} page(s), {len(pages)} active")
        all_pages.extend(pages)

    print(f"  {len(all_pages)} page(s) to check\n")

    updated = 0
    skipped = 0
    errors  = 0

    for page in all_pages:
        page_id = page["id"]
        title   = get_page_title(page)
        current = get_current_hours(page)

        comments = get_comments(page_id)
        total, entries = extract_hours_from_comments(comments)

        if not entries:
            skipped += 1
            continue

        print(f"  {title}")
        for hrs, text in entries:
            print(f"      {hrs}h  ->  {text}")
        print(f"      Total: {total}h  |  Current property: {current}h")

        if abs(total - current) < 1e-9:
            print(f"      Already up to date\n")
            skipped += 1
            continue

        if total < current:
            print(f"      Skipped - comment total ({total}h) is less than current value ({current}h), keeping existing value\n")
            skipped += 1
            continue

        if DRY_RUN:
            print(f"      DRY RUN: Would update {current}h -> {total}h\n")
            updated += 1
            continue

        result = update_hours(page_id, total)
        if result:
            print(f"      Updated to {total}h\n")
            updated += 1
        else:
            print(f"      Failed to update\n")
            errors += 1

    print("-" * 50)
    if DRY_RUN:
        print(f"  DRY RUN - no changes were made")
        print(f"  Would update:         {updated} page(s)")
    else:
        print(f"  Updated:              {updated} page(s)")
    print(f"  Skipped (up to date): {skipped} page(s)")
    if errors:
        print(f"  Errors:               {errors} page(s)")
    if API_ERRORS:
        print(f"  API calls that failed: {len(API_ERRORS)}")
        for method, path, code, _ in API_ERRORS[:10]:
            print(f"      {code}  {method} {path}")
    print()

    # Make GitHub Actions show a red X when anything went wrong
    if API_ERRORS:
        sys.exit(1)


if __name__ == "__main__":
    main()
