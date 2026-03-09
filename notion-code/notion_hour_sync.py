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
"""

import re
import sys
import os
import json
from urllib.request import urlopen, Request
from urllib.error import HTTPError

DRY_RUN = "--dry-run" in sys.argv

# ─────────────────────────────────────────────
# CONFIGURATION
# API key is read from the NOTION_API_KEY environment variable.
# Set it in GitHub Secrets, or locally by running:
#   Windows:   set NOTION_API_KEY=secret_xxxx
#   Mac/Linux: export NOTION_API_KEY=secret_xxxx
# ─────────────────────────────────────────────
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
HOURS_PROPERTY = "Actual Hours"  # exact property name in your Notion databases
# ─────────────────────────────────────────────

NOTION_VERSION = "2022-06-28"
BASE_URL       = "https://api.notion.com/v1"
HOUR_PATTERN   = re.compile(r"^(\d+(?:\.\d+)?)H:", re.IGNORECASE)


def notion_request(method, path, body=None):
    url  = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req  = Request(url, data=data, method=method, headers={
        "Authorization":  f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type":   "application/json",
    })
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        print(f"  X API error {e.code}: {e.read().decode()}")
        return None


def get_relevant_database_ids():
    """Return IDs of databases that have an 'Actual Hours' property."""
    db_ids, cursor = [], None
    print("  Finding databases with 'Actual Hours' property...")
    while True:
        body = {"filter": {"value": "database", "property": "object"}, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_request("POST", "/search", body)
        if not result:
            break
        for db in result.get("results", []):
            if HOURS_PROPERTY in db.get("properties", {}):
                db_ids.append(db["id"])
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    print(f"  Found {len(db_ids)} relevant database(s)")
    return db_ids


def get_pages_from_database(db_id):
    """Fetch all pages from a database."""
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
    return pages


def get_comments(page_id):
    result = notion_request("GET", f"/comments?block_id={page_id}")
    return result.get("results", []) if result else []


def extract_hours_from_comments(comments):
    total, entries = 0.0, []
    for comment in comments:
        for block in comment.get("rich_text", []):
            text  = block.get("plain_text", "").strip()
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
                return texts[0].get("plain_text", "Untitled")
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
    db_ids = get_relevant_database_ids()
    if not db_ids:
        print("\n  No databases with 'Actual Hours' found.")
        print("  Make sure your integration is connected to your databases.\n")
        sys.exit(0)

    # Collect pages from relevant databases only
    all_pages = []
    for db_id in db_ids:
        pages = get_pages_from_database(db_id)
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

        if total == current:
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
    print()


if __name__ == "__main__":
    main()