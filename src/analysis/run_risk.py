"""
Module G. How large a deposit run each bank could absorb before equity is gone.

The waterfall is the mechanism that turned an interest rate problem into a failure. A
bank with an underwater securities book is not insolvent while it can hold those
securities to maturity. It becomes insolvent the moment it has to sell them, and what
forces the sale is depositors leaving.

Funding a withdrawal runs down a strict order of increasingly expensive options:

  1. Cash and reserves. Free.
  2. Secured borrowing against the securities portfolio, after a collateral haircut.
     Expensive in rate terms but it realises no loss and, crucially, pledging an HTM
     security does not taint the classification. This is the step SVB took through 2022,
     taking short-term borrowings from $121m to $13.6bn.
  3. Selling available-for-sale securities. The loss here is already sitting in equity
     through AOCI, so selling does not create it; what selling adds is the discount to
     fair value a forced seller accepts.
  4. Selling held-to-maturity securities. This is the cliff. Under ASC 320, selling HTM
     securities for reasons outside a narrow set of permitted exceptions calls the
     intent to hold into question for the entire portfolio, and the whole book has to be
     reclassified and marked. A bank that sells one dollar of HTM recognises the
     unrealized loss on all of it.

For SVB at end-2022 that cliff was $15.2bn against $16.0bn of equity. The model solves
for the run size at which the bank can no longer fund the outflow, and for the size at
which market-value equity reaches zero, then sets both beside what actually happened.
The Federal Reserve's April 2023 review records over $40bn withdrawn on 9 March with a
further $100bn expected the following day - together about 85% of the deposit base.

    python -m src.analysis.run_risk
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from ..config import DATA_PROCESSED, DATA_RAW, params
from ..data.buckets import build as build_sheet


def book_inputs(sheet: pd.DataFrame, bank: str, as_of: str) -> dict:
    """
    The handful of balance-sheet quantities the waterfall needs.

    Unrealized losses are taken as cost less fair value on each classification, which is
    what the securities note discloses directly. AFS and HTM are kept apart because they
    behave completely differently in a run: the AFS loss is already in equity, the HTM
    loss is not and only appears if the book is tainted.
    """
    securities = sheet[(sheet["bank"] == bank) & (sheet["as_of"] == as_of)
                       & (sheet["instrument"] == "securities")]
    afs = securities[securities["classification"] == "AFS"]
    htm = securities[securities["classification"] == "HTM"]

    balance_sheet = pd.read_csv(DATA_RAW / "balance_sheet.csv")
    line = balance_sheet[(balance_sheet["bank"] == bank)
                         & (balance_sheet["as_of"] == as_of)].set_index("line_item")

    def value(name: str, default: float = 0.0) -> float:
        return float(line.loc[name, "amount"]) if name in line.index else default

    uninsured = pd.read_csv(DATA_RAW / "uninsured_deposits.csv")
    row = uninsured[(uninsured["bank"] == bank) & (uninsured["as_of"] == as_of)].iloc[0]

    return dict(
        bank=bank, as_of=as_of,
        cash=value("cash"),
        afs_fair_value=float(afs["fair_value"].sum()),
        afs_amortized_cost=float(afs["amortized_cost"].sum()),
        htm_fair_value=float(htm["fair_value"].sum()),
        htm_amortized_cost=float(htm["amortized_cost"].sum()),
        unrealized_loss_afs=float(afs["amortized_cost"].sum()
                                  - afs["fair_value"].sum()),
        unrealized_loss_htm=float(htm["amortized_cost"].sum()
                                  - htm["fair_value"].sum()),
        book_equity=value("total_equity"),
        existing_secured_borrowing=value("short_term_borrowings"),
        total_deposits=value("total_deposits"),
        uninsured_deposits=float(row["uninsured_domestic"]) + float(
            row["foreign_deposits"]),
    )


def waterfall(inputs: dict, run_fraction: float, haircut: float | None = None,
              fire_sale_discount: float | None = None,
              tax_rate: float | None = None,
              access_fraction: float = 1.0) -> dict:
    """
    Fund an outflow of `run_fraction` times uninsured deposits, and mark the damage.

    Collateral already pledged against existing borrowings is removed from the
    borrowing capacity first. SVB had drawn $13.6bn by end-2022, so that capacity was
    spent before the run even started; counting it twice would make the bank look
    considerably more liquid than it was.
    """
    config = params()["run_risk"]
    haircut = float(config["collateral_haircut"]) if haircut is None else haircut
    fire_sale_discount = (float(config["fire_sale_discount"])
                          if fire_sale_discount is None else fire_sale_discount)
    tax_rate = float(config["tax_rate"]) if tax_rate is None else tax_rate

    outflow = run_fraction * inputs["uninsured_deposits"]
    securities_market_value = inputs["afs_fair_value"] + inputs["htm_fair_value"]

    # Step 1: cash.
    from_cash = min(outflow, inputs["cash"])
    need = outflow - from_cash

    # Step 2: secured borrowing on whatever collateral is not already encumbered.
    encumbered = (inputs["existing_secured_borrowing"] / (1.0 - haircut)
                  if haircut < 1.0 else securities_market_value)
    free_collateral = max(securities_market_value - encumbered, 0.0)
    # access_fraction is the share of that theoretical capacity a bank can actually
    # mobilise inside a run: collateral has to be eligible, pre-positioned and operable.
    borrowing_capacity = access_fraction * (1.0 - haircut) * free_collateral
    from_borrowing = min(need, borrowing_capacity)
    need -= from_borrowing
    pledged = from_borrowing / (1.0 - haircut) if haircut < 1.0 else 0.0

    # Step 3: sell unencumbered AFS, at a discount to fair value.
    afs_available = max(inputs["afs_fair_value"] - min(pledged,
                                                       inputs["afs_fair_value"]), 0.0)
    afs_proceeds_possible = afs_available * (1.0 - fire_sale_discount)
    from_afs = min(need, afs_proceeds_possible)
    afs_sold_value = (from_afs / (1.0 - fire_sale_discount)
                      if fire_sale_discount < 1.0 else 0.0)
    need -= from_afs

    # Step 4: sell HTM, which taints the entire portfolio.
    htm_available = max(inputs["htm_fair_value"] - max(pledged
                                                       - inputs["afs_fair_value"], 0.0),
                        0.0)
    htm_proceeds_possible = htm_available * (1.0 - fire_sale_discount)
    from_htm = min(need, htm_proceeds_possible)
    htm_sold_value = (from_htm / (1.0 - fire_sale_discount)
                      if fire_sale_discount < 1.0 else 0.0)
    need -= from_htm
    htm_tapped = from_htm > 0.0

    # The AFS loss is already in book equity through AOCI, so selling does not create
    # it. Tainting the HTM book does create a loss that was not previously recognised,
    # and it lands on the whole book rather than the part sold. That asymmetry is the
    # cliff.
    taint_loss = (1.0 - tax_rate) * inputs["unrealized_loss_htm"] if htm_tapped else 0.0
    fire_sale_loss = fire_sale_discount * (afs_sold_value + htm_sold_value)

    return dict(
        run_fraction=run_fraction, outflow=outflow,
        from_cash=from_cash, from_borrowing=from_borrowing, from_afs=from_afs,
        from_htm=from_htm, unmet=need,
        afs_sold_value=afs_sold_value, htm_sold_value=htm_sold_value,
        htm_tainted=htm_tapped, taint_loss=taint_loss, fire_sale_loss=fire_sale_loss,
        market_value_equity=inputs["book_equity"] - taint_loss - fire_sale_loss,
    )


def liquidation_sensitivity(inputs: dict,
                            discounts=(0.0, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15),
                            access_levels=(1.0, 0.5, 0.0)) -> pd.DataFrame:
    """
    The two frictions that decide whether a run ends in illiquidity or insolvency.

    Cutting secured borrowing access turns out to move the liquidity threshold almost
    not at all, and the reason is worth stating because it is not obvious: capacity
    removed from pledging reappears as sales. Borrowing against a bond at a 5% haircut
    and selling it at a 2% discount raise nearly the same cash. What changes is not how
    much the bank can raise but what raising it costs - sales taint the HTM book and
    crystallise the discount, pledging does neither.

    So the parameter that actually binds is the discount a forced seller accepts. At
    end-2022 SVB's equity survives a complete liquidation only while that discount stays
    low; past a few points, marking the HTM book plus the sale haircut consumes all of
    it. This grid locates that point instead of asserting one.
    """
    rows = []
    for access in access_levels:
        for discount in discounts:
            thresholds = failure_thresholds(inputs, access_fraction=float(access),
                                            fire_sale_discount=float(discount))
            full_run = waterfall(inputs, 1.0, access_fraction=float(access),
                                 fire_sale_discount=float(discount))
            rows.append(dict(bank=inputs["bank"], as_of=inputs["as_of"],
                             secured_access=float(access),
                             fire_sale_discount=float(discount),
                             **thresholds,
                             htm_tainted_at_full_run=full_run["htm_tainted"],
                             taint_loss_at_full_run=full_run["taint_loss"],
                             fire_sale_loss_at_full_run=full_run["fire_sale_loss"]))
    return pd.DataFrame(rows)


def break_even_discount(inputs: dict, access_fraction: float = 1.0) -> float:
    """
    The fire-sale discount at which a complete run leaves exactly zero equity.

    A single number for the question "how badly could SVB have sold and still been
    solvent on paper". Returns NaN where no discount in a plausible range does it,
    which is the answer for a bank with no HTM book to taint.
    """
    def equity(discount: float) -> float:
        return waterfall(inputs, 1.0, fire_sale_discount=discount,
                         access_fraction=access_fraction)["market_value_equity"]

    low, high = 0.0, 0.60
    if equity(low) <= 0:
        return 0.0
    if equity(high) > 0:
        return float("nan")
    return float(brentq(equity, low, high, xtol=1e-6))


def failure_thresholds(inputs: dict, **overrides) -> dict:
    """
    The two run sizes that matter, because a bank can fail either way.

    x_solvency is where market-value equity reaches zero: the bank still has assets to
    sell, but selling them has consumed the shareholders. Equity falls discontinuously
    the moment the HTM book is tainted, so there may be no x where it equals exactly
    zero; when the jump carries it straight through, the crossing is reported as
    discontinuous rather than handed to a root-finder that would return whichever side
    it happened to land on.

    x_liquidity is where the waterfall simply runs dry - every security pledged or sold
    and the outflow still unmet. This is the one that actually closes a bank, and it was
    missing from the first version of this model: measuring only accounting equity made
    SVB look solvent at a full run while $54bn of withdrawals went unfunded.

    The binding threshold is whichever comes first, and which one that is turns out to
    differ between the two banks and between the two year-ends.
    """
    grid_low, grid_high, grid_count = params()["run_risk"]["run_fraction_grid"]
    grid = np.linspace(float(grid_low), float(grid_high), int(grid_count))
    results = [waterfall(inputs, float(x), **overrides) for x in grid]

    equity = np.array([r["market_value_equity"] for r in results])
    unmet = np.array([r["unmet"] for r in results])

    def first_crossing(flags: np.ndarray) -> int | None:
        hits = np.flatnonzero(flags)
        return int(hits[0]) if hits.size else None

    liquidity_index = first_crossing(unmet > 1e-6)
    x_liquidity = float(grid[liquidity_index]) if liquidity_index else np.nan

    solvency_index = first_crossing(equity <= 0)
    if solvency_index is None:
        x_solvency, crossing = np.nan, "never"
    elif solvency_index == 0:
        x_solvency, crossing = 0.0, "insolvent_at_zero"
    else:
        before, after = grid[solvency_index - 1], grid[solvency_index]
        jumped = (results[solvency_index]["htm_tainted"]
                  and not results[solvency_index - 1]["htm_tainted"])
        if jumped:
            x_solvency, crossing = float(after), "discontinuous_at_htm_taint"
        else:
            x_solvency = float(brentq(
                lambda x: waterfall(inputs, x, **overrides)["market_value_equity"],
                float(before), float(after), xtol=1e-6))
            crossing = "continuous"

    candidates = [v for v in (x_solvency, x_liquidity) if np.isfinite(v)]
    binding = min(candidates) if candidates else np.nan
    which = ("liquidity" if np.isfinite(x_liquidity) and binding == x_liquidity
             else "solvency" if candidates else "neither")

    return dict(x_solvency=x_solvency, solvency_crossing=crossing,
                x_liquidity=x_liquidity, x_star=binding, binding_constraint=which,
                equity_at_full_run=float(equity[-1]),
                unmet_at_full_run=float(unmet[-1]))


def profile(inputs: dict, points: int | None = None) -> pd.DataFrame:
    grid_low, grid_high, grid_count = params()["run_risk"]["run_fraction_grid"]
    grid = np.linspace(float(grid_low), float(grid_high), points or int(grid_count))
    rows = [dict(bank=inputs["bank"], as_of=inputs["as_of"],
                 **waterfall(inputs, float(x))) for x in grid]
    frame = pd.DataFrame(rows)
    frame["equity_pct_of_book"] = (100.0 * frame["market_value_equity"]
                                   / inputs["book_equity"])
    return frame


def main() -> None:
    config = params()
    sheet = build_sheet()
    test_date = str(config["valuation_dates"]["test"])
    one_day = float(config["run_risk"]["svb_outflow_9_march_2023"])
    two_day = one_day + float(config["run_risk"]["svb_expected_outflow_10_march_2023"])

    profiles, summary, sensitivities = [], [], []
    for bank in ("SVB", "Schwab"):
        for as_of in (str(config["valuation_dates"]["base"]), test_date):
            inputs = book_inputs(sheet, bank, as_of)
            if inputs["uninsured_deposits"] <= 0:
                continue
            threshold = failure_thresholds(inputs)
            profiles.append(profile(inputs))
            sensitivities.append(liquidation_sensitivity(inputs))

            summary.append(dict(
                bank=bank, as_of=as_of,
                uninsured_deposits=inputs["uninsured_deposits"],
                uninsured_share=100.0 * inputs["uninsured_deposits"]
                / inputs["total_deposits"],
                book_equity=inputs["book_equity"],
                unrealized_loss_htm=inputs["unrealized_loss_htm"],
                after_tax_taint=(1 - float(config["run_risk"]["tax_rate"]))
                * inputs["unrealized_loss_htm"],
                taint_as_pct_of_equity=(100.0 * (1 - float(config["run_risk"]
                                                           ["tax_rate"]))
                                        * inputs["unrealized_loss_htm"]
                                        / inputs["book_equity"]
                                        if inputs["book_equity"] else np.nan),
                x_solvency=threshold["x_solvency"],
                x_liquidity=threshold["x_liquidity"],
                x_star=threshold["x_star"],
                binding=threshold["binding_constraint"],
                outflow_at_x_star=(threshold["x_star"] * inputs["uninsured_deposits"]
                                   if np.isfinite(threshold["x_star"]) else np.nan),
                equity_at_full_run=threshold["equity_at_full_run"],
                unmet_at_full_run=threshold["unmet_at_full_run"],
                break_even_fire_sale_discount=break_even_discount(inputs),
            ))

    summary_frame = pd.DataFrame(summary)
    is_svb = summary_frame["bank"] == "SVB"
    summary_frame["outflow_9_march"] = np.where(is_svb, one_day, np.nan)
    summary_frame["demand_over_two_days"] = np.where(is_svb, two_day, np.nan)
    summary_frame["realised_run_fraction"] = (summary_frame["outflow_9_march"]
                                              / summary_frame["uninsured_deposits"])
    summary_frame["two_day_run_fraction"] = (summary_frame["demand_over_two_days"]
                                             / summary_frame["uninsured_deposits"])

    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(summary_frame[["bank", "as_of", "uninsured_share", "book_equity",
                             "unrealized_loss_htm", "after_tax_taint",
                             "taint_as_pct_of_equity"]].to_string(
            index=False, float_format=lambda v: f"{v:,.1f}"))
        print()
        print(summary_frame[["bank", "as_of", "x_solvency", "x_liquidity", "x_star",
                             "binding", "outflow_at_x_star",
                             "realised_run_fraction", "two_day_run_fraction",
                             "break_even_fire_sale_discount"]].to_string(
            index=False, float_format=lambda v: f"{v:,.3f}"))

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    pd.concat(profiles, ignore_index=True).to_csv(
        DATA_PROCESSED / "run_risk_profile.csv", index=False)
    pd.concat(sensitivities, ignore_index=True).to_csv(
        DATA_PROCESSED / "run_risk_sensitivity.csv", index=False)
    summary_frame.to_csv(DATA_PROCESSED / "run_risk_summary.csv", index=False)
    print(f"\nwritten to {DATA_PROCESSED}")


if __name__ == "__main__":
    main()
