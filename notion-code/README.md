# Notion Hour Sync

Automatically syncs hour-tracking comments to the **Actual Hours** property across your Notion workspace, running every 15 minutes via GitHub Actions.

## Comment format

Add comments to any Notion page in this format:

```
0.5H: onQ review
1.25H: fixed login bug
2H: client call prep
```

The script will sum all matching comments on each page and write the total to the **Actual Hours** property.

## Setup

### 1. Fork or clone this repository

### 2. Add your Notion API key as a GitHub Secret
- Go to your repo on GitHub → **Settings** → **Secrets and variables** → **Actions**
- Click **New repository secret**
- Name: `NOTION_API_KEY`
- Value: your Notion internal integration secret (e.g. `secret_xxxxxxxxxxxx`)

### 3. Create a Notion internal integration
- Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
- Click **New integration** → select **Internal**
- Copy the **Internal Integration Secret**

### 4. Connect your databases to the integration
For each Notion database you want to sync:
- Open the database → click `...` (top right) → **Connect to** → select your integration

### 5. Enable GitHub Actions
- Go to your repo → **Actions** tab → enable workflows if prompted

The sync will now run automatically every 15 minutes. You can also trigger it manually from the Actions tab anytime.

## Running locally

```bash
# Set your API key
set NOTION_API_KEY=secret_xxxxxxxxxxxx   # Windows
export NOTION_API_KEY=secret_xxxxxxxxxxxx # Mac/Linux

# Dry run (no changes made)
python notion_hour_sync.py --dry-run

# Live run
python notion_hour_sync.py
```
