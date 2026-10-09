"""
Module F. Economic value of equity, and what the six BCBS shocks do to it.

EVE is the market value of assets less the market value of liabilities. It is the
measure that would have shown SVB's problem, and it is the measure SVB stopped
publishing after the FY2021 10-K.

This module is also where the project gets its second check against reality. SVB's
FY2021 Item 7A disclosed a base EVE of $20,672m and a sensitivity of -13.9% at +100bp
and -27.7% at +200bp. Those numbers came out of SVB's own internal simulation model,
with its own deposit assumptions, and were never meant to be reproducible from outside.
Getting within shouting distance of them from public filings alone is a stronger test of
the whole framework than the securities fair value on its own, because it exercises the
liability side too.

Where the two must differ is worth stating up front. SVB modelled its own deposit decay
from a client study; this project cannot see that study and uses a two-layer split keyed
to the disclosed uninsured share. SVB's published figure includes the pay-fixed swap
book it held at end-2021 and later terminated; this project does not model the swaps,
and SVB says in the same filing what they were worth - the +100bp sensitivity was -15.4%
before hedging and -13.9% after.

    python -m src.analysis.eve
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import DATA_PROCESSED, DATA_RAW, params
from ..data.buckets import build as build_sheet
from ..models.deposits import value_deposits
from ..models.hull_white import HullWhite, build as build_hull_white
from ..models.mbs import MortgagePool, PrepaymentModel, mortgage_spread
from ..models.nss import build_curve
from ..models.securities import (
    bullet_cash_flows,
    calibrate_portfolio,
    floating_cash_flows,
    is_floating,
    is_pass_through,
    pass_through_cash_flows,
)
from ..models.shocks import ShockedCurve, scenarios


def value_securities(sheet: pd.DataFrame, bank: str, as_of: str, model: HullWhite,
                     oas: dict, prepayment: PrepaymentModel, spread: float,
                     paths: int, seed: int) -> float:
    """Mark the securities book on a given curve, holding the calibrated spreads."""
    positions = sheet[(sheet["bank"] == bank) & (sheet["as_of"] == as_of)
                      & (sheet["instrument"] == "securities")]
    total = 0.0
    for _, position in positions.iterrows():
        label = str(position["label"])
        spread_bp = oas.get((position["classification"], label))
        if spread_bp is None:
            spread_bp = 0.0
        balance = float(position["amortized_cost"])

        if is_floating(label) or position["rate_type"] == "floating":
            flows = floating_cash_flows(model.curve, balance)
        elif is_pass_through(label):
            pool = MortgagePool.from_position(balance, float(position["book_yield"]),
                                              bucket=str(position["bucket"]))
            flows, _ = pass_through_cash_flows(model, pool, prepayment, spread,
                                               paths, seed)
        else:
            flows = bullet_cash_flows(model.curve, balance,
                                      float(position["book_yield"]),
                                      float(position["maturity_years"]))
        total += flows.price(spread_bp)
    return total


def value_other_assets(sheet: pd.DataFrame, bank: str, as_of: str, curve) -> float:
    """
    Cash and loans.

    Floating-rate loans price at par at their next reset, which is why SVB's loan book
    contributes almost nothing to its rate exposure: 91% of it floated. Fixed-rate loans
    are priced as bullets at their bucket's representative maturity, discounted on the
    curve without a credit spread, since the filings give no loan-level fair value to
    calibrate one against. That understates credit risk and is listed as a limitation;
    it does not affect the rate sensitivity, which is what this module measures.
    """
    rows = sheet[(sheet["bank"] == bank) & (sheet["as_of"] == as_of)
                 & (sheet["instrument"].isin(["cash", "loans"]))]
    total = 0.0
    for _, row in rows.iterrows():
        balance = float(row["amortized_cost"])
        if row["instrument"] == "cash" or row["rate_type"] == "floating":
            total += balance
            continue
        maturity = float(row["maturity_years"])
        # No disclosed loan yield, so the curve's own par rate at that maturity is used
        # as the coupon. A par bond prices to par, so this treats the fixed loan book as
        # fairly priced at origination and lets the shock move it from there.
        coupon = float(curve.zero_rate(maturity))
        total += bullet_cash_flows(curve, balance, coupon, maturity).price(0.0)
    return total


def value_borrowings(sheet: pd.DataFrame, bank: str, as_of: str, curve) -> float:
    """
    Short-term borrowings and long-term debt, marked on the curve.

    Priced at the curve's own rate for their maturity, so they sit at par in the base
    case and move with the shock. No issuer spread is applied: the filings give no fair
    value for these liabilities to calibrate one against, and a spread that does not
    move with rates would not change the sensitivity this module measures.
    """
    rows = sheet[(sheet["bank"] == bank) & (sheet["as_of"] == as_of)
                 & (sheet["instrument"] == "borrowings")]
    total = 0.0
    for _, row in rows.iterrows():
        balance = float(row["amortized_cost"])
        if row["rate_type"] == "floating":
            total += balance
            continue
        maturity = float(row["maturity_years"])
        coupon = float(curve.zero_rate(maturity))
        total += bullet_cash_flows(curve, balance, coupon, maturity).price(0.0)
    return total


def economic_value_of_equity(sheet: pd.DataFrame, bank: str, as_of: str,
                             model: HullWhite, oas: dict,
                             prepayment: PrepaymentModel, spread: float,
                             paths: int, seed: int, beta: float | None = None,
                             uninsured_half_life: float | None = None,
                             insured_half_life: float | None = None) -> dict:
    """
    Market value of assets less market value of liabilities, on one curve.

    The deposit franchise enters here with its sign the right way round: a larger
    franchise value makes the liability cheaper than its book balance, which raises EVE.
    That is the hedge, and it is why the deposit assumptions passed in move the answer
    as much as anything on the asset side.
    """
    curve = model.curve
    securities = value_securities(sheet, bank, as_of, model, oas, prepayment, spread,
                                  paths, seed)
    other_assets = value_other_assets(sheet, bank, as_of, curve)
    deposits = value_deposits(curve, sheet, bank, as_of,
                              uninsured_half_life=uninsured_half_life,
                              insured_half_life=insured_half_life, beta=beta)
    borrowings = value_borrowings(sheet, bank, as_of, curve)

    assets = securities + other_assets
    liabilities = deposits["economic_value"] + borrowings
    return dict(bank=bank, as_of=as_of, securities=securities,
                other_assets=other_assets, assets=assets,
                deposits=deposits["economic_value"],
                deposit_franchise=deposits["franchise_value"],
                borrowings=borrowings, liabilities=liabilities,
                eve=assets - liabilities)


def asset_values(bank: str, as_of: str, paths: int | None = None) -> dict:
    """
    Mark the asset side once under every scenario, and keep the pieces.

    Worth separating because the two sides of EVE cost wildly different amounts to
    compute. Assets need the Monte Carlo, since the mortgage book's cash flows depend on
    the rate path. The deposit franchise is a deterministic integral over the curve. So
    the expensive half is evaluated once and the deposit assumptions can then be varied
    for free, which is what makes the implied-half-life solve below practical.
    """
    config = params()
    paths = paths or int(config["hull_white"]["paths"])
    seed = int(config["hull_white"]["seed"])

    sheet = build_sheet()
    prepayment = PrepaymentModel.from_config()
    base_curve, _ = build_curve(as_of)
    base_model, fit = build_hull_white(base_curve)
    spread = mortgage_spread(base_curve, as_of)

    book = calibrate_portfolio(sheet, bank, as_of, base_model, prepayment, spread,
                               paths, seed)

    from ..models.shocks import ShockShape

    curves = {"base": base_curve}
    for name, shape in scenarios().items():
        curves[name] = ShockedCurve(base_curve, shape)
    for shock_bp in (-200, -100, 100, 200):
        curves[f"parallel_{shock_bp:+d}bp"] = ShockedCurve(
            base_curve, ShockShape(f"parallel_{shock_bp:+d}", 0.0, 0.0,
                                   float(shock_bp)))

    marked = {}
    for name, curve in curves.items():
        model = HullWhite(a=fit.a, sigma=fit.sigma, curve=curve)
        securities = value_securities(sheet, bank, as_of, model, book.oas, prepayment,
                                      spread, paths, seed)
        other = value_other_assets(sheet, bank, as_of, curve)
        marked[name] = dict(curve=curve, securities=securities, other_assets=other,
                            assets=securities + other,
                            borrowings=value_borrowings(sheet, bank, as_of, curve))

    return dict(bank=bank, as_of=as_of, sheet=sheet, book=book, marked=marked,
                fit=fit, spread=spread)


def eve_from_assets(marked: dict, sheet: pd.DataFrame, bank: str, as_of: str,
                    beta: float, uninsured_half_life: float,
                    insured_half_life: float) -> pd.DataFrame:
    """EVE under each already-marked scenario, for one set of deposit assumptions."""
    rows = []
    for name, block in marked.items():
        deposits = value_deposits(block["curve"], sheet, bank, as_of,
                                  uninsured_half_life=uninsured_half_life,
                                  insured_half_life=insured_half_life, beta=beta)
        liabilities = deposits["economic_value"] + block["borrowings"]
        rows.append(dict(
            scenario=name, securities=block["securities"],
            other_assets=block["other_assets"], assets=block["assets"],
            deposits=deposits["economic_value"],
            deposit_franchise=deposits["franchise_value"],
            borrowings=block["borrowings"], liabilities=liabilities,
            eve=block["assets"] - liabilities))

    frame = pd.DataFrame(rows)
    base_eve = float(frame.loc[frame["scenario"] == "base", "eve"].iloc[0])
    frame["delta_eve"] = frame["eve"] - base_eve
    frame["delta_eve_pct"] = 100.0 * frame["delta_eve"] / base_eve
    frame.insert(0, "bank", bank)
    frame.insert(1, "as_of", as_of)
    return frame


def implied_deposit_half_life(precomputed: dict, target_pct: float,
                              scenario: str = "parallel_+100bp",
                              beta: float | None = None,
                              insured_ratio: float = 3.5) -> dict:
    """
    Back out the deposit decay SVB's own disclosed EVE sensitivity implies.

    This inverts the usual question. Rather than asking what EVE sensitivity follows
    from an assumed deposit half-life, it asks: given that SVB published -13.9% at
    +100bp, how long did its model need those deposits to stay for that number to come
    out? The asset side is fixed by the securities the bank disclosed, so the deposit
    assumption is the only free parameter left, and it can be solved for.

    The answer is the sharpest form of the project's question. A long implied half-life
    on a base that was 96% uninsured and concentrated in one industry is an assumption
    about depositor behaviour, not a measurement - and it is the assumption that broke
    in March 2023.

    The insured layer is held at `insured_ratio` times the uninsured one, so the shape
    of the two-layer split is preserved while its level is solved for.
    """
    from scipy.optimize import brentq

    config = params()["deposits"]
    beta = float(config["base_case"]["beta"]) if beta is None else beta
    sheet, bank, as_of = (precomputed["sheet"], precomputed["bank"],
                          precomputed["as_of"])

    def sensitivity(uninsured_half_life: float) -> float:
        frame = eve_from_assets(precomputed["marked"], sheet, bank, as_of, beta,
                                uninsured_half_life,
                                uninsured_half_life * insured_ratio)
        return float(frame.loc[frame["scenario"] == scenario,
                               "delta_eve_pct"].iloc[0])

    low, high = 0.25, 25.0
    at_low, at_high = sensitivity(low), sensitivity(high)
    if (at_low - target_pct) * (at_high - target_pct) > 0:
        return dict(solved=False, target_pct=target_pct, at_low=at_low,
                    at_high=at_high, low=low, high=high, beta=beta)

    solution = float(brentq(lambda h: sensitivity(h) - target_pct, low, high,
                            xtol=1e-4))
    frame = eve_from_assets(precomputed["marked"], sheet, bank, as_of, beta,
                            solution, solution * insured_ratio)
    return dict(solved=True, target_pct=target_pct, beta=beta,
                uninsured_half_life=solution,
                insured_half_life=solution * insured_ratio,
                base_eve=float(frame.loc[frame["scenario"] == "base", "eve"].iloc[0]),
                table=frame)


def sensitivity_heatmap(precomputed: dict, scenario: str = "parallel_+200bp",
                        insured_ratio: float = 3.5) -> pd.DataFrame:
    """
    Change in EVE across the grid of deposit beta and uninsured half-life.

    This is the figure the project is really making. Drechsler, Savov and Schnabl show
    that a deposit franchise hedges rate risk: as rates rise the spread earned on
    below-market funding widens, the liability cheapens, and the fall in asset values is
    offset. The hedge is real, and on this grid you can watch it work - the top right,
    where deposits are sticky, shows EVE barely moving or rising.

    What the grid also shows is the hedge's one precondition. It is paid for entirely by
    the assumption that the deposits stay. Move left along the half-life axis and the
    offset disappears, because a liability that runs off in eighteen months has almost
    no duration to contribute. For a bank funded by insured retail money the right-hand
    side of this chart is the relevant one. For a bank that was 96% uninsured and
    concentrated in a single industry, the left-hand side is, and nothing on the asset
    side changes between them.
    """
    config = params()["deposits"]["heatmap"]
    beta_low, beta_high, beta_count = config["beta_range"]
    life_low, life_high, life_count = config["half_life_range"]

    sheet, bank, as_of = (precomputed["sheet"], precomputed["bank"],
                          precomputed["as_of"])
    rows = []
    for beta in np.linspace(float(beta_low), float(beta_high), int(beta_count)):
        for half_life in np.linspace(float(life_low), float(life_high),
                                     int(life_count)):
            frame = eve_from_assets(precomputed["marked"], sheet, bank, as_of,
                                    float(beta), float(half_life),
                                    float(half_life) * insured_ratio)
            row = frame.loc[frame["scenario"] == scenario].iloc[0]
            base = float(frame.loc[frame["scenario"] == "base", "eve"].iloc[0])
            rows.append(dict(bank=bank, as_of=as_of, scenario=scenario,
                             beta=float(beta), uninsured_half_life=float(half_life),
                             base_eve=base, delta_eve=float(row["delta_eve"]),
                             delta_eve_pct=float(row["delta_eve_pct"])))
    return pd.DataFrame(rows)


def run_scenarios(bank: str, as_of: str, paths: int | None = None,
                  extra_shocks_bp: tuple[int, ...] = (-200, -100, 100, 200)
                  ) -> pd.DataFrame:
    """
    Base EVE plus every BCBS scenario, and the parallel shocks SVB itself disclosed.

    The same random draws are reused across scenarios. That is not a cosmetic choice:
    EVE differences between scenarios are a fraction of a percent of the gross values,
    and independent draws would bury them in simulation noise.
    """
    config = params()
    paths = paths or int(config["hull_white"]["paths"])
    seed = int(config["hull_white"]["seed"])

    sheet = build_sheet()
    prepayment = PrepaymentModel.from_config()
    base_curve, _ = build_curve(as_of)
    base_model, fit = build_hull_white(base_curve)
    spread = mortgage_spread(base_curve, as_of)

    book = calibrate_portfolio(sheet, bank, as_of, base_model, prepayment, spread,
                               paths, seed)
    base = economic_value_of_equity(sheet, bank, as_of, base_model, book.oas,
                                    prepayment, spread, paths, seed)

    rows = [dict(scenario="base", **{k: v for k, v in base.items()
                                     if k not in ("bank", "as_of")},
                 delta_eve=0.0, delta_eve_pct=0.0)]

    from ..models.shocks import ShockShape

    def evaluate(name: str, curve) -> None:
        model = HullWhite(a=fit.a, sigma=fit.sigma, curve=curve)
        shocked = economic_value_of_equity(sheet, bank, as_of, model, book.oas,
                                           prepayment, spread, paths, seed)
        delta = shocked["eve"] - base["eve"]
        rows.append(dict(scenario=name,
                         **{k: v for k, v in shocked.items()
                            if k not in ("bank", "as_of")},
                         delta_eve=delta,
                         delta_eve_pct=100.0 * delta / base["eve"]))

    for name, shape in scenarios().items():
        evaluate(name, ShockedCurve(base_curve, shape))

    # The parallel shocks SVB published, so the model can be set beside them directly.
    for shock_bp in extra_shocks_bp:
        shape = ShockShape(f"parallel_{shock_bp:+d}", 0.0, 0.0, float(shock_bp))
        evaluate(f"parallel_{shock_bp:+d}bp", ShockedCurve(base_curve, shape))

    frame = pd.DataFrame(rows)
    frame.insert(0, "bank", bank)
    frame.insert(1, "as_of", as_of)
    return frame


def outlier_test(results: pd.DataFrame, capital: pd.DataFrame) -> pd.DataFrame:
    """
    Worst-case EVE loss against Tier 1 capital, the 15% supervisory outlier screen.

    The threshold is from the 2016 standard, which measures the worst of the six
    scenarios against Tier 1. A bank through it is not in breach of anything
    automatically; it is flagged for supervisory attention. The interesting question for
    this project is whether SVB would have been flagged at end-2021 on public data
    alone.
    """
    bcbs = set(scenarios())
    keys = ["bank", "as_of"]
    if "deposit_case" in results.columns:
        keys.append("deposit_case")
    rows = []
    for group_key, block in results.groupby(keys, sort=False):
        bank, as_of = group_key[0], group_key[1]
        deposit_case = group_key[2] if len(group_key) > 2 else "unspecified"
        standard = block[block["scenario"].isin(bcbs)]
        worst = standard.loc[standard["delta_eve"].idxmin()]

        tier1 = capital[(capital["bank"] == bank) & (capital["as_of"] == as_of)
                        & (capital["metric"] == "tier1_capital")
                        & (capital["entity"].isin(["SVB Financial", "CSC"]))]
        tier1_value = float(tier1["value"].iloc[0]) if not tier1.empty else np.nan

        loss = -float(worst["delta_eve"])
        rows.append(dict(
            bank=bank, as_of=as_of, deposit_case=deposit_case,
            worst_scenario=worst["scenario"],
            base_eve=float(block[block["scenario"] == "base"]["eve"].iloc[0]),
            worst_delta_eve=float(worst["delta_eve"]),
            worst_delta_eve_pct=float(worst["delta_eve_pct"]),
            tier1_capital=tier1_value,
            loss_over_tier1_pct=100.0 * loss / tier1_value if tier1_value else np.nan,
            breaches_15pct=(loss / tier1_value > float(params()["bcbs"]
                                                       ["outlier_threshold"])
                            if tier1_value else False)))
    return pd.DataFrame(rows)




def disclosed_eve_sensitivity(bank: str, as_of: str) -> float | None:
    """
    The percentage change in EVE at +100bp that the bank published, if it published one.

    Only one cell in this whole project qualifies: SVB's FY2021 10-K. Its FY2022 filing
    dropped EVE, and Schwab never disclosed it in either year, which is the asymmetry
    the project exists to point at.
    """
    disclosures = pd.read_csv(DATA_RAW / "rate_sensitivity_disclosed.csv")
    row = disclosures[(disclosures["bank"] == bank)
                      & (disclosures["as_of"] == as_of)
                      & (disclosures["shock_bp"] == 100)]
    if row.empty or pd.isna(row["eve_pct"].iloc[0]):
        return None
    return float(row["eve_pct"].iloc[0])


def main() -> None:
    config = params()
    capital = pd.read_csv(DATA_RAW / "regulatory_capital.csv")
    base_case = config["deposits"]["base_case"]

    scenario_frames, heatmaps, implied_rows = [], [], []

    for bank in ("SVB", "Schwab"):
        for as_of in (str(config["valuation_dates"]["base"]),
                      str(config["valuation_dates"]["test"])):
            precomputed = asset_values(bank, as_of)

            conservative = eve_from_assets(
                precomputed["marked"], precomputed["sheet"], bank, as_of,
                float(base_case["beta"]),
                float(base_case["uninsured_half_life_years"]),
                float(base_case["insured_half_life_years"]))
            conservative.insert(2, "deposit_case", "conservative")
            scenario_frames.append(conservative)

            # Where the bank published an EVE sensitivity, solve for the deposit decay
            # that reproduces it and report the balance sheet on those terms too.
            disclosed = disclosed_eve_sensitivity(bank, as_of)
            if disclosed is not None:
                solved = implied_deposit_half_life(precomputed, disclosed)
                if solved["solved"]:
                    table = solved["table"]
                    table.insert(2, "deposit_case", "implied_by_disclosure")
                    scenario_frames.append(table)
                    implied_rows.append(dict(
                        bank=bank, as_of=as_of, disclosed_pct_at_100bp=disclosed,
                        beta=solved["beta"],
                        implied_uninsured_half_life=solved["uninsured_half_life"],
                        implied_insured_half_life=solved["insured_half_life"],
                        model_base_eve=solved["base_eve"]))

            if as_of == str(config["valuation_dates"]["base"]):
                heatmaps.append(sensitivity_heatmap(precomputed))

    results = pd.concat(scenario_frames, ignore_index=True)
    heatmap = pd.concat(heatmaps, ignore_index=True)
    implied = pd.DataFrame(implied_rows)

    with pd.option_context("display.width", 240, "display.max_columns", 25):
        shown = results[results["deposit_case"] == "conservative"]
        print("EVE under the six BCBS scenarios, conservative deposit assumptions "
              f"(beta {base_case['beta']}, uninsured half-life "
              f"{base_case['uninsured_half_life_years']}y)")
        print(shown[["bank", "as_of", "scenario", "assets", "liabilities", "eve",
                     "delta_eve", "delta_eve_pct"]].to_string(
            index=False, float_format=lambda v: f"{v:,.1f}"))

        if not implied.empty:
            print(f"\n{'=' * 72}\nDeposit decay implied by each bank's own disclosed "
                  f"EVE sensitivity")
            print(implied.to_string(index=False,
                                    float_format=lambda v: f"{v:,.2f}"))

        print(f"\n{'=' * 72}\nSupervisory outlier test (worst of six, against Tier 1)")
        outliers = outlier_test(results, capital)
        print(outliers.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    results.to_csv(DATA_PROCESSED / "eve_scenarios.csv", index=False)
    heatmap.to_csv(DATA_PROCESSED / "eve_heatmap.csv", index=False)
    implied.to_csv(DATA_PROCESSED / "implied_deposit_half_life.csv", index=False)
    outlier_test(results, capital).to_csv(DATA_PROCESSED / "outlier_test.csv",
                                          index=False)
    print(f"\nwritten to {DATA_PROCESSED}")


if __name__ == "__main__":
    main()
