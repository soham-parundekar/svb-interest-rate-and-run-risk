"""
Module A. Turn the raw filing tables into one bucketed balance sheet per bank and date.

Everything downstream prices this table, so it has to carry four things for every
position: what it is, when it reprices, what it cost, and what the filing says it is
worth. The reconciliation at the end is an automated test rather than an eyeball check,
because it runs four times - two banks, two year-ends - and a quiet failure in any one
of them would corrupt a result nobody re-derives by hand.

    python -m src.data.buckets
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..config import DATA_PROCESSED, DATA_RAW, params

# Security type names differ between the securities note and the maturity table in the
# same filing: one writes "Agency-issued CMO-fixed rate" with an em-dash, the other with
# a spaced hyphen. Matching on raw strings loses the join silently and leaves a $10bn
# CMO bucket with no amortized cost attached.
_NORMALISE = re.compile(r"[^a-z0-9]+")

# Repricing tenor for anything that floats. SVB's variable-rate loans reset off prime or
# a one- or three-month index; a quarter is the right order of magnitude and the EVE
# result is insensitive to it, since a floating position prices near par either way.
FLOATING_REPRICE_YEARS = 0.25

# Schwab publishes no fixed/floating split for its bank loans. HELOCs are prime-indexed
# and pledged asset lines are margin-style revolvers, so both float by construction.
# First Mortgages are treated as fixed, which is the conservative reading: Schwab
# originated a meaningful share of adjustable-rate product, so calling the whole book
# fixed OVERSTATES Schwab's rate sensitivity and therefore works against this project's
# thesis rather than for it.
SCHWAB_LOAN_RATE_TYPE = {
    "first mortgages": ("fixed", 7.0),       # effective life of a seasoned 30y pool
    "helocs": ("floating", FLOATING_REPRICE_YEARS),
    "pledged asset lines": ("floating", FLOATING_REPRICE_YEARS),
    "other": ("floating", FLOATING_REPRICE_YEARS),
}

# SVB's loan maturity table has its own bucket edges.
LOAN_BUCKET_MID_YEARS = {"le_1y": 0.5, "1_5y": 3.0, "5_15y": 10.0, "gt_15y": 20.0}


def normalise_type(name: str) -> str:
    return _NORMALISE.sub(" ", str(name).lower()).strip()


def _is_total(name: str) -> bool:
    return normalise_type(name).startswith("total")


def load_raw(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA_RAW / f"{name}.csv")


def _allocate(basis: str, carry: float, total_carry: float,
              meta: pd.Series) -> tuple[float, float]:
    """
    Split a security type's cost and fair value into one maturity bucket.

    Only one side is published per bucket and the other is spread pro rata. Which side
    is published depends on the filer, not on the classification, and getting this
    backwards is a $14bn error on Schwab's 2022 HTM book:

      SVB  - heads its AFS maturity columns "Carrying Value" (fair value for an AFS
             position) and its HTM columns "Net Carry Value" (amortized cost less the
             credit allowance). So the published side flips between the two.
      Schwab - reports both classifications at fair value and adds a separate
             "Total amortized cost" summary line underneath.

    Spreading pro rata assumes the buckets of one security type trade at similar prices.
    For SVB that is nearly harmless: 95% of the HTM book sits in a single bucket, so
    there is almost nothing to allocate.
    """
    share = carry / total_carry
    if basis == "fair_value":
        return meta["amortized_cost"] * share, carry
    return carry, meta["fair_value"] * share


def carrying_basis(block: pd.DataFrame, classification: str) -> str:
    """
    Whether a maturity block's per-type figures are fair value or amortized cost.

    Read from the block rather than assumed: a filer that prints its own
    "Total amortized cost" line alongside the per-type rows is telling us those rows are
    the other thing, namely fair value.
    """
    totals = block[block["security_type"].map(_is_total)]
    if not totals.empty and totals["amortized_cost"].notna().any():
        return "fair_value"
    return "fair_value" if classification == "AFS" else "amortized_cost"


def securities_positions() -> pd.DataFrame:
    """
    Every securities position, as (bank, date, classification, type, bucket).

    The two issuers disclose yields at different granularity. SVB gives a
    weighted-average yield per security type per bucket. Schwab gives one yield per
    bucket across the whole classification, so its positions inherit the bucket yield.
    That is a limit of Schwab's disclosure, not of the model, and inside agency MBS -
    effectively the whole book - it costs very little.
    """
    detail = load_raw("securities_detail")
    maturity = load_raw("securities_maturity")
    detail = detail[~detail["security_type"].map(_is_total)].copy()
    detail["key"] = detail["security_type"].map(normalise_type)

    buckets = list(params()["securities"]["bucket_mid_years"])
    rows = []

    for (bank, as_of, classification), block in maturity.groupby(
            ["bank", "as_of", "classification"], sort=False):
        lookup = detail[(detail["bank"] == bank) & (detail["as_of"] == as_of)
                        & (detail["classification"] == classification)
                        ].set_index("key")

        # Schwab's per-bucket yields live on the classification's total lines.
        totals = block[block["security_type"].map(_is_total)]
        fallback_yield = (totals.set_index("bucket")["weighted_avg_yield"]
                          if not totals.empty else pd.Series(dtype=float))
        basis = carrying_basis(block, classification)

        for security_type, type_rows in block.groupby("security_type", sort=False):
            if _is_total(security_type):
                continue
            key = normalise_type(security_type)
            if key not in lookup.index:
                raise KeyError(
                    f"{bank} {as_of} {classification}: the maturity table lists "
                    f"{security_type!r} but the securities note has no matching line "
                    f"(looked for {key!r} among {sorted(lookup.index)})")
            meta = lookup.loc[key]

            carrying = type_rows.set_index("bucket")["carrying_value"]
            yields = type_rows.set_index("bucket")["weighted_avg_yield"]
            total_carry = float(carrying.get("total", 0.0) or 0.0)
            if total_carry == 0.0:
                continue

            for bucket in buckets:
                carry = float(carrying.get(bucket, 0.0) or 0.0)
                if carry == 0.0:
                    continue
                amortized_cost, fair_value = _allocate(basis, carry, total_carry,
                                                       meta)
                book_yield = yields.get(bucket)
                if pd.isna(book_yield) or book_yield == 0.0:
                    book_yield = yields.get("total")
                if pd.isna(book_yield) or book_yield == 0.0:
                    book_yield = fallback_yield.get(bucket)
                floating = "variable" in key
                rows.append(dict(
                    bank=bank, as_of=as_of, side="asset", instrument="securities",
                    classification=classification, label=security_type, bucket=bucket,
                    rate_type="floating" if floating else "fixed",
                    maturity_years=(FLOATING_REPRICE_YEARS if floating
                                    else params()["securities"]["bucket_mid_years"][bucket]),
                    amortized_cost=amortized_cost, fair_value=fair_value,
                    book_yield=(np.nan if pd.isna(book_yield)
                                else float(book_yield) / 100.0)))
    frame = pd.DataFrame(rows)
    for column in ("amortized_cost", "fair_value", "book_yield", "maturity_years"):
        frame[column] = pd.to_numeric(frame[column])
    return frame


def loan_positions() -> pd.DataFrame:
    """
    Loans, carried at amortized cost.

    SVB publishes a fixed-versus-variable maturity table, so its split is read straight
    off the filing: 91% of the book floated at end-2021 and 92% at end-2022. Schwab
    publishes only product balances, so the repricing behaviour comes from
    SCHWAB_LOAN_RATE_TYPE and is recorded there as an assumption.

    Loans are held at cost in the filings, with no fair value by maturity, so fair value
    is left empty here and Module D marks them from their book yield.
    """
    loans = load_raw("loans")
    rows = []
    for row in loans.itertuples():
        if row.bank == "SVB":
            if row.rate_type == "all" or row.bucket == "total":
                continue
            floating = row.rate_type == "floating"
            rows.append(dict(
                bank=row.bank, as_of=row.as_of, side="asset", instrument="loans",
                classification="loans", label=f"{row.rate_type}-rate loans",
                bucket=row.bucket, rate_type=row.rate_type,
                maturity_years=(FLOATING_REPRICE_YEARS if floating
                                else LOAN_BUCKET_MID_YEARS[row.bucket]),
                amortized_cost=row.amount, fair_value=None, book_yield=None))
        else:
            product = normalise_type(row.product)
            if product not in SCHWAB_LOAN_RATE_TYPE:
                continue
            rate_type, maturity_years = SCHWAB_LOAN_RATE_TYPE[product]
            rows.append(dict(
                bank=row.bank, as_of=row.as_of, side="asset", instrument="loans",
                classification="loans", label=row.product, bucket="product",
                rate_type=rate_type, maturity_years=maturity_years,
                amortized_cost=row.amount, fair_value=None, book_yield=None))
    return pd.DataFrame(rows)


def cash_positions() -> pd.DataFrame:
    """Cash and reserves. Prices at par in every scenario and earns the overnight rate."""
    sheet = load_raw("balance_sheet")
    cash = sheet[sheet["line_item"] == "cash"]
    return pd.DataFrame([dict(
        bank=r.bank, as_of=r.as_of, side="asset", instrument="cash",
        classification="cash", label="Cash and cash equivalents", bucket="le_1y",
        rate_type="floating", maturity_years=0.0,
        amortized_cost=r.amount, fair_value=r.amount, book_yield=None)
        for r in cash.itertuples()])


def deposit_positions() -> pd.DataFrame:
    """
    Deposits, split into the insured and uninsured layers that Module E values
    separately and Module G runs off at different speeds.

    Both banks disclose an uninsured balance in prose rather than in the deposit note,
    so the split is applied at the total and then pushed down the deposit types in
    proportion. SVB's foreign-office deposits sit outside the FDIC regime altogether and
    are counted with the uninsured layer, which is what makes its fast-money share
    around 96% rather than the 88% the domestic figure alone implies.

    Time deposits are held out of the non-maturity franchise: they have a contractual
    term, so Module E's decay model does not apply to them.
    """
    deposits = load_raw("deposits")
    uninsured = load_raw("uninsured_deposits").set_index(["bank", "as_of"])
    rows = []

    for (bank, as_of), block in deposits.groupby(["bank", "as_of"], sort=False):
        total_row = block[block["deposit_type"].map(_is_total)]
        total = float(total_row["amount"].iloc[0])
        detail = block[~block["deposit_type"].map(_is_total)]

        meta = uninsured.loc[(bank, as_of)]
        uninsured_total = float(meta["uninsured_domestic"]) + float(
            meta["foreign_deposits"])
        uninsured_share = min(uninsured_total / total, 1.0)

        for row in detail.itertuples():
            term = "time" if normalise_type(row.deposit_type).startswith("time") \
                else "non_maturity"
            interest_bearing = not normalise_type(
                row.deposit_type).startswith("noninterest")
            for layer, share in (("uninsured", uninsured_share),
                                 ("insured", 1.0 - uninsured_share)):
                rows.append(dict(
                    bank=bank, as_of=as_of, side="liability", instrument="deposits",
                    classification=f"{term}_{layer}", label=row.deposit_type,
                    bucket=layer, rate_type="floating" if interest_bearing else "fixed",
                    maturity_years=None, amortized_cost=row.amount * share,
                    fair_value=None, book_yield=None))
    return pd.DataFrame(rows)


def borrowing_positions() -> pd.DataFrame:
    """
    Short-term borrowings and long-term debt.

    SVB's short-term borrowings went from $121m to $13,565m over 2022 as deposits ran
    off. Module G needs that: secured borrowing capacity already drawn is capacity the
    bank cannot use again in a run.
    """
    sheet = load_raw("balance_sheet")
    wanted = {"short_term_borrowings": (0.5, "floating"),
              "long_term_debt": (5.0, "fixed")}
    rows = []
    for row in sheet[sheet["line_item"].isin(wanted)].itertuples():
        maturity_years, rate_type = wanted[row.line_item]
        rows.append(dict(
            bank=row.bank, as_of=row.as_of, side="liability",
            instrument="borrowings", classification=row.line_item, label=row.label,
            bucket=row.line_item, rate_type=rate_type, maturity_years=maturity_years,
            amortized_cost=row.amount, fair_value=None, book_yield=None))
    return pd.DataFrame(rows)


def build() -> pd.DataFrame:
    frames = [securities_positions(), loan_positions(), cash_positions(),
              deposit_positions(), borrowing_positions()]
    sheet = pd.concat(frames, ignore_index=True)
    # Positions that carry no fair value or yield leave those columns as object dtype
    # after the concat, which quietly breaks any later arithmetic on them.
    for column in ("amortized_cost", "fair_value", "book_yield", "maturity_years"):
        sheet[column] = pd.to_numeric(sheet[column], errors="coerce")
    return sheet.sort_values(["bank", "as_of", "side", "instrument", "classification",
                              "bucket"]).reset_index(drop=True)


# ------------------------------------------------------------------ reconciliation

def reconcile(sheet: pd.DataFrame, tolerance: float = 2.0) -> pd.DataFrame:
    """
    Check every bucketed total against the figure on the face of the balance sheet.

    Tolerance is $2m on books of $100bn-plus, which is what independently rounded
    component lines cost. Anything larger is a extraction or allocation fault, not
    rounding.
    """
    reported = load_raw("balance_sheet").set_index(["bank", "as_of", "line_item"])

    # Schwab's balance sheet shows loans net of the credit allowance while its loan note
    # totals gross. The bucketed book is gross, so it is checked against the note. SVB
    # reports "Loans, amortized cost" gross on the face of the balance sheet, so both
    # sources agree there.
    loan_note = load_raw("loans")
    loan_totals = {
        (r.bank, r.as_of): r.amount for r in loan_note.itertuples()
        if r.bucket == "total" and (r.rate_type == "all"
                                    or normalise_type(str(r.product)) == "total bank loans")
    }
    checks = []

    for (bank, as_of), block in sheet.groupby(["bank", "as_of"], sort=False):
        def bucketed(**mask) -> float:
            rows = block
            for column, value in mask.items():
                rows = rows[rows[column] == value]
            return float(rows["amortized_cost"].sum())

        securities = block[block["instrument"] == "securities"]
        afs = securities[securities["classification"] == "AFS"]
        htm = securities[securities["classification"] == "HTM"]

        candidates = [
            ("afs_fair_value", float(afs["fair_value"].sum())),
            ("htm_net_carry", float(htm["amortized_cost"].sum())),
            ("loans_amortized_cost", bucketed(instrument="loans")),
            ("cash", bucketed(instrument="cash")),
            ("total_deposits", bucketed(instrument="deposits")),
        ]
        for line_item, modelled in candidates:
            if line_item == "loans_amortized_cost" and (bank, as_of) in loan_totals:
                stated = float(loan_totals[(bank, as_of)])
            else:
                key = (bank, as_of, line_item)
                if key not in reported.index:
                    continue
                stated = float(reported.loc[key, "amount"])
            checks.append(dict(bank=bank, as_of=as_of, line_item=line_item,
                               bucketed=modelled, reported=stated,
                               difference=modelled - stated,
                               passed=abs(modelled - stated) <= tolerance))
    return pd.DataFrame(checks)


def main() -> None:
    sheet = build()
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    out = DATA_PROCESSED / "bucketed_balance_sheet.csv"
    sheet.to_csv(out, index=False)

    checks = reconcile(sheet)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(checks.to_string(index=False,
                               float_format=lambda v: f"{v:,.1f}"))
    failed = checks[~checks["passed"]]
    if not failed.empty:
        raise AssertionError(
            f"{len(failed)} reconciliation check(s) failed:\n"
            f"{failed.to_string(index=False)}")
    print(f"\n{len(sheet)} positions written to {out}")
    print(f"all {len(checks)} reconciliation checks passed")


if __name__ == "__main__":
    main()
