"""
The out-of-sample test. Everything else in this project supports this one number.

A model calibrated using nothing but information available on 31 December 2021 is asked
to predict a figure SVB did not publish until February 2023: the fair value of its
held-to-maturity securities at 31 December 2022, which came in at $76,169m against
$91,327m of amortized cost.

The rules of the test, which matter more than the result:

  * Option-adjusted spreads are solved at end-2021 and then frozen. Not re-solved, not
    nudged, not re-bucketed.
  * Pools are aged twelve months under the END-2021 model. How much principal came back
    during 2022 is a prediction made at end-2021, not a fact read off the 2022 filing.
  * Only the discount curve changes, to the one actually observed at end-2022.

The headline is reported as a price per $100 of amortized cost rather than as a dollar
total, because the book itself changed over 2022: SVB let the HTM balance run from
$98.2bn down to $91.3bn and bought nothing new into it. Comparing dollar totals would
mix a pricing error with a composition change and allow either to hide the other. The
dollar comparison is reported too, with that caveat attached.

    python -m src.analysis.validation
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import DATA_PROCESSED, DATA_RAW, params
from ..data.buckets import build as build_sheet
from ..models.hull_white import build as build_hull_white
from ..models.mbs import MONTHS_PER_YEAR, MortgagePool, PrepaymentModel, mortgage_spread
from ..models.nss import build_curve
from ..models.securities import (
    CalibratedBook,
    bullet_cash_flows,
    calibrate_portfolio,
    floating_cash_flows,
    is_floating,
    is_pass_through,
    pass_through_cash_flows,
)

# Schwab held no HTM at all at end-2021; the book that appears in 2022 is the same
# agency collateral moved across from available-for-sale in the January and November
# transfers. So its spread is inherited from the AFS line of the same name rather than
# invented.
TRANSFERRED_FROM = {("HTM", "U.S. agency mortgage-backed securities"):
                    ("AFS", "U.S. agency mortgage-backed securities")}


def lookup_oas(book: CalibratedBook, classification: str, label: str) -> float | None:
    key = (classification, label)
    if key in book.oas:
        return book.oas[key]
    fallback = TRANSFERRED_FROM.get(key)
    if fallback and fallback in book.oas:
        return book.oas[fallback]
    return None


def age_position(position: pd.Series, book: CalibratedBook, base_model,
                 prepayment: PrepaymentModel, base_spread: float, paths: int,
                 seed: int) -> dict:
    """
    Roll one position forward twelve months using only end-2021 information.

    A pass-through loses whatever principal the end-2021 model says came back during the
    year. A bullet keeps its balance and loses a year of term; one that matures inside
    the year is returned as cash, which prices at par and carries no rate risk, and that
    is the correct treatment rather than an approximation.
    """
    label = str(position["label"])
    balance = float(position["amortized_cost"])

    if is_floating(label) or position["rate_type"] == "floating":
        return dict(kind="floating", balance=balance, pool=None, maturity=None)

    if is_pass_through(label):
        pool = book.pools.get((position["classification"], label, position["bucket"]))
        if pool is None:
            pool = MortgagePool.from_position(balance, float(position["book_yield"]),
                                              bucket=str(position["bucket"]))
        from ..models.mbs import age_pool
        aged = age_pool(pool, base_model, prepayment, base_spread, paths, seed)
        return dict(kind="pass_through", balance=aged.balance, pool=aged, maturity=None)

    maturity = float(position["maturity_years"]) - 1.0
    if maturity <= 0:
        return dict(kind="matured", balance=balance, pool=None, maturity=None)
    return dict(kind="bullet", balance=balance, pool=None, maturity=maturity)


def reprice_aged_book(sheet: pd.DataFrame, bank: str, classification: str,
                      book: CalibratedBook, base_model, test_model,
                      prepayment: PrepaymentModel, base_spread: float,
                      test_spread: float, paths: int, seed: int) -> pd.DataFrame:
    """
    Age the end-2021 book one year, then reprice it on the end-2022 curve.

    The only thing that changes between the two valuations is the curve and the passage
    of time. Spreads are the ones solved a year earlier.
    """
    positions = book.positions
    positions = positions[positions["classification"] == classification]

    rows = []
    for _, position in positions.iterrows():
        label = str(position["label"])
        oas = lookup_oas(book, classification, label)
        if oas is None:
            continue

        aged = age_position(position, book, base_model, prepayment, base_spread,
                            paths, seed)
        start_balance = float(position["amortized_cost"])

        if aged["kind"] == "matured":
            predicted = aged["balance"]          # cash at par
        elif aged["kind"] == "floating":
            flows = floating_cash_flows(test_model.curve, aged["balance"])
            predicted = flows.price(oas)
        elif aged["kind"] == "pass_through":
            pool = aged["pool"]
            if pool.balance <= 0:
                predicted = 0.0
            else:
                flows, _ = pass_through_cash_flows(test_model, pool, prepayment,
                                                   test_spread, paths, seed)
                predicted = flows.price(oas)
        else:
            flows = bullet_cash_flows(test_model.curve, aged["balance"],
                                      float(position["book_yield"]), aged["maturity"])
            predicted = flows.price(oas)

        rows.append(dict(
            bank=bank, classification=classification, label=label,
            bucket=position["bucket"], kind=aged["kind"],
            start_amortized_cost=start_balance,
            aged_amortized_cost=aged["balance"],
            predicted_fair_value=predicted,
            oas_bp=oas * 1e4))
    return pd.DataFrame(rows)


def run(paths: int | None = None) -> dict:
    config = params()
    base_date = str(config["valuation_dates"]["base"])
    test_date = str(config["valuation_dates"]["test"])
    paths = paths or int(config["hull_white"]["paths"])
    seed = int(config["hull_white"]["seed"])

    sheet = build_sheet()
    prepayment = PrepaymentModel.from_config()

    base_curve, _ = build_curve(base_date)
    test_curve, _ = build_curve(test_date)
    base_model, fit = build_hull_white(base_curve)
    test_model, _ = build_hull_white(test_curve, fit)

    base_spread = mortgage_spread(base_curve, base_date)
    test_spread = mortgage_spread(test_curve, test_date)

    results = {}
    for bank in ("SVB", "Schwab"):
        book = calibrate_portfolio(sheet, bank, base_date, base_model, prepayment,
                                   base_spread, paths, seed)
        per_class = {}
        for classification in sorted(book.positions["classification"].unique()):
            aged = reprice_aged_book(sheet, bank, classification, book, base_model,
                                     test_model, prepayment, base_spread, test_spread,
                                     paths, seed)
            if aged.empty:
                continue

            disclosed = sheet[(sheet["bank"] == bank) & (sheet["as_of"] == test_date)
                              & (sheet["instrument"] == "securities")
                              & (sheet["classification"] == classification)]
            per_class[classification] = dict(
                detail=aged,
                aged_amortized_cost=float(aged["aged_amortized_cost"].sum()),
                predicted_fair_value=float(aged["predicted_fair_value"].sum()),
                actual_amortized_cost=float(disclosed["amortized_cost"].sum()),
                actual_fair_value=float(disclosed["fair_value"].sum()),
            )
        results[bank] = dict(book=book, by_classification=per_class)

    return dict(results=results, base_spread=base_spread, test_spread=test_spread,
                base_curve=base_curve, test_curve=test_curve, fit=fit)


def summary_table(outcome: dict) -> pd.DataFrame:
    rows = []
    for bank, payload in outcome["results"].items():
        for classification, block in payload["by_classification"].items():
            predicted_price = (100.0 * block["predicted_fair_value"]
                               / block["aged_amortized_cost"]
                               if block["aged_amortized_cost"] else np.nan)
            actual_price = (100.0 * block["actual_fair_value"]
                            / block["actual_amortized_cost"]
                            if block["actual_amortized_cost"] else np.nan)
            rows.append(dict(
                bank=bank, classification=classification,
                aged_cost=block["aged_amortized_cost"],
                predicted_fv=block["predicted_fair_value"],
                predicted_price=predicted_price,
                actual_cost=block["actual_amortized_cost"],
                actual_fv=block["actual_fair_value"],
                actual_price=actual_price,
                price_error=predicted_price - actual_price,
                price_error_pct=(100.0 * (predicted_price - actual_price) / actual_price
                                 if actual_price else np.nan)))
    return pd.DataFrame(rows)


def combined_comparison(outcome: dict, sheet: pd.DataFrame) -> pd.DataFrame:
    """
    Whole securities book, on a like-for-like cost basis.

    Two corrections are needed before the two banks can be set beside each other.

    The classifications have to be combined. Schwab moved $188.6bn between them during
    2022, so its end-2022 AFS line is a residue rather than the same portfolio aged by a
    year; comparing against it alone measures the reclassification, not the model.

    And the cost basis has to be put back. A security transferred out of
    available-for-sale is rebased to its fair value on the transfer date, with the
    unrealized loss moved to AOCI and amortized over the remaining life. So Schwab's
    disclosed HTM amortized cost of $173.1bn is not what it paid; adding back the
    $17.5bn still unamortized at year end recovers an original-cost basis comparable
    with SVB's, whose HTM was bought rather than transferred.

    The gap between the two columns is itself a result: it is how much better Schwab's
    securities note looks than its economics, and it is roughly $17.5bn.
    """
    transfers = pd.read_csv(DATA_RAW / "securities_transfers.csv")
    test_date = str(params()["valuation_dates"]["test"])

    rows = []
    for bank, payload in outcome["results"].items():
        predicted_fv = sum(b["predicted_fair_value"]
                           for b in payload["by_classification"].values())
        aged_cost = sum(b["aged_amortized_cost"]
                        for b in payload["by_classification"].values())

        disclosed = sheet[(sheet["bank"] == bank) & (sheet["as_of"] == test_date)
                          & (sheet["instrument"] == "securities")]
        actual_cost = float(disclosed["amortized_cost"].sum())
        actual_fv = float(disclosed["fair_value"].sum())

        # Only a transfer recorded at the test date still distorts that date's basis.
        unamortized = float(transfers[(transfers["bank"] == bank)
                                      & (transfers["as_of"] == test_date)]
                            ["unamortized_at_year_end"].fillna(0.0).sum())
        rows.append(dict(
            bank=bank, aged_cost=aged_cost, predicted_fv=predicted_fv,
            predicted_price=100.0 * predicted_fv / aged_cost,
            disclosed_cost=actual_cost, disclosed_fv=actual_fv,
            disclosed_price=100.0 * actual_fv / actual_cost,
            unamortized_transfer_loss=unamortized,
            original_cost=actual_cost + unamortized,
            original_cost_price=100.0 * actual_fv / (actual_cost + unamortized)))

    table = pd.DataFrame(rows)
    table["error_vs_disclosed"] = table["predicted_price"] - table["disclosed_price"]
    table["error_vs_original_cost"] = (table["predicted_price"]
                                       - table["original_cost_price"])
    table["error_pct"] = (100.0 * table["error_vs_original_cost"]
                          / table["original_cost_price"])
    return table


def main() -> None:
    config = params()
    outcome = run()
    table = summary_table(outcome)

    print(f"mortgage spread over the ten-year zero: "
          f"end-2021 {outcome['base_spread'] * 100:.2f}%, "
          f"end-2022 {outcome['test_spread'] * 100:.2f}%")
    print(f"Hull-White: a = {outcome['fit'].a:.4f}, "
          f"sigma = {outcome['fit'].sigma * 1e4:.0f} bp\n")

    with pd.option_context("display.width", 220, "display.max_columns", 20):
        print(table.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))

    combined = combined_comparison(outcome, build_sheet())
    print(f"\n{'=' * 72}\nWhole securities book, both classifications combined")
    with pd.option_context("display.width", 240, "display.max_columns", 20):
        print(combined.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    combined.to_csv(DATA_PROCESSED / "combined_validation.csv", index=False)

    svb_htm = table[(table["bank"] == "SVB") & (table["classification"] == "HTM")]
    if not svb_htm.empty:
        row = svb_htm.iloc[0]
        print(f"\n{'=' * 72}")
        print("SVB held-to-maturity, the headline test")
        print(f"  predicted price   {row['predicted_price']:.2f} per 100 "
              f"of amortized cost")
        print(f"  disclosed price   {row['actual_price']:.2f} per 100 "
              f"({row['actual_fv']:,.0f} against {row['actual_cost']:,.0f})")
        print(f"  error             {row['price_error']:+.2f} points, "
              f"{row['price_error_pct']:+.1f}%")

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    table.to_csv(DATA_PROCESSED / "out_of_sample_validation.csv", index=False)
    for bank, payload in outcome["results"].items():
        payload["book"].diagnostics.to_csv(
            DATA_PROCESSED / f"oas_calibration_{bank.lower()}.csv", index=False)
    print(f"\nwritten to {DATA_PROCESSED}")


if __name__ == "__main__":
    main()
