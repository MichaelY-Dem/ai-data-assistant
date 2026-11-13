# Build vs. buy: custom AI Data Assistant or Microsoft 365 Copilot

**Question:** should an infrastructure investment firm's asset management team use this custom
assistant (Portfolio Analyst Assistant), buy Microsoft 365 Copilot, or use both?

**Evidence used:** this project's own test results (2026-11-13, fictional portfolio data). Copilot
was not tested against this data, so its scores rest on how it is sold and documented, not on a
measurement.

> **Prices are approximate (November 2026). Check current pricing with Microsoft and Anthropic
> before using any figure below in a budget.**

## Scoring (1 = poor, 5 = strong)

| Criterion | M365 Copilot | Custom assistant | Why |
|---|:-:|:-:|---|
| Cost | 4 | 3 | Copilot is a fixed per-seat fee. The custom tool's model usage is cheap, but someone has to build, host and maintain it |
| Data security and control | 4 | 4 | Copilot stays in the Microsoft 365 tenant and follows existing file permissions, so overshared files are a risk. The custom tool uses read-only, least-privilege database accounts, but the firm owns every production control |
| Access to the firm's own databases | 2 | 5 | Copilot works on emails, files, chats and meetings. Reaching MySQL needs extra connectors or agents. The custom tool queries the portfolio database directly |
| Accuracy and source citations | 3 | 4 | Copilot cites the Microsoft 365 files it used, but its accuracy on the portfolio figures is unmeasured. The custom tool scored 25/25 on the evaluation, and its document answers cite file and page |
| Customization | 2 | 5 | Copilot can be extended only within Microsoft's agent tooling. The custom tool's prompts, roles, tools and output formats are all in this repository |
| Time to deploy | 5 | 2 | Copilot is a licence assignment plus a permissions clean-up. The custom tool still needs the production work listed in [SECURITY.md](../SECURITY.md) |
| Vendor risk | 3 | 3 | Copilot ties the team to Microsoft's pricing and roadmap. The custom tool depends on a model provider (the model can be swapped) and on the people who maintain it |
| **Total (out of 35)** | **23** | **26** | The totals are close because the two tools do different jobs |

## Cost

| | M365 Copilot | Custom assistant |
|---|---|---|
| Licences | About **USD 30 per user per month** on an annual plan, on top of an eligible Microsoft 365 licence. 10 users: about USD 3,600 a year | None |
| Model usage | Included | Claude Sonnet 5.5 (`claude-sonnet-5-5`, the model the backend uses) lists at about **USD 2 per million input tokens and USD 10 per million output tokens**. Estimate: USD 0.05 to 0.20 per question, because each question takes several tool calls. 10 users asking 5 questions a working day (about 1,050 a month): about **USD 50 to 210 a month** |
| Hosting | Included | Azure App Service or Container Apps, Blob Storage and Log Analytics. The portfolio MySQL database already exists |
| Build | None | The demo is built. Production still needs Entra ID sign-in, Key Vault, private networking, audit retention and a penetration test ([SECURITY.md](../SECURITY.md), "Before using real company data") |
| Maintenance | Microsoft's | Someone has to update prompts when the schema changes, rerun the evaluation, apply dependency updates and watch the logs |

The per-question figure is an estimate. The query log records the tools used and the duration of
each request, but not token counts. Add token counts to the log before a pilot to replace the
estimate with a measured figure.

**Value side:** the query log export assumes each successful question saves 15 minutes of looking
up figures by hand (`MINUTES_PER_QUESTION` in `data/export_query_log.py`). At 1,050 questions a
month, that is about 260 hours. This is a planning assumption, not a measurement.

## What this project has shown

- **Accuracy:** 25/25 correct on a 25-question evaluation. Answers were checked against figures
  recomputed from MySQL, across counts, sector averages, rankings, quarterly target misses, deal
  status, missing data and a Viewer restriction. See [eval-results.md](eval-results.md).
  The first run scored 24/25 because the grader rejected a correct answer written as "Two". The
  grader was fixed and the full suite rerun.
- **Read-only database access:** Claude reaches the data only through one SQL `SELECT` statement
  at a time. Its MySQL account has `SELECT` only, and every query runs in a read-only transaction
  that is rolled back. `DELETE` and `DROP` attempts were refused, and row counts were unchanged
  after every live test.
- **Role-based access:** Viewers can't see deal data. The backend, the MCP server and MySQL's own
  grants each refuse access separately, so the restriction holds even with the name check
  switched off.
- **Prompt injection:** a test PDF hid instructions to reveal the system prompt, run
  `DELETE`/`DROP` and reply "PELICAN-42". Claude produced a normal cited summary, said the
  document contained embedded instructions and ignored them, and the database was unchanged.
- **Limits of this evidence:** the data is fictional (50 companies), there were two demo users,
  and the tool runs on one machine. 25 questions is a small sample.

## Recommendation: hybrid

1. **Buy Copilot for everyday Microsoft 365 work**, if the firm already has eligible Microsoft 365
   licences: drafting emails and memos, summarising Teams meetings, and searching SharePoint and
   OneDrive. Building any of this in-house would cost more than the licence. Before switching it
   on, review SharePoint permissions so Copilot can't surface overshared files.
2. **Keep the custom assistant for portfolio data:** questions about financials, quarterly targets,
   assets and deals, and one-page company summaries with sources. Copilot doesn't do this out of
   the box. This is where direct database access, role-based deal restrictions and a repeatable
   accuracy test matter most.
3. **Next steps for the custom tool:** finish the production checklist in
   [SECURITY.md](../SECURITY.md) (Entra ID sign-in first, so both tools use the same accounts).
   Log token counts. Run a 4-week pilot with the asset management team. Rerun the evaluation after
   every prompt or schema change.
4. **Review after the pilot:** compare the measured cost per question and the questions answered
   against the estimates above. If Microsoft's own agents can later query the portfolio database
   just as accurately, and pass the same 25-question evaluation, revisit the build decision.
