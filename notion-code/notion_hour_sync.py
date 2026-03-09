"""
Notion Hour Tracker Sync — Workspace-Wide
==========================================
Scans ALL pages in your Notion workspace for comments in the format:
    0.5H: onQ review
    1.25H: fixed login bug

For each page that has hour comments AND an "Actual Hours" property,
it sums all hour entries and updates the property with the total.

Pages without hour comments or without the "Actual Hours" property
are silently skipped.

Run:  python notion_hour_sync.py
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
#   Windows: set NOTION_API_KEY=secret_xxxx
#   Mac/Linux: export NOTION_API_KEY=secret_xxxx
# ─────────────────────────────────────────────
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
HOURS_PROPERTY = "Actual Hours"   # exact property name in your Notion databases
# ─────────────────────────────────────────────

NOTION_VERSION = "2022-06-28"
BASE_URL       = "https://api.notion.com/v1"
HOUR_PATTERN   = re.compile(r"^(\d+(?:\.\d+)?)H:", re.IGNORECASE)


def notion_request(method, path, body=None):
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        error_body = e.read().decode()
        print(f"  ✗ API error {e.code}: {error_body}")
        return None


def get_all_pages():
    """Search for every page in the workspace the integration can access."""
    pages, cursor = [], None
    print("  Searching workspace for all pages...")
    while True:
        body = {"filter": {"value": "page", "property": "object"}, "page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        result = notion_request("POST", "/search", body)
        if not result:
            break
        batch = result.get("results", [])
        pages.extend(batch)
        print(f"  ...found {len(pages)} pages so far", end="\r")
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    print()
    return pages


def get_comments(page_id):
    """Fetch all comments for a page."""
    result = notion_request("GET", f"/comments?block_id={page_id}")
    return result.get("results", []) if result else []


def extract_hours_from_comments(comments):
    """Sum all hour entries matching the pattern e.g. '0.5H: description'."""
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


def get_page_title(page):
    """Extract the title from a page for display purposes."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            texts = prop.get("title", [])
            if texts:
                return texts[0].get("plain_text", "Untitled")
    return "Untitled"


def has_hours_property(page):
    """Check if this page has the Actual Hours property."""
    return HOURS_PROPERTY in page.get("properties", {})


def get_current_hours(page):
    """Read the current value of the Actual Hours property."""
    prop = page.get("properties", {}).get(HOURS_PROPERTY, {})
    return prop.get("number") or 0.0


def update_hours(page_id, total_hours):
    """Write the new total to the Actual Hours property."""
    body = {"properties": {HOURS_PROPERTY: {"number": total_hours}}}
    return notion_request("PATCH", f"/pages/{page_id}", body)


def main():
    if not NOTION_API_KEY:
        print("\n⚠️  NOTION_API_KEY environment variable is not set.")
        print("   Set it with: set NOTION_API_KEY=secret_xxxx (Windows)")
        sys.exit(1)

    if DRY_RUN:
        print("\n🧪 DRY RUN MODE — no changes will be made to Notion\n")
    print("\n🔍 Scanning your Notion workspace...\n")
    pages = get_all_pages()
    print(f"   Found {len(pages)} total page(s)\n")

    updated  = 0
    skipped  = 0
    no_prop  = 0
    errors   = 0

    for page in pages:
        page_id = page["id"]
        title   = get_page_title(page)

        # Skip pages that don't have the Actual Hours property
        if not has_hours_property(page):
            no_prop += 1
            continue

        comments = get_comments(page_id)
        total, entries = extract_hours_from_comments(comments)

        # Skip pages with no matching hour comments
        if not entries:
            skipped += 1
            continue

        current = get_current_hours(page)

        print(f"  ✔ {title}")
        for hrs, text in entries:
            print(f"      {hrs}h  →  {text}")
        print(f"      Total: {total}h  |  Current property: {current}h")

        if total == current:
            print(f"      Already up to date ✓\n")
            skipped += 1
            continue


        if total < current:
            print(f"      Skipped — comment total ({total}h) is less than current value ({current}h), keeping existing value\n")
            skipped += 1
            continue
        if DRY_RUN:
            print(f"      🧪 Would update: {current}h → {total}h (dry run, no change made)\n")
            updated += 1
            continue

        result = update_hours(page_id, total)
        if result:
            print(f"      ✅ Updated to {total}h\n")
            updated += 1
        else:
            print(f"      ✗ Failed to update\n")
            errors += 1

    print("─" * 50)
    if DRY_RUN:
        print(f"  🧪 DRY RUN — no changes were made to Notion")
        print(f"  🧪 Would update:          {updated} page(s)")
    else:
        print(f"  ✅ Updated:               {updated} page(s)")
    print(f"  — Skipped (up to date):  {skipped} page(s)")
    print(f"  — No 'Actual Hours' prop: {no_prop} page(s)")
    if errors:
        print(f"  ✗ Errors:               {errors} page(s)")
    print()


def print_setup_guide():
    print("""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SETUP GUIDE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. INSTALL PYTHON (if not already installed)
   Download from https://python.org — no extra packages needed.

2. CREATE A NOTION INTEGRATION
   a. Go to https://www.notion.so/my-integrations
   b. Click "New integration"
   c. Name it (e.g. "Hour Tracker"), select your workspace
   d. Copy the "Internal Integration Secret" — this is your API key

3. CONNECT THE INTEGRATION TO YOUR DATABASES
   For each database you want to sync:
   a. Open the database in Notion
   b. Click "..." (top right) → "Connect to" → select your integration

   ⚠️  Notion requires explicit connection per database.
       The integration can only see pages in databases it's connected to.

4. EDIT THIS SCRIPT
   Open notion_hour_sync.py and fill in:
     NOTION_API_KEY = "secret_xxxxxxxxxxxx"

5. RUN THE SCRIPT (Windows)
   Open Command Prompt or PowerShell in the script's folder:

   Dry run first (no changes made — just shows what would happen):
     python notion_hour_sync.py --dry-run

   Live run (actually updates Actual Hours):
     python notion_hour_sync.py

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  COMMENT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Comments must start with:   {hours}H: {description}
  Examples:
    0.5H: onQ review
    1.25H: fixed login bug
    2H: client call prep

  The script sums ALL matching comments on each page
  and writes the total to the "Actual Hours" property.
  Pages without that property are automatically skipped.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


if __name__ == "__main__":
    main()
