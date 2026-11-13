# ai-data-assistant

An AI assistant that answers questions about a fictional infrastructure investment
portfolio (energy, data centers, fiber, toll roads, water) stored in MySQL.

## Layout

| Folder | Purpose |
|---|---|
| `backend/` | Application code (API / assistant logic), to be built |
| `mcp_server/` | MCP server exposing the database to the AI, to be built |
| `data/` | Schema, fake-data generator, cleaning/loading pipeline, DB user setup |
| `data/raw/`, `data/clean/` | Generated CSVs (git-ignored; regenerate them any time) |
| `prompts/` | Prompt templates for the assistant |
| `tests/` | pytest suite |
| `docs/` | Documentation, including the generated `data-cleaning.md` log |

## Setup (Windows PowerShell)

```powershell
# 1. Activate the virtual environment and install packages
.\.venv\Scripts\Activate.ps1          # create it first with: py -3.12 -m venv .venv
pip install -r requirements.txt

# 2. Create your .env and fill in the two passwords in a text editor
Copy-Item .env.example .env
notepad .env

# 3. Generate messy data, then clean it and load it into MySQL (database `portfolio`)
python data\generate_fake_data.py
python data\clean_and_load.py           # add --skip-load to clean without MySQL

# 4. Create the read-only account the app uses
python data\create_reader_user.py

# 5. Run the tests
pytest
```

If `Activate.ps1` is blocked, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

## Data model

All money columns are in millions of USD (`_musd`).

- **companies**: one row per portfolio company (sector, country, our stake, acquisition date, status)
- **financials**: revenue, EBITDA, net debt and capex per company per fiscal year
- **assets**: physical infrastructure (solar farms, data center campuses, fiber routes, toll roads, water plants) with capacity
- **deals**: acquisitions, follow-on investments, refinancings and exits

See `data/schema.sql` for the full definition and `docs/data-cleaning.md` for how the raw data was fixed.
