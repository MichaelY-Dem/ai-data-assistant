# Template: company comparison
#
# Compare two or more portfolio companies side by side. Replace each
# {placeholder}, then send everything below the line as the "question" to POST /chat.
#
# Example: companies = "the two largest toll-road companies by FY2025 revenue"
#          period    = "FY2023 to FY2025, plus the 2026 quarters so far"
# ---------------------------------------------------------------------------

Compare these portfolio companies: {companies}

Period: {period}

Cover, where the database has the data:
1. Profile: sector, subsector, country, ownership %, acquisition date, status.
2. Revenue and EBITDA for each year in the period, and EBITDA margin (EBITDA / revenue).
3. Growth: revenue and EBITDA growth year on year.
4. Balance sheet: net debt and net debt / EBITDA for the latest full year.
5. Quarterly delivery: how many quarters in the period met or missed the revenue target.
6. Assets: number of assets and total book value.

Put the companies in columns and the metrics in rows in one comparison table.
End with two or three sentences on the main differences, based only on the figures shown.
If a company or a metric has no data, mark it "n/a" in the table and say so below it.
