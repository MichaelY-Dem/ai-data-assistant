# Template: one-page company summary
#
# A one-page summary of a single portfolio company for an investment committee
# pack. Replace {company}, then send everything below the line as the "question"
# to POST /chat.
#
# Example: company = "the company with code CO-004"
# ---------------------------------------------------------------------------

Write a one-page summary of {company}, using only the portfolio database.

Use these sections, each short:

**Overview**: sector, subsector, country, HQ city, ownership %, acquisition date, status.

**Financial performance**: a table of revenue, EBITDA, EBITDA margin, net debt and capex for
each fiscal year in the database, followed by one or two sentences on the trend.

**Recent quarters**: a table of revenue vs revenue target for the last four quarters available,
with the variance in USD m and %, and how many of those quarters missed the target.

**Assets**: number of assets by type, total capacity per unit, and total book value.

**Deals**: the deals on record with date, type, value and status.

**Data gaps**: anything the database doesn't cover for this company.

Keep the whole summary to roughly one page (about 400 words plus tables). Don't add opinions,
forecasts or outside information that isn't in the data.
