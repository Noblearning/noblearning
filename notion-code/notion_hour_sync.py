"""
Notion Hour Tracker Sync
=========================
Syncs hour-tracking comments to the "Actual Hours" property.

Comment format examples:

  0.5H: onQ review
  1.25H: fixed login bug
  2H: client call prep

Run locally:
  python notion_hour_sync.py
  python notion_hour_sync.py --dry-run
"""

import re
import sys
import os
import json
import datetime
from urllib.request import urlopen, Request
from urllib.error import HTTPError

DRY_RUN = "--dry-run" in sys.argv

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")

NOTION_VERSION = "2022-06-28"
BASE_URL = "https://api.notion.com/v1"

HOURS_PROPERTY = "Actual Hours"

# 🔧 CHANGE THIS
# Add the database IDs you want this script to monitor
DATABASE_IDS = [
    "3048b36ce4b78070b8cbc17ae00cb441"
]

# Sync timestamp file
SYNC_FILE = os.path.join(os.path.dirname(__file__), ".last_sync")

# Hour comment pattern
HOUR_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)\s*[hH]:", re.IGNORECASE)


# ─────────────────────────────────────────────
# NOTION API HELPER
# ─────────────────────────────────────────────

def notion_request(method, path, body=None):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None

    req = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {NOTION_API_KEY}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )

    try:
        with urlopen(req) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        print(f"API error {e.code}: {e.read().decode()}")
        return None


# ─────────────────────────────────────────────
# SYNC STATE
# ─────────────────────────────────────────────

def get_last_sync_time():
    if not os.path.exists(SYNC_FILE):
        return None

    with open(SYNC_FILE, "r") as f:
        return f.read().strip()


def set_last_sync_time(ts):
    with open(SYNC_FILE, "w") as f:
        f.write(ts)


# ─────────────────────────────────────────────
# DATABASE QUERIES
# ─────────────────────────────────────────────

def get_pages_from_database(db_id, last_sync):
    pages = []
    cursor = None

    while True:

        body = {
            "page_size": 100
        }

        if last_sync:
            body["filter"] = {
                "timestamp": "last_edited_time",
                "last_edited_time": {
                    "after": last_sync
                }
            }

        if cursor:
            body["start_cursor"] = cursor

        result = notion_request(
            "POST",
            f"/databases/{db_id}/query",
            body
        )

        if not result:
            break

        pages.extend(result.get("results", []))

        if not result.get("has_more"):
            break

        cursor = result.get("next_cursor")

    return pages


# ─────────────────────────────────────────────
# COMMENT PROCESSING
# ─────────────────────────────────────────────

def get_comments(page_id):
    result = notion_request("GET", f"/comments?block_id={page_id}")
    return result.get("results", []) if result else []


def extract_hours_from_comments(comments):
    total = 0.0
    entries = []

    for comment in comments:
        for block in comment.get("rich_text", []):

            text = block.get("plain_text", "").strip()

            match = HOUR_PATTERN.match(text)

            if match:
                hours = float(match.group(1))
                total += hours
                entries.append((hours, text))

    return total, entries


# ─────────────────────────────────────────────
# PAGE HELPERS
# ─────────────────────────────────────────────

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
    body = {
        "properties": {
            HOURS_PROPERTY: {
                "number": total_hours
            }
        }
    }

    return notion_request(
        "PATCH",
        f"/pages/{page_id}",
        body
    )


# ─────────────────────────────────────────────
# MAIN LOGIC
# ─────────────────────────────────────────────

def main():

    if not NOTION_API_KEY:
        print("NOTION_API_KEY not set")
        sys.exit(1)

    if DRY_RUN:
        print("\nDRY RUN MODE\n")

    last_sync = get_last_sync_time()

    print(f"Last sync: {last_sync}\n")

    all_pages = []

    for db_id in DATABASE_IDS:
        pages = get_pages_from_database(db_id, last_sync)
        all_pages.extend(pages)

    print(f"{len(all_pages)} page(s) edited since last run\n")

    updated = 0
    skipped = 0
    errors = 0

    for page in all_pages:

        page_id = page["id"]
        title = get_page_title(page)

        current = get_current_hours(page)

        comments = get_comments(page_id)

        total, entries = extract_hours_from_comments(comments)

        if not entries:
            skipped += 1
            continue

        print(title)

        for hrs, text in entries:
            print(f"  {hrs}h -> {text}")

        print(f"  Total: {total}h | Current: {current}h")

        if total == current:
            skipped += 1
            print("  Already up to date\n")
            continue

        if total < current:
            skipped += 1
            print("  Skipped (lower than existing value)\n")
            continue

        if DRY_RUN:
            print(f"  DRY RUN: would update to {total}h\n")
            updated += 1
            continue

        result = update_hours(page_id, total)

        if result:
            print(f"  Updated to {total}h\n")
            updated += 1
        else:
            print("  Failed\n")
            errors += 1

    print("-" * 40)
    print(f"Updated: {updated}")
    print(f"Skipped: {skipped}")
    print(f"Errors: {errors}")

    now = datetime.datetime.utcnow().isoformat()
    set_last_sync_time(now)


if __name__ == "__main__":
    main()