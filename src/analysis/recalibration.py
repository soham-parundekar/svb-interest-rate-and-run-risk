"""
What the Basel Committee's 2024 shock recalibration would do to these results.

The 2016 USD calibration -- 200bp parallel, 300bp short, 150bp long -- is what applied
at both valuation dates, and it is what the headline results use. The Committee revised
the calibration in 2024 (BCBS d578), to be implemented by 1 January 2026. For USD the
parallel and short shocks are unchanged and the long shock rises from 150bp to 225bp.

That has a narrower reach than it first appears. The long shock enters only the
steepener and flattener, with weights of +0.90 and -0.60; the parallel and short
scenarios carry a long weight of zero. So the recalibration cannot move the parallel
or short results at all, and the question it actually raises is whether a stronger
long leg makes the steepener bite harder than the parallel shock on a book as
asset-heavy at the long end as SVB's. If it does, the worst-case scenario in the
supervisory outlier test changes identity, which is a different kind of finding from a
number moving a little.

This module recomputes the scenarios under the 2024 long shock and sets them beside
the frozen 2016 results. Both runs use the same seed and the same path count, so the
unaffected scenarios must reproduce the frozen values exactly. That is checked rather
than assumed: if a parallel result moves, the override touched something it should not
have, and the comparison is void.

    python -m src.analysis.recalibration
"""

from __future__ import annotations

import pandas as pd

from ..config import DATA_PROCESSED, DATA_RAW, overridden, params
from . import eve

# The USD long shock under the 2024 recalibration. Parallel stays at 200 and short at
# 300, so those keys are deliberately not overridden.
LONG_BP_2024 = 225.0

# Scenarios the long shock cannot reach, by construction of the shapes.
UNAFFECTED = ("base", "parallel_up", "parallel_down", "short_up", "short_down",
              "parallel_-200bp", "parallel_-100bp", "parallel_+100bp",
              "parallel_+200bp")

# Scenarios it does reach.
AFFECTED = ("steepener", "flattener")

RECONCILE_TOLERANCE_PCT = 1e-9


def scenarios_under_2024() -> pd.DataFrame:
    """
    Re-run every scenario with the long shock at 225bp, deposit cases unchanged.

    Mirrors `eve.main` rather than calling it, because only the scenario frame is
    wanted here and the heatmap is the slow part of that function.
    """
    config = params()
    base_case = config["deposits"]["base_case"]
    frames = []

    for bank in ("SVB", "Schwab"):
        for as_of in (str(config["valuation_dates"]["base"]),
                      str(config["valuation_dates"]["test"])):
            precomputed = eve.asset_values(bank, as_of)

            conservative = eve.eve_from_assets(
                precomputed["marked"], precomputed["sheet"], bank, as_of,
                float(base_case["beta"]),
                float(base_case["uninsured_half_life_years"]),
                float(base_case["insured_half_life_years"]))
            conservative.insert(2, "deposit_case", "conservative")
            frames.append(conservative)

            disclosed = eve.disclosed_eve_sensitivity(bank, as_of)
            if disclosed is not None:
                solved = eve.implied_deposit_half_life(precomputed, disclosed)
                if solved["solved"]:
                    table = solved["table"]
                    table.insert(2, "deposit_case", "implied_by_disclosure")
                    frames.append(table)

    return pd.concat(frames, ignore_index=True)


def reconciles(frozen: pd.DataFrame, revised: pd.DataFrame) -> pd.DataFrame:
    """
    Check that the scenarios the long shock cannot reach did not move.

    This is the guard on the whole exercise. The override changes one config key, and
    if that key leaks into a parallel or short shock then the two runs are not
    comparable and nothing below means anything.
    """
    keys = ["bank", "as_of", "deposit_case", "scenario"]
    merged = frozen.merge(revised, on=keys, suffixes=("_2016", "_2024"))
    unaffected = merged[merged["scenario"].isin(UNAFFECTED)].copy()
    unaffected["drift_pct"] = (unaffected["delta_eve_pct_2024"]
                               - unaffected["delta_eve_pct_2016"])
    return unaffected


def comparison(frozen: pd.DataFrame, revised: pd.DataFrame) -> pd.DataFrame:
    """One row per bank, date, deposit case and affected scenario."""
    keys = ["bank", "as_of", "deposit_case", "scenario"]
    merged = frozen.merge(revised, on=keys, suffixes=("_2016", "_2024"))
    affected = merged[merged["scenario"].isin(AFFECTED)].copy()
    affected["move_pct_points"] = (affected["delta_eve_pct_2024"]
                                   - affected["delta_eve_pct_2016"])
    return affected[keys + ["eve_2016", "eve_2024", "delta_eve_pct_2016",
                            "delta_eve_pct_2024", "move_pct_points"]]


def worst_scenario_change(frozen: pd.DataFrame, revised: pd.DataFrame,
                          capital: pd.DataFrame) -> pd.DataFrame:
    """
    Whether the binding scenario, or the outlier-test verdict, changes identity.

    The headline question. A stronger long leg could make the steepener the worst of
    the six where the parallel shock held that place before.
    """
    old = eve.outlier_test(frozen, capital)
    new = eve.outlier_test(revised, capital)
    keys = ["bank", "as_of", "deposit_case"]
    merged = old.merge(new, on=keys, suffixes=("_2016", "_2024"))
    merged["worst_scenario_changed"] = (merged["worst_scenario_2016"]
                                        != merged["worst_scenario_2024"])
    merged["breach_changed"] = (merged["breaches_15pct_2016"]
                                != merged["breaches_15pct_2024"])
    return merged[keys + ["worst_scenario_2016", "worst_scenario_2024",
                          "worst_scenario_changed",
                          "worst_delta_eve_pct_2016", "worst_delta_eve_pct_2024",
                          "loss_over_tier1_pct_2016", "loss_over_tier1_pct_2024",
                          "breaches_15pct_2016", "breaches_15pct_2024",
                          "breach_changed"]]


def main() -> None:
    frozen = pd.read_csv(DATA_PROCESSED / "eve_scenarios.csv")
    capital = pd.read_csv(DATA_RAW / "regulatory_capital.csv")

    print(f"recomputing scenarios with the USD long shock at {LONG_BP_2024:.0f}bp")
    with overridden(**{"bcbs.long_bp": LONG_BP_2024}):
        revised = scenarios_under_2024()

    check = reconciles(frozen, revised)
    worst_drift = check["drift_pct"].abs().max()
    print(f"\nunaffected scenarios checked: {len(check)} rows, "
          f"worst drift {worst_drift:.2e} percentage points")
    if worst_drift > RECONCILE_TOLERANCE_PCT:
        offenders = check.loc[check["drift_pct"].abs() > RECONCILE_TOLERANCE_PCT,
                              ["bank", "as_of", "deposit_case", "scenario",
                               "drift_pct"]]
        raise SystemExit(
            "the long-shock override moved a scenario it cannot reach, so the two "
            f"runs are not comparable:\n{offenders.to_string(index=False)}")
    print("  parallel and short scenarios reproduce exactly, as they must")

    table = comparison(frozen, revised)
    with pd.option_context("display.width", 220):
        print("\nscenarios the recalibration reaches")
        print(table.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))

        verdict = worst_scenario_change(frozen, revised, capital)
        print("\nworst-case scenario and outlier test, before and after")
        print(verdict.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))

    changed = verdict[verdict["worst_scenario_changed"]]
    if changed.empty:
        print("\nthe binding scenario is unchanged in every bank-year")
    else:
        print(f"\nthe binding scenario changes in {len(changed)} of {len(verdict)} "
              "bank-years:")
        for _, row in changed.iterrows():
            print(f"  {row['bank']} {row['as_of']} ({row['deposit_case']}): "
                  f"{row['worst_scenario_2016']} -> {row['worst_scenario_2024']}, "
                  f"{row['worst_delta_eve_pct_2016']:.2f}% -> "
                  f"{row['worst_delta_eve_pct_2024']:.2f}%")

    if verdict["breach_changed"].any():
        print("the outlier-test verdict changes for at least one bank-year")
    else:
        print("every bank-year breaches the 15% threshold under both calibrations")

    table.to_csv(DATA_PROCESSED / "recalibration_comparison.csv", index=False)
    verdict.to_csv(DATA_PROCESSED / "recalibration_outlier_test.csv", index=False)
    print(f"\nwritten to {DATA_PROCESSED / 'recalibration_comparison.csv'}")
    print(f"written to {DATA_PROCESSED / 'recalibration_outlier_test.csv'}")


if __name__ == "__main__":
    main()
