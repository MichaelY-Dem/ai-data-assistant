"""Create 4 FAKE sample documents for portfolio companies, in data/sample_docs/.

    Alder_Q3_2026.pdf                    quarterly report explaining the Q3 2026 revenue miss (CO-048)
    Ironbridge_Investment_Memo.pdf       investment memo for a data center company (CO-004)
    Summit_Energy_Budget_FY2027.xlsx     quarterly budget for Summit Energy Partners (CO-044)
    Pinnacle_Board_Update_Q3_2026.pdf    board update for a water company (CO-034)

Every reported figure (financials, quarterly results vs target, assets, deals) is read from the
portfolio database with the read-only account, so the documents always match the data. The
narrative, the variance explanations and all forward-looking numbers (budgets, guidance,
proposals) are invented. Everything is fictional.

Run from the project root (MySQL must be running and loaded):
    .venv\\Scripts\\python.exe data\\make_sample_documents.py
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from db_config import reader_connection

OUT_DIR = Path(__file__).resolve().parent / "sample_docs"
DISCLAIMER = "FICTIONAL SAMPLE DOCUMENT - all companies, people and figures are invented for a software demo."

STYLES = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=STYLES["Heading1"], fontSize=17, spaceAfter=4, textColor=colors.HexColor("#123b5c"))
H2 = ParagraphStyle("H2", parent=STYLES["Heading2"], fontSize=12.5, spaceBefore=10, spaceAfter=4,
                    textColor=colors.HexColor("#123b5c"))
BODY = ParagraphStyle("Body", parent=STYLES["BodyText"], fontSize=9.8, leading=13.5, spaceAfter=5)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
BULLET = ParagraphStyle("Bullet", parent=BODY, leftIndent=12, bulletIndent=2)


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #

def load_company(code: str) -> dict:
    """Company row plus its annual financials, quarters, assets and deals, as floats."""
    def rows(cur, sql):
        cur.execute(sql, (code,))
        cols = [d[0] for d in cur.description]
        return [{c: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v)
                 for c, v in zip(cols, r)} for r in cur.fetchall()]

    conn = reader_connection()
    try:
        with conn.cursor() as cur:
            company = rows(cur, "SELECT * FROM companies WHERE company_code = %s")
            if not company:
                raise SystemExit(f"{code} is not in the database. Load the data first (see README).")
            join = "JOIN companies c USING (company_id) WHERE c.company_code = %s"
            data = company[0]
            data["financials"] = rows(cur, f"SELECT f.* FROM financials f {join} ORDER BY fiscal_year")
            data["quarters"] = rows(cur, f"SELECT q.* FROM quarterly_financials q {join} ORDER BY fiscal_year, quarter")
            data["assets"] = rows(cur, f"SELECT a.* FROM assets a {join} ORDER BY asset_name")
            data["deals"] = rows(cur, f"SELECT d.* FROM deals d {join} ORDER BY deal_date")
            return data
    finally:
        conn.close()


def quarter(data: dict, year: int, q: int) -> dict:
    for row in data["quarters"]:
        if row["fiscal_year"] == year and row["quarter"] == q:
            return row
    raise SystemExit(f"{data['company_code']} has no quarterly row for Q{q} {year}.")


def year(data: dict, fy: int) -> dict:
    for row in data["financials"]:
        if row["fiscal_year"] == fy:
            return row
    raise SystemExit(f"{data['company_code']} has no financials row for FY{fy}.")


def m(x: float | None, dp: int = 1) -> str:
    return "n/a" if x is None else f"USD {x:,.{dp}f}m"


def pct(x: float) -> str:
    return f"{x:+.1f}%"


def d(value) -> str:
    return value.strftime("%d %B %Y").lstrip("0") if value else "n/a"


# --------------------------------------------------------------------------- #
# PDF helpers
# --------------------------------------------------------------------------- #

def table(rows: list[list], widths: list[float] | None = None) -> Table:
    t = Table([[Paragraph(str(c), SMALL if i else ParagraphStyle("th", parent=SMALL, textColor=colors.white,
                                                                     fontName="Helvetica-Bold"))
                for c in r] for i, r in enumerate(rows)],
              colWidths=widths, hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#123b5c")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#eef3f7")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#b8c4ce")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def bullets(items: list[str]) -> list[Paragraph]:
    return [Paragraph(text, BULLET, bulletText="•") for text in items]


def build_pdf(path: Path, title: str, header: str, story: list) -> None:
    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(18 * mm, 287 * mm, header)
        canvas.drawString(18 * mm, 10 * mm, DISCLAIMER)
        canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=A4, title=title, author="Sample generator",
                            leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm)
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


# --------------------------------------------------------------------------- #
# 1. Alder Power Group: Q3 2026 quarterly report (revenue miss)
# --------------------------------------------------------------------------- #

def alder_report(path: Path) -> None:
    c = load_company("CO-048")
    q3, q2, q1 = quarter(c, 2026, 3), quarter(c, 2026, 2), quarter(c, 2026, 1)
    q3_ly = quarter(c, 2025, 3)
    fy25 = year(c, 2025)
    miss = q3["revenue_musd"] - q3["revenue_target_musd"]
    if miss >= 0:
        raise SystemExit("The database no longer shows a Q3 2026 revenue miss for Alder Power Group.")
    miss_pct = miss / q3["revenue_target_musd"] * 100
    margin = q3["ebitda_musd"] / q3["revenue_musd"] * 100
    yoy = (q3["revenue_musd"] / q3_ly["revenue_musd"] - 1) * 100
    ytd = sum(q["revenue_musd"] for q in (q1, q2, q3))
    ytd_target = sum(q["revenue_target_musd"] for q in (q1, q2, q3))
    leverage = fy25["net_debt_musd"] / fy25["ebitda_musd"]
    previous = [q for q in c["quarters"] if (q["fiscal_year"], q["quarter"]) < (2026, 3)]
    n_missed = sum(q["revenue_musd"] < q["revenue_target_musd"] for q in previous)
    earlier_misses = (
        f"This is the first quarterly revenue miss since Q{previous[0]['quarter']} {previous[0]['fiscal_year']}."
        if n_missed == 0 else f"{n_missed} of the previous {len(previous)} quarters also missed target.")

    # Invented variance bridge that adds up exactly to the database miss.
    delay = round(miss * 0.46, 2)
    spreads = round(miss * 0.33, 2)
    outage = round(miss - delay - spreads, 2)
    delayed = next((a for a in c["assets"] if a["commissioning_date"] and a["commissioning_date"].year >= 2027), None)
    delayed_name = delayed["asset_name"] if delayed else "the new Sydney storage facility"
    delayed_mw = f"{delayed['capacity_value']:,.1f} MW" if delayed else "n/a"
    delayed_cod = d(delayed["commissioning_date"]) if delayed else "2027"
    total_mw = sum(a["capacity_value"] or 0 for a in c["assets"])
    operating_mw = total_mw - (delayed["capacity_value"] if delayed else 0)

    story = [
        Paragraph("Alder Power Group", H1),
        Paragraph("Quarterly report to shareholders - Q3 2026 (quarter ended 30 September 2026)", BODY),
        Paragraph(f"Company code {c['company_code']} · Battery storage · {c['hq_city']}, {c['country']} · "
                  f"Issued 28 October 2026", SMALL),
        Spacer(1, 6),
        Paragraph("1. Highlights", H2),
        Paragraph(
            f"Revenue for Q3 2026 was <b>{m(q3['revenue_musd'], 2)}</b>, <b>{m(abs(miss), 2)} ({miss_pct:.1f}%) below</b> "
            f"the budgeted target of {m(q3['revenue_target_musd'], 2)}. {earlier_misses} Revenue still grew {yoy:.1f}% on Q3 2025 ({m(q3_ly['revenue_musd'], 2)}).", BODY),
        Paragraph(
            f"EBITDA was {m(q3['ebitda_musd'], 2)}, an EBITDA margin of {margin:.1f}%. Year to date, revenue of "
            f"{m(ytd, 2)} is {m(ytd - ytd_target, 2)} {'ahead of' if ytd >= ytd_target else 'behind'} the year-to-date "
            f"target of {m(ytd_target, 2)}, because Q1 and Q2 were ahead of budget.", BODY),
        table([
            ["Metric", "Q3 2026 actual", "Q3 2026 target", "Variance", "Q3 2025 actual"],
            ["Revenue", m(q3["revenue_musd"], 2), m(q3["revenue_target_musd"], 2),
             f"{m(miss, 2)} ({miss_pct:.1f}%)", m(q3_ly["revenue_musd"], 2)],
            ["EBITDA", m(q3["ebitda_musd"], 2), "-", "-", m(q3_ly["ebitda_musd"], 2)],
            ["EBITDA margin", f"{margin:.1f}%", "-", "-", f"{q3_ly['ebitda_musd'] / q3_ly['revenue_musd'] * 100:.1f}%"],
        ], [32 * mm, 33 * mm, 33 * mm, 38 * mm, 33 * mm]),
        Paragraph("2. Quarterly revenue against target, 2026", H2),
        table([["Quarter", "Revenue", "Target", "Variance", "Result"]] + [
            [f"Q{q['quarter']} 2026", m(q["revenue_musd"], 2), m(q["revenue_target_musd"], 2),
             m(q["revenue_musd"] - q["revenue_target_musd"], 2),
             "Beat" if q["revenue_musd"] >= q["revenue_target_musd"] else "Missed"] for q in (q1, q2, q3)
        ] + [["YTD 2026", m(ytd, 2), m(ytd_target, 2), m(ytd - ytd_target, 2), ""]],
            [30 * mm, 34 * mm, 34 * mm, 34 * mm, 30 * mm]),
        PageBreak(),
        Paragraph("3. Why revenue missed the target", H2),
        Paragraph(
            f"The Q3 2026 budget was set in late 2025 and assumed a large step-up in revenue from the third quarter. "
            f"The shortfall of {m(abs(miss), 2)} breaks down into three causes:", BODY),
        table([
            ["Driver", "Impact on Q3 revenue", "Share of miss", "Explanation"],
            [f"Delay of {delayed_name}", m(delay, 2), f"{delay / miss * 100:.0f}%",
             f"The budget assumed the {delayed_mw} facility would start earning grid-services revenue in July 2026. "
             f"Transformer delivery slipped and commissioning is now expected on {delayed_cod}."],
            ["Lower wholesale price spreads", m(spreads, 2), f"{spreads / miss * 100:.0f}%",
             "A mild winter and high solar output narrowed the daily gap between peak and off-peak prices, "
             "reducing arbitrage revenue per MWh cycled."],
            ["Unplanned outage", m(outage, 2), f"{outage / miss * 100:.0f}%",
             "An inverter fault took one facility offline for 11 days in August 2026. Insurance recovery "
             "has been claimed but is not yet recognised."],
            ["Total", m(miss, 2), "100%", "Ties to the reported Q3 variance."],
        ], [42 * mm, 30 * mm, 22 * mm, 80 * mm]),
        Spacer(1, 6),
        Paragraph(
            "Management considers the delay and the outage to be timing issues. The lower price spreads are partly "
            "structural and have been reflected in the revised outlook below.", BODY),
        Paragraph("4. Portfolio of assets", H2),
        Paragraph(f"Alder owns {len(c['assets'])} battery storage facilities in {c['hq_city']} with a combined "
                  f"capacity of {total_mw:,.1f} MW, of which {operating_mw:,.1f} MW is operating.", BODY),
        table([["Asset", "Capacity", "Commissioning", "Book value"]] + [
            [a["asset_name"], f"{a['capacity_value']:,.1f} {a['capacity_unit']}" if a["capacity_value"] else "n/a",
             d(a["commissioning_date"]), m(a["book_value_musd"])] for a in c["assets"]
        ], [62 * mm, 30 * mm, 38 * mm, 34 * mm]),
        PageBreak(),
        Paragraph("5. Balance sheet and funding", H2),
        Paragraph(
            f"At the end of FY2025 net debt was {m(fy25['net_debt_musd'])} against FY2025 EBITDA of "
            f"{m(fy25['ebitda_musd'])}, a leverage ratio of {leverage:.1f}x. FY2025 revenue was {m(fy25['revenue_musd'])} "
            f"and capex {m(fy25['capex_musd'])}. Covenant headroom remains adequate, but the delay of {delayed_name} "
            f"pushes back the deleveraging the 2026 plan assumed.", BODY),
        Paragraph("6. Actions and outlook", H2),
        *bullets([
            "A second transformer supplier has been contracted; weekly commissioning reports now go to the board.",
            "Merchant exposure is being reduced: two new fixed-price grid-services contracts start in Q1 2027.",
            f"Management expects Q4 2026 revenue of about {m(q3_ly['revenue_musd'] * 0.78, 1)} (management guidance, "
            f"unaudited), which would leave full-year 2026 revenue slightly below budget.",
            "The FY2027 budget will use lower price-spread assumptions and a later start date for new capacity.",
        ]),
        Paragraph("7. Shareholders", H2),
        Paragraph(f"The portfolio fund holds {c['ownership_pct']:.1f}% of Alder Power Group, acquired on "
                  f"{d(c['acquisition_date'])}.", BODY),
        Spacer(1, 10),
        Paragraph("Approved by the board of Alder Power Group. Figures are unaudited.", SMALL),
    ]
    build_pdf(path, "Alder Power Group - Q3 2026 quarterly report", "Alder Power Group · Q3 2026 quarterly report", story)


# --------------------------------------------------------------------------- #
# 2. Ironbridge Digital Partners: investment memo (data centers)
# --------------------------------------------------------------------------- #

def ironbridge_memo(path: Path) -> None:
    c = load_company("CO-004")
    fys = [f for f in c["financials"] if f["fiscal_year"] >= 2023]
    fy25 = year(c, 2025)
    q26 = [quarter(c, 2026, q) for q in (1, 2, 3)]
    ytd = sum(q["revenue_musd"] for q in q26)
    ytd_target = sum(q["revenue_target_musd"] for q in q26)
    ytd_ly = sum(quarter(c, 2025, q)["revenue_musd"] for q in (1, 2, 3))
    acq = next(x for x in c["deals"] if x["deal_type"] == "acquisition")
    terminated = next((x for x in c["deals"] if x["status"] == "terminated"), None)
    known_mw = sum(a["capacity_value"] or 0 for a in c["assets"])
    book = sum(a["book_value_musd"] or 0 for a in c["assets"])
    missing_cap = [a["asset_name"] for a in c["assets"] if a["capacity_value"] is None]
    leverage = fy25["net_debt_musd"] / fy25["ebitda_musd"]
    proposal = 310.0  # invented

    story = [
        Paragraph("Investment Committee memo", H1),
        Paragraph("<b>Ironbridge Digital Partners - proposed follow-on investment for Paris Campus V</b>", BODY),
        Paragraph(f"Company code {c['company_code']} · Data centers (hyperscale) · {c['hq_city']}, {c['country']} · "
                  f"Prepared by the digital infrastructure deal team · 20 October 2026 · Confidential", SMALL),
        Paragraph("1. Recommendation", H2),
        Paragraph(
            f"We recommend approving a follow-on equity investment of up to <b>{m(proposal)}</b> to fund a 60 MW "
            f"fifth campus in Paris, pre-let at 70% to an existing hyperscale tenant. The investment would be made "
            f"pro rata to our {c['ownership_pct']:.1f}% stake. This proposal replaces the follow-on of "
            f"{m(terminated['deal_value_musd']) if terminated else 'n/a'} with "
            f"{terminated['counterparty'] if terminated else 'n/a'}, which was terminated on "
            f"{d(terminated['deal_date']) if terminated else 'n/a'} after the co-investor withdrew.", BODY),
        Paragraph("2. Background", H2),
        Paragraph(
            f"We acquired {c['ownership_pct']:.1f}% of Ironbridge on {d(acq['deal_date'])} for {m(acq['deal_value_musd'])} "
            f"from {acq['counterparty']}. The company operates {len(c['assets'])} data center campuses in Paris with "
            f"{known_mw:,.1f} MW of recorded IT capacity and a combined book value of {m(book)}.", BODY),
        table([["Campus", "Capacity", "In service since", "Book value"]] + [
            [a["asset_name"], f"{a['capacity_value']:,.1f} MW" if a["capacity_value"] else "not recorded",
             d(a["commissioning_date"]), m(a["book_value_musd"])] for a in c["assets"]
        ], [62 * mm, 30 * mm, 38 * mm, 34 * mm]),
        Paragraph("3. Financial performance", H2),
        table([["Fiscal year", "Revenue", "EBITDA", "EBITDA margin", "Net debt", "Capex"]] + [
            [f"FY{f['fiscal_year']}", m(f["revenue_musd"]), m(f["ebitda_musd"]),
             f"{f['ebitda_musd'] / f['revenue_musd'] * 100:.1f}%", m(f["net_debt_musd"]), m(f["capex_musd"])]
            for f in fys
        ], [24 * mm, 28 * mm, 28 * mm, 28 * mm, 30 * mm, 28 * mm]),
        Spacer(1, 4),
        Paragraph(
            f"Revenue for the first nine months of 2026 was {m(ytd, 2)}, {pct((ytd / ytd_target - 1) * 100)} versus the "
            f"budget of {m(ytd_target, 2)} and {pct((ytd / ytd_ly - 1) * 100)} on the same period of 2025. All three "
            f"2026 quarters beat their revenue target.", BODY),
        PageBreak(),
        Paragraph("4. Investment thesis", H2),
        *bullets([
            "Paris hyperscale vacancy is low and the anchor tenant has asked for 40 MW of additional capacity by 2028.",
            "A pre-let of 70% before construction limits leasing risk; the remaining 30% targets existing customers.",
            "The site has a grid connection agreement for 75 MW, removing the main bottleneck for new capacity in the region.",
            "Base-case unlevered return of 9.5-10.5% (deal team model, not audited).",
        ]),
        Paragraph("5. Key risks", H2),
        *bullets([
            f"<b>Leverage.</b> FY2025 net debt of {m(fy25['net_debt_musd'])} is {leverage:.1f}x FY2025 EBITDA of "
            f"{m(fy25['ebitda_musd'])}, up from {m(year(c, 2024)['net_debt_musd'])} at the end of FY2024. The follow-on "
            f"must be equity-funded; no new debt is proposed until leverage falls.",
            f"<b>Margin.</b> The EBITDA margin was {fy25['ebitda_musd'] / fy25['revenue_musd'] * 100:.1f}% in FY2025, "
            f"below hyperscale peers, mainly because of power costs that are passed through only with a lag.",
            "<b>Construction.</b> Cost overruns on electrical equipment of 10-15% are possible; the budget holds an 8% contingency.",
            "<b>Execution history.</b> The 2025 follow-on fell through; a second failure would damage tenant confidence.",
        ]),
        Paragraph("6. Data gaps", H2),
        Paragraph(
            "Capacity is not recorded for: " + (", ".join(missing_cap) if missing_cap else "none") +
            ". The deal team has asked management to confirm it before final approval.", BODY),
        Paragraph("7. Decision requested", H2),
        Paragraph(f"Approval of a follow-on equity commitment of up to {m(proposal)}, subject to a signed pre-let "
                  f"agreement and confirmatory technical due diligence.", BODY),
    ]
    build_pdf(path, "Ironbridge Digital Partners - investment memo", "Ironbridge Digital Partners · IC memo", story)


# --------------------------------------------------------------------------- #
# 3. Summit Energy Partners: FY2027 quarterly budget (Excel)
# --------------------------------------------------------------------------- #

def summit_budget(path: Path) -> None:
    c = load_company("CO-044")
    q25 = {q: quarter(c, 2025, q) for q in (1, 2, 3, 4)}
    q26 = {q: quarter(c, 2026, q) for q in (1, 2, 3)}
    growth = 0.035          # invented budget assumptions
    margin = 0.58
    capex_split = [0.20, 0.30, 0.30, 0.20]
    capex_total = 160.0
    mw = sum(a["capacity_value"] or 0 for a in c["assets"])

    wb = Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="123B5C")

    def header(ws, row):
        ws.append(row)
        for cell in ws[ws.max_row]:
            cell.font, cell.fill = head, fill
            cell.alignment = Alignment(horizontal="center", wrap_text=True)

    # Sheet 1: FY2027 budget by quarter (values, not formulas, so every reader sees numbers)
    ws = wb.active
    ws.title = "Quarterly Budget"
    ws.append(["Summit Energy Partners - FY2027 budget by quarter (USD m)"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"Company code {c['company_code']} · Solar · {c['hq_city']}, {c['country']} · "
               f"Draft for board approval, 6 November 2026 · {DISCLAIMER}"])
    ws.append([])
    header(ws, ["Line item", "Q1 2027", "Q2 2027", "Q3 2027", "Q4 2027", "FY2027"])
    revenue = [round(q25[q]["revenue_musd"] * (1 + growth) ** 2, 2) for q in (1, 2, 3, 4)]
    ebitda = [round(r * margin, 2) for r in revenue]
    opex = [round(r - e, 2) for r, e in zip(revenue, ebitda)]
    capex = [round(capex_total * s, 2) for s in capex_split]
    for label, vals in (("Revenue", revenue), ("Operating costs", opex), ("EBITDA", ebitda), ("Capex", capex)):
        ws.append([label, *vals, round(sum(vals), 2)])
    ws.append(["EBITDA margin", *[f"{margin * 100:.1f}%"] * 4, f"{margin * 100:.1f}%"])
    ws.append(["Revenue vs same quarter FY2025", *[f"{((1 + growth) ** 2 - 1) * 100:+.1f}%"] * 4,
               f"{(sum(revenue) / year(c, 2025)['revenue_musd'] - 1) * 100:+.1f}%"])

    # Sheet 2: 2026 actuals vs target, from the database
    ws2 = wb.create_sheet("2026 Actual vs Target")
    ws2.append(["FY2026 revenue: actual vs budget target (USD m). Q4 2026 is a forecast."])
    ws2["A1"].font = Font(bold=True, size=12)
    ws2.append([])
    header(ws2, ["Quarter", "Revenue actual", "Revenue target", "Variance", "Variance %", "EBITDA actual", "Status"])
    for q, row in q26.items():
        var = row["revenue_musd"] - row["revenue_target_musd"]
        ws2.append([f"Q{q} 2026", row["revenue_musd"], row["revenue_target_musd"], round(var, 2),
                    f"{var / row['revenue_target_musd'] * 100:+.1f}%", row["ebitda_musd"],
                    "Beat" if var >= 0 else "Missed"])
    q4_forecast = round(q25[4]["revenue_musd"] * 1.02, 2)
    ws2.append(["Q4 2026 (forecast)", q4_forecast, "", "", "", "", "Forecast"])
    ytd = sum(r["revenue_musd"] for r in q26.values())
    ytd_t = sum(r["revenue_target_musd"] for r in q26.values())
    ws2.append(["YTD Q1-Q3 2026", round(ytd, 2), round(ytd_t, 2), round(ytd - ytd_t, 2),
                f"{(ytd / ytd_t - 1) * 100:+.1f}%", round(sum(r["ebitda_musd"] for r in q26.values()), 2), ""])
    ws2.append([])
    ws2.append(["Comment", "Q3 2026 missed target because of curtailment at Lyon Solar farm during grid works "
                           "(July-August) and lower capture prices."])

    # Sheet 3: assumptions
    ws3 = wb.create_sheet("Assumptions")
    header(ws3, ["Assumption", "Value", "Note"])
    for row in (
        ["Revenue growth vs FY2025, per year", f"{growth * 100:.1f}%", "Applied for two years to each FY2025 quarter"],
        ["EBITDA margin", f"{margin * 100:.1f}%", f"FY2025 actual: {year(c, 2025)['ebitda_musd'] / year(c, 2025)['revenue_musd'] * 100:.1f}%"],
        ["Capex FY2027", capex_total, "Repowering of inverters at Lyon Solar farm; split 20/30/30/20 by quarter"],
        ["Installed capacity (MW)", round(mw, 1), " + ".join(f"{a['asset_name']} {a['capacity_value']:,.1f} MW" for a in c["assets"])],
        ["Average realised price (USD/MWh)", 67.5, "Blend of PPA and merchant sales"],
        ["FY2025 revenue (USD m)", year(c, 2025)["revenue_musd"], "Audited, from the annual accounts"],
        ["FY2025 net debt (USD m)", year(c, 2025)["net_debt_musd"], "No refinancing planned in FY2027"],
    ):
        ws3.append(row)

    for sheet in (ws, ws2, ws3):
        sheet.column_dimensions["A"].width = 34
        for col in "BCDEFG":
            sheet.column_dimensions[col].width = 16
    ws3.column_dimensions["C"].width = 70
    wb.save(path)


# --------------------------------------------------------------------------- #
# 4. Pinnacle Utilities Holdings: board update (water)
# --------------------------------------------------------------------------- #

def pinnacle_board_update(path: Path) -> None:
    c = load_company("CO-034")
    q26 = [quarter(c, 2026, q) for q in (1, 2, 3)]
    q3 = q26[2]
    ytd = sum(q["revenue_musd"] for q in q26)
    ytd_t = sum(q["revenue_target_musd"] for q in q26)
    ytd_e = sum(q["ebitda_musd"] for q in q26)
    fy25, fy24 = year(c, 2025), year(c, 2024)
    plant = c["assets"][0]

    story = [
        Paragraph("Pinnacle Utilities Holdings", H1),
        Paragraph("Board update - Q3 2026 · For the board meeting of 12 November 2026", BODY),
        Paragraph(f"Company code {c['company_code']} · Water treatment · {c['hq_city']}, {c['country']} · "
                  f"Prepared by the CEO and CFO", SMALL),
        Paragraph("1. Summary for the board", H2),
        *bullets([
            f"Q3 2026 revenue was {m(q3['revenue_musd'], 2)}, {m(q3['revenue_musd'] - q3['revenue_target_musd'], 2)} "
            f"above the target of {m(q3['revenue_target_musd'], 2)}: the first quarter on or above target in 2026.",
            f"Q1 and Q2 2026 both missed target, so year-to-date revenue of {m(ytd, 2)} is "
            f"{m(ytd - ytd_t, 2)} ({(ytd / ytd_t - 1) * 100:+.1f}%) against the YTD target of {m(ytd_t, 2)}.",
            f"YTD EBITDA is {m(ytd_e, 2)}, an EBITDA margin of {ytd_e / ytd * 100:.1f}%.",
            f"The tariff review with the Hamburg regulator is the main item for decision (section 4).",
        ]),
        Paragraph("2. Quarterly results 2026", H2),
        table([["Quarter", "Revenue", "Target", "Variance", "EBITDA"]] + [
            [f"Q{q['quarter']} 2026", m(q["revenue_musd"], 2), m(q["revenue_target_musd"], 2),
             m(q["revenue_musd"] - q["revenue_target_musd"], 2), m(q["ebitda_musd"], 2)] for q in q26
        ] + [["YTD 2026", m(ytd, 2), m(ytd_t, 2), m(ytd - ytd_t, 2), m(ytd_e, 2)]],
            [30 * mm, 34 * mm, 34 * mm, 34 * mm, 34 * mm]),
        Spacer(1, 4),
        Paragraph(
            "The first-half misses came from lower industrial water volumes after two large customers cut production. "
            "Volumes recovered in Q3, helped by a dry summer and a new supply contract with a food processing plant.",
            BODY),
        Paragraph("3. Operations and balance sheet", H2),
        Paragraph(
            f"{plant['asset_name']} ({plant['capacity_value']:,.0f} {plant['capacity_unit']}, in service since "
            f"{d(plant['commissioning_date'])}, book value {m(plant['book_value_musd'])}) ran at an average of 81% of "
            f"capacity in Q3, with no compliance breaches. Capex rose from {m(fy24['capex_musd'])} in FY2024 to "
            f"{m(fy25['capex_musd'])} in FY2025 for the membrane filtration upgrade, which is now 70% complete. "
            f"Net debt was {m(fy25['net_debt_musd'])} at the end of FY2025 "
            f"({fy25['net_debt_musd'] / fy25['ebitda_musd']:.1f}x FY2025 EBITDA of {m(fy25['ebitda_musd'])}).", BODY),
        Paragraph("4. Decisions requested", H2),
        *bullets([
            "Approve the submission of the 2027-2031 tariff proposal (average real increase of 2.4% per year).",
            "Approve a further USD 45m of capex to complete the membrane filtration upgrade in 2027.",
            "Note the plan to refinance the 2028 bank loan early if rates stay at current levels.",
        ]),
        Paragraph("5. Risks to watch", H2),
        *bullets([
            "Industrial demand: the two customers that cut volumes have not confirmed 2027 orders.",
            "Regulation: the tariff decision is expected in Q2 2027; a lower outcome would cut FY2028 revenue.",
            "Energy costs: electricity is about 18% of operating costs and is hedged only to mid-2027.",
        ]),
        Spacer(1, 8),
        Paragraph(f"Shareholding: the portfolio fund owns {c['ownership_pct']:.0f}% of Pinnacle Utilities Holdings "
                  f"since {d(c['acquisition_date'])}.", SMALL),
    ]
    build_pdf(path, "Pinnacle Utilities Holdings - board update Q3 2026",
              "Pinnacle Utilities Holdings · Board update Q3 2026", story)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for name, make in (
        ("Alder_Q3_2026.pdf", alder_report),
        ("Ironbridge_Investment_Memo.pdf", ironbridge_memo),
        ("Summit_Energy_Budget_FY2027.xlsx", summit_budget),
        ("Pinnacle_Board_Update_Q3_2026.pdf", pinnacle_board_update),
    ):
        path = OUT_DIR / name
        make(path)
        print(f"Wrote {path.relative_to(OUT_DIR.parent.parent)} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
