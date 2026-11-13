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