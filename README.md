## Data model

A fictional infrastructure investment portfolio in MySQL (`data/schema.sql`). All money columns
are in millions of US dollars (suffix `_musd`). Every child table joins to `companies` on `company_id`.

| Table | One row per | Main columns |
|---|---|---|
| `companies` | portfolio company | `company_code`, `name`, `sector`, `subsector`, `country`, `hq_city`, `ownership_pct`, `acquisition_date`, `status` |
| `financials` | company per fiscal year (2021-2025, while owned) | `fiscal_year`, `revenue_musd`, `ebitda_musd`, `net_debt_musd`, `capex_musd` |
| `quarterly_financials` | company per fiscal year per quarter (Q1 2024 to Q3 2026) | `fiscal_year`, `quarter` (1-4), `revenue_musd`, `revenue_target_musd`, `ebitda_musd` |
| `assets` | physical asset | `asset_name`, `asset_type`, `country`, `city`, `capacity_value`, `capacity_unit`, `commissioning_date`, `book_value_musd` |
| `deals` | transaction | `deal_type`, `deal_date`, `deal_value_musd`, `equity_stake_pct`, `counterparty`, `status` |

`quarterly_financials` is derived from the cleaned annual figures (`data/quarterly.py`): the four
quarters of revenue and EBITDA for 2024 and 2025 add up exactly to the matching `financials` row.
FY2026 is still open: Q1-Q3 2026 (the latest completed quarter) are projected from 2025 for companies
still held, and there is no annual 2026 row in `financials` yet. A quarter where
`revenue_musd < revenue_target_musd` missed its target. Some companies beat every quarter,
some miss every quarter, and the rest are mixed.

## Demo

### Claude reads the database through the MCP server
![Claude listing the tables in the portfolio database](docs/screenshots/list-tables.png)

### Write access is blocked
Asked to delete every company, Claude refuses: the MCP server only allows single SELECT
statements, and it connects as a read-only MySQL user, so there are two layers of protection.

![Claude refusing to delete data](docs/screenshots/delete-blocked.png)

### Answering business questions from live data
![Energy companies that missed their revenue target](docs/screenshots/missed-targets.png)
## Backend API (Phase 4)

`backend/main.py` is a FastAPI app. `POST /chat` sends a question to Claude (Sonnet) through the
Claude Agent SDK. Claude signs in with your `claude login` credentials, not an API key, and its only
tools are the read-only MCP server in `mcp_server/server.py`. The system prompt is read from
`prompts/system.md` on every request. Reusable question templates are in `prompts/`.

Each request is logged to `assistant_logs.query_log` (time, question, answer length, tools used,
duration, status) by an INSERT-only account. Set `QUERY_LOGGER_PASSWORD` in `.env`, then create the
table and account once:

```powershell
.venv\Scripts\python.exe data\create_logger_user.py
```

Start the server and open http://127.0.0.1:8000/docs:

```powershell
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

## Chat frontend (Phase 5)

`frontend/` is a Vite + React single-page chat. Answers are rendered as Markdown (tables included,
via `react-markdown` + `remark-gfm`), with the tools Claude used and the response time underneath.
In development, Vite proxies `/chat` and `/health` to the backend on http://127.0.0.1:8000, so
FastAPI needs no CORS settings.

Install the packages once (needs Node.js 20.19+):

```powershell
cd frontend
npm install
```

Run the backend and the frontend at the same time, each in its own PowerShell window, both
started from the project root:

```powershell
# Window 1: backend
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Window 2: frontend
npm --prefix frontend run dev
```

Then open http://localhost:5173. If the backend isn't running, the page says so instead of failing
silently.

![Portfolio Analyst Assistant chat screen](docs/screenshots/chat-ui.png)

## Documents (Phase 6)

Upload PDF and Excel files, have Claude summarize them with page/sheet citations, and combine a
company's documents with its database figures into a one-page investment summary.

- **Storage**: Azure Blob Storage through the `azure-storage-blob` SDK. Locally that's the
  [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite) emulator on
  `127.0.0.1:10000`. Files go into a private container named `documents`, created on startup if
  missing: the original under `files/<id>/<name>` and the extracted text plus metadata under
  `extracted/<id>.json`.
- **Extraction** (`backend/documents.py`): PDFs are read page by page with `pypdf` (cited as `p.2`),
  Excel workbooks sheet by sheet with `openpyxl` (cited as `sheet Budget`). Only `.pdf` and `.xlsx`
  up to 10 MB are accepted; the extension and the file's first bytes must both match. Scanned PDFs
  without a text layer are rejected.
- **Prompts**: `prompts/document_summary.md` and `prompts/one_page_summary.md` (re-read on every
  request). Every claim must cite its source, e.g. `[Alder_Q3_2026.pdf, p.2]` or `[DB: financials]`.
- **Prompt-injection defence**: document text is treated as data, never as instructions. It is
  HTML-escaped (so it can't close or fake the tags around it), wrapped in `<documents>` /
  `<section cite="...">` tags, and both the system prompt and the user message tell Claude to ignore
  any instructions inside it. Summaries get no tools at all; one-pagers get only the read-only
  database tools.

| Endpoint | What it does |
|---|---|
| `GET /companies` | Company codes and names (for the company picker) |
| `POST /documents/upload` | Multipart form: `file` (PDF/.xlsx, max 10 MB), optional `company_code` |
| `GET /documents` | Uploaded documents (metadata only) |
| `POST /documents/{id}/summarize` | Cited summary of one document |
| `POST /companies/{company_code}/one-page-summary` | Database figures + that company's documents, cited |

### Set up Azurite

Install Azurite once (needs Node.js), and the new Python packages:

```powershell
npm install -g azurite
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Add this line to `.env` (it's in `.env.example`); `UseDevelopmentStorage=true` points the SDK at
Azurite's default account:

```
AZURE_STORAGE_CONNECTION_STRING=UseDevelopmentStorage=true
```

For real Azure, use the storage account's connection string instead. Azurite's data lives in
`.azurite/`, which is git-ignored.

### Sample documents

`data/make_sample_documents.py` writes four **fictional** documents to `data/sample_docs/`, with
every reported figure read from the database so they always match it:

| File | Company | What it is |
|---|---|---|
| `Alder_Q3_2026.pdf` | Alder Power Group (CO-048) | 3-page quarterly report explaining the Q3 2026 revenue miss |
| `Ironbridge_Investment_Memo.pdf` | Ironbridge Digital Partners (CO-004) | Investment committee memo for a data center follow-on |
| `Summit_Energy_Budget_FY2027.xlsx` | Summit Energy Partners (CO-044) | FY2027 budget by quarter, 2026 actual vs target, assumptions |
| `Pinnacle_Board_Update_Q3_2026.pdf` | Pinnacle Utilities Holdings (CO-034) | Board update for a water company |

```powershell
.venv\Scripts\python.exe data\make_sample_documents.py
```

Then upload them in the **Documents** tab, linking each to its company.

### Run everything (three PowerShell windows, all from the project root)

```powershell
# Window 1: Azurite (blob, queue and table emulators; data in .azurite\)
azurite --silent --location .azurite
```

```powershell
# Window 2: backend
.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Window 3: frontend
npm --prefix frontend run dev
```

Open http://localhost:5173 and switch to the **Documents** tab: upload a file (optionally linked to
a company), click **Summarize** on a document, or **One-page summary** on a company. Results are
rendered as Markdown. The chat keeps working even if Azurite is down; document calls then return
a clear 503.

Tests for upload validation (wrong types, oversize, empty and mismatched files), PDF/Excel text
extraction, prompt escaping and the endpoints are in `tests/test_documents.py`:

```powershell
.venv\Scripts\python.exe -m pytest
```
### Document summaries with sources
Uploaded reports are stored in Azure Blob Storage (developed locally with the Azurite emulator), and every claim cites its file and page.
![Document summary with citations](docs/screenshots/document-summary.png)

### One-page investment summary
Combines a company's database figures with its uploaded documents into a one-page summary, with sources cited.
![One-page company summary, part 1](docs/screenshots/one-page-summary1.png)
![One-page company summary, part 2](docs/screenshots/one-page-summary2.png)