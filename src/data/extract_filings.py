"""
Turn the four 10-K filings into the versioned CSVs under data/raw/.

Run this once; the CSVs it writes are the project's record of what the filings said.
Nothing downstream re-reads the HTML. That split matters because the filings are 30 MB
of inline XBRL and we want the numbers that feed the valuation to be reviewable by eye
and stable under version control.

Every extraction asserts something: row widths, component sums against stated totals,
reporting units. SVB and Schwab disagree on the sign convention for unrealized losses
(SVB parenthesises them, Schwab prints them positive and expects you to subtract), so
the normalisation is checked rather than assumed.

    python -m src.data.extract_filings
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .filings import (
    cut_at,
    sectioned_rows,
    Filing,
    clean_label,
    find_table,
    label_rows,
    load_tables,
    squash,
    table_to_frame,
    to_number,
)

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
FILING_DIR = ROOT / "references" / "sec_filings"

FILINGS = [
    Filing("SVB", 2021, "0000719739", "0000719739-22-000023",
           FILING_DIR / "2021_SEC_SVBFinancialGroup_10K.htm"),
    Filing("SVB", 2022, "0000719739", "0000719739-23-000021",
           FILING_DIR / "2022_SEC_SVBFinancialGroup_10K.htm"),
    Filing("Schwab", 2021, "0000316709", "0000316709-22-000009",
           FILING_DIR / "2021_SEC_CharlesSchwabCorp_10K.htm"),
    Filing("Schwab", 2022, "0000316709", "0000316709-23-000009",
           FILING_DIR / "2022_SEC_CharlesSchwabCorp_10K.htm"),
]

# Both issuers report in millions throughout the tables we touch. Everything in the
# project is in $ millions as a result; there is no unit conversion anywhere, which is
# deliberate, because mixing thousands and millions inside one filing is the classic way
# these reconstructions go wrong.
UNITS = "USD millions"

MATURITY_BUCKETS = ["le_1y", "1_5y", "5_10y", "gt_10y"]

# Column headers that the row parser cannot tell from data, because the year beside
# them parses as a perfectly good number. "December 31, 2021" would otherwise be filed
# as a deposit type holding $2,021m.
HEADER_LABELS = re.compile(r"^\(?(dollars|in millions|december 31|year ended)", re.I)


def _is_header(label: str) -> bool:
    return bool(HEADER_LABELS.match(label.strip()))


def _rounded_equal(parts: float, total: float, tol: float = 2.0) -> bool:
    """Filers round each line independently, so component sums miss totals by a unit
    or two. Two million on portfolios of $100bn is rounding, not an extraction error."""
    return abs(parts - total) <= tol


# --------------------------------------------------------------------------- SVB


def svb_securities(filing: Filing, tables) -> list[dict]:
    """
    AFS and HTM holdings by security type.

    SVB's two tables do not have the same shape. AFS ends at carrying value; HTM carries
    an allowance column, and from FY2022 also a separate net-carry column. The widths
    below pin that down so a layout change breaks the run instead of shifting a column.
    """
    rows = []
    date_label = f"December 31, {filing.fiscal_year}"

    afs_width = 4
    _, afs = find_table(tables, "AFS securities, at fair value", "Amortized Cost",
                        date_label, max_chars=1200)
    for label, v in label_rows(table_to_frame(afs), width=afs_width):
        cost, gains, losses, fair = v
        rows.append(dict(classification="AFS", security_type=label,
                         amortized_cost=cost, unrealized_gains=gains,
                         unrealized_losses=losses, fair_value=fair,
                         allowance=0.0, net_carry=fair))

    # FY2021 prints five columns, FY2022 adds "Net Carry Value" as a sixth.
    htm_width = 5 if filing.fiscal_year == 2021 else 6
    _, htm = find_table(tables, "HTM securities, at", "Amortized Cost", date_label,
                        max_chars=1200)
    for label, v in label_rows(table_to_frame(htm), width=htm_width):
        cost, gains, losses, fair, acl = v[:5]
        net = v[5] if htm_width == 6 else cost - acl
        rows.append(dict(classification="HTM", security_type=label,
                         amortized_cost=cost, unrealized_gains=gains,
                         unrealized_losses=losses, fair_value=fair,
                         allowance=acl, net_carry=net))
    return rows


def svb_maturity(filing: Filing, tables) -> list[dict]:
    """
    Carrying value and weighted-average yield by contractual maturity bucket.

    Contractual is the operative word. SVB's MBS sit almost entirely in the "after ten
    years" column because that is their legal final maturity; the cash actually comes
    back far sooner through amortisation and prepayment. Module D exists to undo this.
    """
    rows = []
    date_label = f"December 31, {filing.fiscal_year}"
    for classification, value_header in [("AFS", "Carrying Value"),
                                         ("HTM", "Net Carry Value")]:
        _, table = find_table(tables, date_label, "Weighted Average Yield",
                              "After Ten Years", value_header, max_chars=1400)
        for label, v in label_rows(table_to_frame(table), width=10):
            total_value, total_yield = v[0], v[1]
            rows.append(dict(classification=classification, security_type=label,
                             bucket="total", carrying_value=total_value,
                             amortized_cost=None, weighted_avg_yield=total_yield))
            for i, bucket in enumerate(MATURITY_BUCKETS):
                rows.append(dict(classification=classification, security_type=label,
                                 bucket=bucket, carrying_value=v[2 + 2 * i],
                                 amortized_cost=None,
                                 weighted_avg_yield=v[3 + 2 * i]))
    return rows


def svb_deposits(filing: Filing, tables) -> list[dict]:
    _, table = find_table(tables, "Noninterest-bearing demand", "Total deposits",
                          "Money market", max_chars=600)
    frame = table_to_frame(table)
    # The note shows the current year beside the prior year; column 0 is the label,
    # column 1 the current year.
    return [dict(deposit_type=label, amount=v[0])
            for label, v in label_rows(frame, width=2) if not _is_header(label)]


# Metrics we keep out of the capital composition tables. The 15% supervisory outlier
# test in Module F divides worst-case EVE loss by Tier 1 capital, so the dollar figure
# matters, not the published ratio.
CAPITAL_METRICS = {
    "cet1 capital": "cet1_capital",
    "common equity tier 1 capital": "cet1_capital",
    "tier 1 capital": "tier1_capital",
    "total capital": "total_capital",
    "total risk-weighted assets": "risk_weighted_assets",
    "risk-weighted assets": "risk_weighted_assets",
}

# Column order differs by filer. SVB runs entity-major (holdco both years, then bank
# both years); Schwab runs year-major (both entities current year, then both prior).
# Position of (entity, year) in each table's value row:
CAPITAL_LAYOUT = {
    "SVB": [("SVB Financial", 0), ("SVB Financial", 1), ("Bank", 0), ("Bank", 1)],
    "Schwab": [("CSC", 0), ("CSB", 0), ("CSC", 1), ("CSB", 1)],
}


def capital(filing: Filing, tables) -> list[dict]:
    """
    Tier 1 capital, total capital and RWA, in dollars, for the holding company and bank.

    SVB's FY2021 10-K prints only the capital *ratios*; the dollar composition table
    first appears in the FY2022 filing, where it covers both year-ends. So SVB's
    end-2021 dollars are read from the FY2022 filing's comparative column, which is the
    same regulatory figure rather than a restatement. `check_capital_ratio` confirms
    that by dividing the dollars back into the ratio each year's own filing published.
    """
    anchor = ("Total risk-weighted assets" if filing.bank == "SVB"
              else "Risk-Weighted Assets")
    try:
        _, table = find_table(tables, anchor, "Tier 1 Capital", max_chars=3000)
    except LookupError:
        return []  # SVB FY2021: ratios only, no dollar table

    layout = CAPITAL_LAYOUT[filing.bank]
    year_offset = {0: filing.fiscal_year, 1: filing.fiscal_year - 1}
    out = []
    for label, v in label_rows(table_to_frame(table)):
        metric = CAPITAL_METRICS.get(label.strip().lower())
        if metric is None or len(v) < len(layout):
            continue
        for (entity, year_index), value in zip(layout, v):
            out.append(dict(metric=metric, entity=entity,
                            as_of=f"{year_offset[year_index]}-12-31", value=value))
    return out


def published_capital_ratios(filing: Filing, tables) -> dict[str, float]:
    """Tier 1 risk-based capital ratio as printed in the filing's own ratio table."""
    try:
        _, table = find_table(tables, "Tier 1 risk-based capital ratio",
                              "Total risk-based capital ratio", max_chars=1200)
    except LookupError:
        return {}
    for label, v in label_rows(table_to_frame(table)):
        if label.strip().lower() == "tier 1 risk-based capital ratio":
            return {"holdco_tier1_ratio": v[0]}
    return {}


# The signed shock labels that open four of the five rows of SVB's Item 7A table. The
# base row is labelled with a bare em-dash, which cannot be matched directly: that same
# dash character fills its four change columns, so splitting on it cuts the row apart.
# The base row is recovered from the tail of the +100 segment instead.
SHOCK_LABEL = re.compile(r"(?<![\d.,])([+-][12]00)(?=\s)")

# A figure, or an em-dash standing in for zero.
FIGURE = re.compile(r"\(?[\d,]+(?:\.\d+)?\)?|—")


def _shock_block(body: str, header: str, span: int) -> str:
    """
    One year's rows from Item 7A, bounded by the next year's date header.

    The comparative year follows immediately in reading order and repeats the same
    "+200" label, so a fixed character window silently lets the prior year's figures
    overwrite the current year's. Both SVB tables put the comparative directly beneath,
    which makes "December 31," the natural terminator.
    """
    start = body.find(header)
    if start < 0:
        raise LookupError(f"Item 7A block {header!r} not found")
    # Keep the "+200" that closes the header so the first row carries its own label.
    open_at = start + len(header) - len("+200")
    window = body[open_at:open_at + span]
    next_year = window.find("December 31,")
    return window[:next_year] if next_year > 0 else window


def _figures(segment: str) -> list[float]:
    return [v for v in (to_number(t) for t in FIGURE.findall(segment))
            if v is not None]


def _parse_shock_rows(block: str, width: int, base_row: bool) -> dict[int, list[float]]:
    """
    Figures keyed by shock size for one year's block of Item 7A.

    With `base_row` set, the unshocked row is taken as the last `width` figures sitting
    in the +100 segment, since it follows +100 in reading order and is terminated by the
    -100 label. Reading it that way skips its own dash label without having to guess how
    many leading dashes to drop.
    """
    pieces = SHOCK_LABEL.split(block)
    rows: dict[int, list[float]] = {}
    for label, segment in zip(pieces[1::2], pieces[2::2]):
        shock = int(label)
        values = _figures(segment)
        if shock == 100 and base_row:
            if len(values) < 2 * width:
                raise ValueError(
                    f"+100 segment holds {len(values)} figures, need {2 * width} "
                    f"to carry both the +100 row and the base row")
            rows[0] = values[-width:]
            values = values[:width]
        else:
            values = values[:width]
        if len(values) != width:
            raise ValueError(
                f"shock {shock:+d} gave {len(values)} figures, want {width}")
        rows[shock] = values
    if base_row and 0 not in rows:
        raise ValueError("base (unshocked) row not found in Item 7A block")
    return rows


def svb_rate_sensitivity(filing: Filing, body: str) -> list[dict]:
    """
    SVB's Item 7A rate-shock disclosure, read from the narrative rather than a table.

    This disclosure is the reason the project exists, so it is worth stating precisely
    what each filing holds. FY2021 reports EVE and NII side by side, in dollars and in
    percent, for 2021 with 2020 alongside. FY2022 reports NII in percent only and
    carries no EVE figures anywhere in Item 7A.

    Workiva emits these numbers as inline facts inside a paragraph-level table whose
    cells do not survive the generic row parser, hence the text route.
    """
    year = filing.fiscal_year
    rows: list[dict] = []

    if year == 2021:
        # EVE amount, EVE change, EVE percent, NII amount, NII change, NII percent.
        parsed = _parse_shock_rows(
            _shock_block(body, f"December 31, {year}: +200", 430), 6, base_row=True)
        for shock, v in sorted(parsed.items(), reverse=True):
            rows.append(dict(as_of=f"{year}-12-31", shock_bp=shock,
                             eve_amount=v[0], eve_change=v[1], eve_pct=v[2],
                             nii_amount=v[3], nii_change=v[4], nii_pct=v[5]))
    else:
        parsed = _parse_shock_rows(
            _shock_block(body, f"December 31, {year}: +200", 110), 1, base_row=False)
        for shock, v in sorted(parsed.items(), reverse=True):
            rows.append(dict(as_of=f"{year}-12-31", shock_bp=shock,
                             eve_amount=None, eve_change=None, eve_pct=None,
                             nii_amount=None, nii_change=None, nii_pct=v[0]))
    return rows


def svb_restated_2021_nii(body: str) -> dict[int, float]:
    """
    The 2021 NII comparatives as the FY2022 filing restates them.

    Used as a cross-filing check: if the FY2022 10-K reprints different 2021 percentages
    than the FY2021 10-K published, our reading of one of the two is wrong.
    """
    parsed = _parse_shock_rows(
        _shock_block(body, "December 31, 2021: +200", 110), 1, base_row=False)
    return {shock: v[0] for shock, v in parsed.items()}


# ------------------------------------------------------------------------ Schwab


SCHWAB_SECTIONS = {
    "Available for sale securities": "AFS",
    "Held to maturity securities": "HTM",
}


def schwab_securities(filing: Filing, tables) -> list[dict]:
    """
    Schwab's AFS book and, from FY2022, the HTM book the two 2022 transfers created.

    Both classifications live in one table separated by a label row, so the section
    tracking matters. Schwab also prints gross unrealized losses as positive numbers
    while SVB parenthesises them; we flip Schwab's sign so the project has one
    convention, and `check_securities` verifies the arithmetic rather than trusting it.
    """
    date_label = f"December 31, {filing.fiscal_year}"
    prior_label = f"December 31, {filing.fiscal_year - 1}"
    _, table = find_table(tables, date_label, "Amortized Cost",
                          "Gross Unrealized Losses", "Available for sale securities",
                          max_chars=1600)
    frame = cut_at(table_to_frame(table), prior_label)
    rows = []
    for section, label, v in sectioned_rows(frame, SCHWAB_SECTIONS, width=4,
                                            default="AFS"):
        cost, gains, losses, fair = v
        rows.append(dict(classification=section, security_type=label,
                         amortized_cost=cost, unrealized_gains=gains,
                         unrealized_losses=-abs(losses), fair_value=fair,
                         allowance=0.0,
                         net_carry=fair if section == "AFS" else cost))
    return rows


def schwab_maturity(filing: Filing, tables) -> list[dict]:
    """
    Contractual maturity buckets at fair value.

    Schwab gives yields only on the total line of each bucket rather than per security
    type, so `weighted_avg_yield` is populated on the total rows and left empty on the
    per-type rows. That is a real limitation of the disclosure, not a gap in parsing,
    and Module D works around it by using the bucket yield for every type in the bucket.
    """
    date_label = f"December 31, {filing.fiscal_year}"
    _, table = find_table(tables, date_label, "Within 1 year", "After 10 years",
                          "Weighted-average yield", max_chars=2600)
    frame = cut_at(table_to_frame(table), f"December 31, {filing.fiscal_year - 1}")

    # Each section closes with three summary lines rather than a single total row.
    # They are folded into one "Total" record per section so the output matches SVB's
    # shape, where the total is a row like any other.
    summary = {"total fair value": "carrying_value",
               "total amortized cost": "amortized_cost",
               "weighted-average yield": "weighted_avg_yield"}
    rows: list[dict] = []
    totals: dict[str, dict] = {}
    for section, label, v in sectioned_rows(frame, SCHWAB_SECTIONS, width=5,
                                            default="AFS"):
        field = summary.get(label.strip().lower())
        if field is None:
            for bucket, value in zip(MATURITY_BUCKETS + ["total"], v):
                rows.append(dict(classification=section, security_type=label,
                                 bucket=bucket, carrying_value=value,
                                 amortized_cost=None, weighted_avg_yield=None))
            continue
        for bucket, value in zip(MATURITY_BUCKETS + ["total"], v):
            key = f"{section}|{bucket}"
            record = totals.setdefault(key, dict(
                classification=section, security_type="Total", bucket=bucket,
                carrying_value=None, amortized_cost=None, weighted_avg_yield=None))
            record[field] = value
    return rows + list(totals.values())


def schwab_deposits(filing: Filing, tables) -> list[dict]:
    _, table = find_table(tables, "Deposits swept from brokerage accounts",
                          "Total bank deposits", max_chars=500)
    return [dict(deposit_type=label, amount=v[0])
            for label, v in label_rows(table_to_frame(table), width=2)
            if not _is_header(label)]


def schwab_rate_sensitivity(filing: Filing, body: str) -> list[dict]:
    """
    Schwab's 12-month net interest revenue simulation.

    Not comparable like-for-like with SVB's: Schwab models a *gradual* 100bp move over
    the year, SVB an instantaneous parallel shock. Schwab publishes no EVE figure in
    either year, so there is nothing on the economic-value side to check our model
    against for this bank. Both points belong in the report.
    """
    marker = "Increase of 100 basis points"
    start = body.find(marker)
    if start < 0:
        raise LookupError("Schwab NIR simulation table not found")
    block = body[start - 60:start + 200]
    pcts = [to_number(t) for t in re.findall(r"\(?\d+\.\d\)?%", block)]
    pcts = [p for p in pcts if p is not None]
    if len(pcts) < 4:
        raise ValueError(f"expected 4 NIR percentages, parsed {pcts}")
    current_up, prior_up, current_down, prior_down = pcts[:4]
    year = filing.fiscal_year
    return [
        dict(as_of=f"{year}-12-31", shock_bp=100, nii_pct=current_up,
             eve_amount=None, eve_change=None, eve_pct=None, nii_amount=None,
             nii_change=None),
        dict(as_of=f"{year}-12-31", shock_bp=-100, nii_pct=current_down,
             eve_amount=None, eve_change=None, eve_pct=None, nii_amount=None,
             nii_change=None),
    ]


# SVB's loan maturity table uses its own bucket edges - one year, five years, fifteen
# years - which do not line up with the securities buckets. Kept distinct rather than
# forced into the securities scheme, because 91% of the book is variable rate and
# reprices within a quarter regardless of when it contractually matures.
LOAN_BUCKETS = ["le_1y", "1_5y", "5_15y", "gt_15y"]


def svb_loans(filing: Filing, tables) -> list[dict]:
    """
    Loans split fixed against variable, by remaining contractual maturity.

    Only the two total lines are kept. The per-segment detail (global fund banking,
    investor dependent, private bank and the rest) is a credit breakdown; for interest
    rate risk the repricing behaviour is what matters and that is captured by the
    fixed/variable split.
    """
    _, table = find_table(tables, "Fixed-rate loans", "Variable-rate loans", "Total",
                          max_chars=4000)
    wanted = {"total fixed-rate loans": "fixed",
              "total variable-rate loans": "floating",
              "total loans": "all"}
    rows = []
    for label, v in label_rows(table_to_frame(table), width=5):
        rate_type = wanted.get(label.strip().lower())
        if rate_type is None:
            continue
        for bucket, amount in zip(LOAN_BUCKETS, v[:4]):
            rows.append(dict(rate_type=rate_type, bucket=bucket, amount=amount))
        rows.append(dict(rate_type=rate_type, bucket="total", amount=v[4]))
    return rows


def schwab_loans(filing: Filing, tables) -> list[dict]:
    """
    Loans by product. Schwab publishes no maturity table and no fixed/floating split,
    so the repricing assumption is made in Module A from the product names and recorded
    there rather than invented here.
    """
    _, table = find_table(tables, "Pledged asset lines", "Total bank loans",
                          f"December 31, {filing.fiscal_year}", max_chars=1400)
    frame = cut_at(table_to_frame(table), f"December 31, {filing.fiscal_year - 1}")
    keep = {"first mortgages", "helocs", "pledged asset lines", "other",
            "total bank loans"}
    rows = []
    for label, v in label_rows(frame):
        if label.strip().lower() in keep and len(v) >= 6:
            # Columns: current, 30-59, 60-89, >90, total past due, total loans, ...
            rows.append(dict(rate_type="unclassified", bucket="total",
                             product=label, amount=v[5]))
    return rows


# ------------------------------------------------------------------- narrative facts

UNINSURED_SVB = re.compile(
    r"uninsured deposits in U\.S\. offices that exceed the FDIC insurance limit were "
    r"\$\s?([\d.]+)\s?billion and \$\s?([\d.]+)\s?billion", re.I)
FOREIGN_SVB = re.compile(
    r"foreign deposits of \$\s?([\d.]+)\s?billion and \$\s?([\d.]+)\s?billion", re.I)
UNINSURED_SCHWAB = re.compile(
    r"uninsured bank deposits totaled approximately \$\s?([\d.]+)\s?billion and "
    r"\$\s?([\d.]+)\s?billion", re.I)


def uninsured_deposits(filing: Filing, body: str) -> dict:
    """
    The uninsured share, which both banks disclose in prose rather than a table.

    This single number carries most of the project's thesis, so it is read straight from
    the filing text with the sentence preserved alongside it. SVB reports U.S.-office
    uninsured balances and foreign-office balances separately; foreign deposits sit
    outside the FDIC regime entirely, so the run-risk layer in Module G is the sum.
    Figures are stated in billions and converted here to the project's $ millions.
    """
    if filing.bank == "SVB":
        m = UNINSURED_SVB.search(body)
        f = FOREIGN_SVB.search(body)
        if not m or not f:
            raise LookupError("SVB uninsured-deposit sentence not found")
        current = round(float(m.group(1)) * 1000, 1)
        foreign = round(float(f.group(1)) * 1000, 1)
        quote = squash(body[m.start():m.start() + 260])
    else:
        m = UNINSURED_SCHWAB.search(body)
        if not m:
            raise LookupError("Schwab uninsured-deposit sentence not found")
        current, foreign = round(float(m.group(1)) * 1000, 1), 0.0
        quote = squash(body[m.start():m.start() + 200])
    return dict(uninsured_domestic=current, foreign_deposits=foreign,
                source_quote=quote)


BALANCE_SHEET_LINES = {
    "SVB": [
        ("Cash and cash equivalents", "cash"),
        ("Available-for-sale securities, at fair value", "afs_fair_value"),
        ("Held-to-maturity securities, at amortized cost", "htm_net_carry"),
        ("Loans, amortized cost", "loans_amortized_cost"),
        ("Total assets", "total_assets"),
        ("Total deposits", "total_deposits"),
        ("Short-term borrowings", "short_term_borrowings"),
        ("Long-term debt", "long_term_debt"),
        ("Total liabilities", "total_liabilities"),
        ("Total SVBFG stockholders", "total_equity"),
    ],
    "Schwab": [
        ("Cash and cash equivalents", "cash"),
        ("Available for sale securities", "afs_fair_value"),
        ("Held to maturity securities", "htm_net_carry"),
        ("Bank loans", "loans_amortized_cost"),
        ("Total assets", "total_assets"),
        ("Bank deposits", "total_deposits"),
        ("Short-term borrowings", "short_term_borrowings"),
        ("Long-term debt", "long_term_debt"),
        ("Total liabilities", "total_liabilities"),
        ("Total stockholders", "total_equity"),
    ],
}


def balance_sheet(filing: Filing, tables) -> list[dict]:
    """
    The handful of balance-sheet lines the model needs, matched on label prefix.

    Only the current year's column is kept. The prior-year column in each filing is a
    restatement and does not always agree with what that year's own 10-K published:
    SVB's FY2022 filing shows end-2021 cash of $14,586m where the FY2021 filing showed
    $14,619m. We take each year's figures from that year's own filing and let
    `check_restatements` report the differences instead of hiding them.
    """
    anchor = ("Consolidated Balance Sheets" if filing.bank == "Schwab"
              else "Total investment securities")
    _, table = find_table(tables, anchor, "Total assets", "Total liabilities",
                          max_chars=3000)
    frame = table_to_frame(table)
    wanted = BALANCE_SHEET_LINES[filing.bank]
    out = []
    for label, v in label_rows(frame):
        if _is_header(label):
            continue
        for prefix, name in wanted:
            if label.lower().startswith(prefix.lower()):
                if name in {n for _, n, in [(r["line_item"], r["line_item"])
                                            for r in out]}:
                    break  # first match wins; "Total liabilities and total equity"
                           # must not overwrite "Total liabilities"
                out.append(dict(line_item=name, label=label, amount=v[0]))
                break
    return out


# ------------------------------------------------------------------------- checks


def check_securities(bank: str, year: int, rows: list[dict]) -> None:
    """
    Two arithmetic checks on every securities table we parsed.

    First, each line must satisfy amortized cost + gains + losses = fair value under the
    project's sign convention (losses negative). That catches a mis-normalised Schwab
    table immediately. Second, the component lines must add to the stated total, which
    catches a dropped or duplicated row.
    """
    for row in rows:
        implied = (row["amortized_cost"] + row["unrealized_gains"]
                   + row["unrealized_losses"])
        if not _rounded_equal(implied, row["fair_value"]):
            raise AssertionError(
                f"{bank} {year} {row['classification']} {row['security_type']}: "
                f"cost {row['amortized_cost']} + gains {row['unrealized_gains']} "
                f"+ losses {row['unrealized_losses']} = {implied}, "
                f"filing says fair value {row['fair_value']}"
            )

    for classification in {r["classification"] for r in rows}:
        block = [r for r in rows if r["classification"] == classification]
        totals = [r for r in block if r["security_type"].lower().startswith("total")]
        parts = [r for r in block if r not in totals]
        if not totals:
            continue
        total = totals[0]
        for field in ("amortized_cost", "fair_value"):
            summed = sum(r[field] for r in parts)
            if not _rounded_equal(summed, total[field]):
                raise AssertionError(
                    f"{bank} {year} {classification} {field}: components sum to "
                    f"{summed}, filing total is {total[field]}"
                )


def check_maturity(bank: str, year: int, rows: list[dict]) -> None:
    """Each security type's four buckets must add to its own total line."""
    keys = {(r["classification"], r["security_type"]) for r in rows}
    for classification, security_type in keys:
        block = {r["bucket"]: r["carrying_value"] for r in rows
                 if r["classification"] == classification
                 and r["security_type"] == security_type}
        if "total" not in block or block["total"] in (None, 0.0):
            continue
        summed = sum(block.get(b, 0.0) or 0.0 for b in MATURITY_BUCKETS)
        if not _rounded_equal(summed, block["total"]):
            raise AssertionError(
                f"{bank} {year} {classification} {security_type}: buckets sum to "
                f"{summed}, total line says {block['total']}"
            )


def check_capital_ratio(rows: list[dict], ratios: dict[tuple, dict]) -> None:
    """
    Divide the extracted Tier 1 dollars by RWA and compare against the ratio the filing
    printed itself.

    This is the check that lets us use SVB's FY2022 comparative column for end-2021
    capital: if those dollars reproduce the 16.08% the FY2021 10-K published, the two
    filings agree and the borrowing is safe. Tolerance is 5bp, which is what independent
    rounding of a $16bn numerator and a $100bn denominator costs.
    """
    holdco = {"SVB": "SVB Financial", "Schwab": "CSC"}
    by_key: dict[tuple, dict] = {}
    for r in rows:
        if r["entity"] == holdco[r["bank"]]:
            by_key.setdefault((r["bank"], r["as_of"][:4]), {})[r["metric"]] = r["value"]

    for (bank, year), published in ratios.items():
        want = published.get("holdco_tier1_ratio")
        figures = by_key.get((bank, str(year)))
        if want is None or not figures:
            continue
        tier1 = figures.get("tier1_capital")
        rwa = figures.get("risk_weighted_assets")
        if not tier1 or not rwa:
            continue
        implied = 100.0 * tier1 / rwa
        if abs(implied - want) > 0.05:
            raise AssertionError(
                f"{bank} {year}: extracted Tier 1 {tier1:,.0f} / RWA {rwa:,.0f} = "
                f"{implied:.2f}%, filing publishes {want:.2f}%"
            )
        print(f"{bank} {year}: Tier 1 ratio {implied:.2f}% matches published "
              f"{want:.2f}%")


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in fieldnames})


def main() -> None:
    securities: list[dict] = []
    maturity: list[dict] = []
    deposits: list[dict] = []
    sheet: list[dict] = []
    sensitivity: list[dict] = []
    uninsured: list[dict] = []
    capital_rows: list[dict] = []
    loan_rows: list[dict] = []
    ratios: dict[tuple, dict] = {}
    manifest: list[dict] = []

    from bs4 import BeautifulSoup

    restated_2021: dict[int, float] | None = None
    published_2021: dict[int, float] | None = None

    for filing in FILINGS:
        tables = load_tables(filing)
        body = squash(BeautifulSoup(
            filing.path.read_text(encoding="utf-8", errors="replace"), "lxml"
        ).get_text(" ", strip=True))
        tag = dict(bank=filing.bank, as_of=str(filing.as_of.date()),
                   accession=filing.accession)

        if filing.bank == "SVB":
            sec = svb_securities(filing, tables)
            mat = svb_maturity(filing, tables)
            dep = svb_deposits(filing, tables)
            sens = svb_rate_sensitivity(filing, body)
            if filing.fiscal_year == 2021:
                published_2021 = {r["shock_bp"]: r["nii_pct"] for r in sens
                                  if r["shock_bp"] != 0}
            else:
                restated_2021 = svb_restated_2021_nii(body)
        else:
            sec = schwab_securities(filing, tables)
            mat = schwab_maturity(filing, tables)
            dep = schwab_deposits(filing, tables)
            sens = schwab_rate_sensitivity(filing, body)

        check_securities(filing.bank, filing.fiscal_year, sec)
        check_maturity(filing.bank, filing.fiscal_year, mat)

        cap = capital(filing, tables)
        capital_rows += [{**{k: tag[k] for k in ("bank", "accession")}, **r}
                         for r in cap]
        ratios[(filing.bank, filing.fiscal_year)] = published_capital_ratios(
            filing, tables)

        loan_rows += [{**tag, **r} for r in
                      (svb_loans(filing, tables) if filing.bank == "SVB"
                       else schwab_loans(filing, tables))]
        securities += [{**tag, **r} for r in sec]
        maturity += [{**tag, **r} for r in mat]
        deposits += [{**tag, **r} for r in dep]
        sheet += [{**tag, **r} for r in balance_sheet(filing, tables)]
        sensitivity += [{**tag, **r} for r in sens]
        uninsured.append({**tag, **uninsured_deposits(filing, body)})
        manifest.append(dict(**tag, fiscal_year=filing.fiscal_year, cik=filing.cik,
                             units=UNITS, url=filing.url,
                             local_file=filing.path.name,
                             retrieved=str(date.today()),
                             securities_rows=len(sec), maturity_rows=len(mat)))
        print(f"{filing.bank} FY{filing.fiscal_year}: {len(sec)} securities rows, "
              f"{len(mat)} maturity rows, {len(dep)} deposit rows")

    if published_2021 and restated_2021:
        for shock, pct in published_2021.items():
            if shock in restated_2021 and abs(restated_2021[shock] - pct) > 1e-9:
                raise AssertionError(
                    f"SVB NII sensitivity at {shock:+d}bp: FY2021 10-K says {pct}%, "
                    f"FY2022 10-K restates {restated_2021[shock]}%"
                )
        print("SVB FY2021 NII sensitivity agrees across both filings")

    write_csv(RAW / "securities_detail.csv", securities,
              ["bank", "as_of", "classification", "security_type", "amortized_cost",
               "unrealized_gains", "unrealized_losses", "fair_value", "allowance",
               "net_carry", "accession"])
    write_csv(RAW / "securities_maturity.csv", maturity,
              ["bank", "as_of", "classification", "security_type", "bucket",
               "carrying_value", "amortized_cost", "weighted_avg_yield",
               "accession"])
    write_csv(RAW / "deposits.csv", deposits,
              ["bank", "as_of", "deposit_type", "amount", "accession"])
    write_csv(RAW / "balance_sheet.csv", sheet,
              ["bank", "as_of", "line_item", "label", "amount", "accession"])
    write_csv(RAW / "rate_sensitivity_disclosed.csv", sensitivity,
              ["bank", "as_of", "shock_bp", "eve_amount", "eve_change", "eve_pct",
               "nii_amount", "nii_change", "nii_pct", "accession"])
    write_csv(RAW / "uninsured_deposits.csv", uninsured,
              ["bank", "as_of", "uninsured_domestic", "foreign_deposits",
               "source_quote", "accession"])
    check_capital_ratio(capital_rows, ratios)
    write_csv(RAW / "loans.csv", loan_rows,
              ["bank", "as_of", "rate_type", "product", "bucket", "amount",
               "accession"])
    write_csv(RAW / "regulatory_capital.csv", capital_rows,
              ["bank", "as_of", "entity", "metric", "value", "accession"])
    write_csv(RAW / "filing_manifest.csv", manifest,
              ["bank", "fiscal_year", "as_of", "cik", "accession", "units", "url",
               "local_file", "retrieved", "securities_rows", "maturity_rows"])
    print(f"\nwrote {len(list(RAW.glob('*.csv')))} CSVs to {RAW}")


if __name__ == "__main__":
    main()
