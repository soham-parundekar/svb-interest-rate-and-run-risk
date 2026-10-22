"""
Assert every figure quoted in the manuscript evidence map against its source.

The map in `evidence_map.md` records each number the paper may carry together
with the file and field it came from. That record is only worth something if it
is enforced, so this script re-reads each source and checks the recorded value
still matches. Run it after any change to the frozen results, and before any
manuscript revision that touches a number.

A mismatch means one of two things: a result moved, or the map is wrong. Both
need resolving at the source rather than by editing the quoted figure.

    python paper/audit/verify_evidence_map.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PROCESSED = ROOT / "data" / "processed"
RAW = ROOT / "data" / "raw"

TOLERANCE = 1e-6


def processed(name: str) -> pd.DataFrame:
    return pd.read_csv(PROCESSED / f"{name}.csv")


def raw(name: str) -> pd.DataFrame:
    return pd.read_csv(RAW / f"{name}.csv")


class Ledger:
    """Collects pass/fail outcomes so one run reports every discrepancy."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, object, object]] = []

    def check(self, label: str, got: object, want: object,
              tol: float = TOLERANCE) -> None:
        ok = abs(float(got) - float(want)) <= tol
        self.rows.append((label, ok, got, want))

    @property
    def failures(self) -> list[tuple[str, bool, object, object]]:
        return [r for r in self.rows if not r[1]]

    def report(self) -> int:
        passed = len(self.rows) - len(self.failures)
        print(f"ledger entries verified: {passed}/{len(self.rows)}")
        for label, _, got, want in self.failures:
            print(f"  MISMATCH {label}: got {got!r}, map says {want!r}")
        return 1 if self.failures else 0


def check_out_of_sample(led: Ledger) -> None:
    """The headline test: 2021 calibration, spreads frozen, priced a year on."""
    frame = processed("out_of_sample_validation")

    svb_htm = frame[(frame.bank == "SVB") & (frame.classification == "HTM")].iloc[0]
    led.check("SVB HTM predicted_price", svb_htm.predicted_price, 80.356036)
    led.check("SVB HTM actual_price", svb_htm.actual_price, 83.407978)
    led.check("SVB HTM price_error_pct", svb_htm.price_error_pct, -3.659053)

    svb_afs = frame[(frame.bank == "SVB") & (frame.classification == "AFS")].iloc[0]
    led.check("SVB AFS predicted_price", svb_afs.predicted_price, 89.278254)
    led.check("SVB AFS price_error_pct", svb_afs.price_error_pct, -2.047005)

    schwab = frame[frame.bank == "Schwab"].iloc[0]
    led.check("Schwab AFS predicted_price", schwab.predicted_price, 85.719290)
    led.check("Schwab AFS price_error_pct", schwab.price_error_pct, -7.155744)


def check_transfer_correction(led: Ledger) -> None:
    """
    Schwab's disclosed cost basis was rebased by its 2022 transfers, so the
    comparison against disclosure overstates the error. The corrected figure is
    the one the manuscript uses.
    """
    frame = processed("combined_validation")

    schwab = frame[frame.bank == "Schwab"].iloc[0]
    led.check("Schwab original_cost_price", schwab.original_cost_price, 87.469958)
    led.check("Schwab error_vs_original_cost",
              schwab.error_vs_original_cost, -1.750668)
    led.check("Schwab error_pct", schwab.error_pct, -2.001450)
    led.check("Schwab unamortized_transfer_loss",
              schwab.unamortized_transfer_loss, 17521.0)

    svb = frame[frame.bank == "SVB"].iloc[0]
    led.check("SVB unamortized_transfer_loss",
              svb.unamortized_transfer_loss, 0.0)


def check_eve_profile(led: Ledger) -> None:
    """
    SVB at end-2021 under the disclosure-implied deposit case. The +100bp point
    is what the half-life was solved against; everything else is out-of-sample.
    """
    frame = processed("eve_scenarios")
    profile = frame[(frame.bank == "SVB")
                    & (frame.as_of == "2021-12-31")
                    & (frame.deposit_case == "implied_by_disclosure")]

    def pct(scenario: str) -> float:
        return float(profile[profile.scenario == scenario].delta_eve_pct.iloc[0])

    led.check("EVE +100bp (calibration target)", pct("parallel_+100bp"), -13.900015)
    led.check("EVE +200bp (out-of-sample)", pct("parallel_+200bp"), -27.607520)
    led.check("EVE -100bp", pct("parallel_-100bp"), 4.602987)
    led.check("EVE -200bp", pct("parallel_-200bp"), 2.541292)
    led.check("EVE steepener", pct("steepener"), -22.746056)
    led.check("EVE base level",
              float(profile[profile.scenario == "base"].eve.iloc[0]), 21271.335262)

    implied = processed("implied_deposit_half_life").iloc[0]
    led.check("implied uninsured half-life",
              implied.implied_uninsured_half_life, 3.589602)
    led.check("implied insured half-life",
              implied.implied_insured_half_life, 12.563608)
    led.check("deposit beta", implied.beta, 0.6)
    led.check("disclosed pct at 100bp", implied.disclosed_pct_at_100bp, -13.9)


def check_outlier_test(led: Ledger) -> None:
    """Every bank-year breaches the 15% of Tier 1 supervisory threshold."""
    frame = processed("outlier_test")

    expected = [
        ("SVB", "2021-12-31", "implied_by_disclosure", -27.607520, 36.238743),
        ("SVB", "2021-12-31", "conservative", -62.471290, 65.430256),
        ("SVB", "2022-12-31", "conservative", -93.238632, 50.726631),
        ("Schwab", "2021-12-31", "conservative", -89.653875, 67.858213),
        ("Schwab", "2022-12-31", "conservative", -161.832014, 43.621917),
    ]
    for bank, as_of, case, worst_pct, over_tier1 in expected:
        row = frame[(frame.bank == bank)
                    & (frame.as_of == as_of)
                    & (frame.deposit_case == case)].iloc[0]
        tag = f"{bank} {as_of[:4]} {case}"
        led.check(f"outlier {tag} worst_delta_eve_pct",
                  row.worst_delta_eve_pct, worst_pct)
        led.check(f"outlier {tag} loss_over_tier1_pct",
                  row.loss_over_tier1_pct, over_tier1)

    led.check("all five bank-years breach 15%", int(frame.breaches_15pct.sum()), 5)


def check_run_risk(led: Ledger) -> None:
    """
    Run thresholds and the realised March 2023 comparison. Solvency never binds
    in the base case, which is the result the manuscript reports.
    """
    frame = processed("run_risk_summary")

    expected = [
        ("SVB", "2022-12-31", "x_star", 0.675),
        ("SVB", "2021-12-31", "x_star", 0.730),
        ("SVB", "2022-12-31", "two_day_run_fraction", 0.846433),
        ("SVB", "2022-12-31", "unmet_at_full_run", 54042.531579),
        ("SVB", "2022-12-31", "break_even_fire_sale_discount", 0.282508),
        ("SVB", "2022-12-31", "uninsured_share", 95.546736),
        ("SVB", "2022-12-31", "taint_as_pct_of_equity", 74.794301),
        ("SVB", "2022-12-31", "outflow_9_march", 40000.0),
        ("Schwab", "2022-12-31", "uninsured_share", 18.787971),
        ("Schwab", "2022-12-31", "taint_as_pct_of_equity", 30.509779),
    ]
    for bank, as_of, field, want in expected:
        row = frame[(frame.bank == bank) & (frame.as_of == as_of)].iloc[0]
        led.check(f"{bank} {as_of[:4]} {field}", row[field], want)

    led.check("x_solvency empty for every bank-year",
              int(frame.x_solvency.notna().sum()), 0)

    grid = processed("run_risk_sensitivity")
    svb22 = grid[(grid.bank == "SVB") & (grid.as_of == "2022-12-31")]
    led.check("sensitivity grid rows", len(svb22), 21)
    led.check("grid x_star minimum", svb22.x_star.min(), 0.2217, tol=1e-4)
    led.check("grid x_star maximum", svb22.x_star.max(), 0.7050, tol=1e-4)
    led.check("solvency binds somewhere in the grid",
              int("solvency" in set(svb22.binding_constraint)), 1)


def check_robustness(led: Ledger) -> None:
    """
    The sweep runs at 4,000 paths, so its base case is not the headline figure.
    Keeping both pinned here is what stops the two being interchanged.
    """
    frame = processed("robustness_sweep")

    base = float(frame[frame.variant == "base case"].predicted_price.iloc[0])
    led.check("sweep base case at 4,000 paths", base, 80.354043)
    led.check("sweep span", frame.predicted_price.max() - frame.predicted_price.min(),
              3.600821)
    led.check("sweep minimum", frame.predicted_price.min(), 78.304158)
    led.check("sweep maximum", frame.predicted_price.max(), 81.904979)
    led.check("every variant errs in the same direction",
              int((frame.error_pct < 0).all()), 1)


def check_disclosure_asymmetry(led: Ledger) -> None:
    """
    The paper's premise. SVB disclosed a full EVE profile for 2021 and none for
    2022; Schwab disclosed none in either year.
    """
    frame = raw("rate_sensitivity_disclosed")

    svb21 = frame[(frame.bank == "SVB") & (frame.as_of == "2021-12-31")]
    led.check("disclosed base EVE",
              float(svb21[svb21.shock_bp == 0].eve_amount.iloc[0]), 20672.0)
    for shock, want in [(200, -27.7), (100, -13.9), (-100, 6.0), (-200, 3.1)]:
        led.check(f"disclosed EVE pct at {shock:+}bp",
                  float(svb21[svb21.shock_bp == shock].eve_pct.iloc[0]), want)

    svb22 = frame[(frame.bank == "SVB") & (frame.as_of == "2022-12-31")]
    led.check("SVB 2022 EVE wholly absent", int(svb22.eve_pct.isna().all()), 1)
    led.check("SVB 2022 NII present at every shock",
              int(svb22.nii_pct.notna().all()), 1)
    led.check("Schwab discloses no EVE in either year",
              int(frame[frame.bank == "Schwab"].eve_pct.isna().all()), 1)


def check_transfers(led: Ledger) -> None:
    """
    SVB did redesignate in 2021, contrary to the repository README. The amount
    is small, but the manuscript has to state it accurately.
    """
    frame = raw("securities_transfers")

    svb = frame[frame.bank == "SVB"].iloc[0]
    led.check("SVB 2021 redesignation fair value",
              svb.fair_value_transferred, 8800.0)
    led.check("SVB 2021 redesignation loss",
              svb.unrealized_loss_at_transfer, 132.0)

    total = frame[(frame.bank == "Schwab") & (frame.transfer == "total")].iloc[0]
    led.check("Schwab transferred fair value", total.fair_value_transferred, 188600.0)
    led.check("Schwab transfer loss", total.unrealized_loss_at_transfer, 18228.0)
    led.check("Schwab transferred original cost", total.original_cost, 206828.0)


def check_diagnostics(led: Ledger) -> None:
    """
    The engine checks, now persisted rather than quoted from a terminal.

    Both figures were previously wrong in the write-up: the pricing error was
    stated as 0.4bp against an observed 0.118bp, and the 2021 curve residual as
    7.6bp against an observed 8.705bp. Pinning them here is what stops that
    recurring.
    """
    pricing = processed("pricing_diagnostics")
    led.check("worst Monte Carlo pricing error, bp",
              pricing.error_bp.abs().max(), 0.118394)
    led.check("worst error in standard errors",
              pricing.error_in_standard_errors.abs().max(), 0.184198)
    led.check("pricing check path count", pricing.paths.iloc[0], 20000)
    led.check("pricing tolerance gate, bp", pricing.tolerance_bp.iloc[0], 5.0)
    led.check("mean reversion a", pricing.mean_reversion_a.iloc[0], 0.088896, tol=1e-5)
    led.check("short-rate sigma", pricing.sigma.iloc[0], 0.006187, tol=1e-5)

    short_rate = processed("short_rate_calibration")
    led.check("calibration grid rows", len(short_rate), 12)
    chosen = short_rate[short_rate.is_chosen_specification].iloc[0]
    led.check("chosen window is 1990-2022 monthly",
              int(chosen.window == "1990-2022" and chosen.step == "monthly"), 1)
    led.check("chosen half-life, years", chosen.half_life_years, 7.7973, tol=1e-4)
    led.check("chosen sigma, bp", chosen.sigma_bp, 61.8651, tol=1e-4)
    led.check("chosen long-run mean, pct", chosen.long_run_mean_pct, 1.7646, tol=1e-4)
    led.check("chosen R-squared", chosen.r_squared, 0.0089, tol=1e-4)

    # The identification failure that drove the specification choice.
    failed = short_rate[(short_rate.window == "2003-2022")
                        & (short_rate.step == "daily")].iloc[0]
    led.check("2003-2022 daily half-life, years", failed.half_life_years,
              364.8202, tol=1e-3)
    led.check("2003-2022 daily long-run mean, pct", failed.long_run_mean_pct,
              86.0726, tol=1e-3)
    led.check("2003-2022 daily R-squared", failed.r_squared, 0.0, tol=5e-5)
    led.check("best R-squared anywhere in the grid", short_rate.r_squared.max(),
              0.0387, tol=1e-4)

    curves = processed("curve_fit_diagnostics")
    expected = [
        ("2021-12-31", -8.704993, 3.875846, 10.882984),
        ("2022-12-31", -10.050728, 5.385848, 14.961809),
    ]
    for as_of, worst, rmse, vs_bootstrap in expected:
        rows = curves[curves.as_of == as_of]
        at_worst = rows.loc[rows.par_error_bp.abs().idxmax()]
        led.check(f"{as_of} worst par residual, bp", at_worst.par_error_bp, worst)
        led.check(f"{as_of} worst residual tenor", at_worst.tenor_years, 20.0)
        led.check(f"{as_of} par-fit RMSE, bp", rows.rmse_bp.iloc[0], rmse)
        led.check(f"{as_of} NSS vs bootstrap max, bp",
                  rows.max_abs_nss_vs_bootstrap_bp.iloc[0], vs_bootstrap)


def check_recalibration(led: Ledger) -> None:
    """
    The 2024 Basel recalibration, run rather than disclaimed.

    The long shock reaches only the steepener and flattener, so the parallel and
    short results must be untouched. That is the guard, and it is pinned here
    alongside the finding: a stronger long leg makes the steepener the binding
    scenario for SVB where the parallel rise held that place before.
    """
    table = processed("recalibration_comparison")
    steepener = table[table.scenario == "steepener"]

    expected = [
        ("SVB", "2021-12-31", "conservative", -48.023804, -69.207057),
        ("SVB", "2021-12-31", "implied_by_disclosure", -22.746056, -32.587907),
        ("SVB", "2022-12-31", "conservative", -66.829778, -95.038007),
        ("Schwab", "2021-12-31", "conservative", -37.821709, -63.058424),
        ("Schwab", "2022-12-31", "conservative", -56.078071, -96.986044),
    ]
    for bank, as_of, case, under_2016, under_2024 in expected:
        row = steepener[(steepener.bank == bank)
                        & (steepener.as_of == as_of)
                        & (steepener.deposit_case == case)].iloc[0]
        led.check(f"steepener 2016 {bank} {as_of[:4]} {case}",
                  row.delta_eve_pct_2016, under_2016)
        led.check(f"steepener 2024 {bank} {as_of[:4]} {case}",
                  row.delta_eve_pct_2024, under_2024)

    verdict = processed("recalibration_outlier_test")
    led.check("binding scenario changes in 3 bank-years",
              int(verdict.worst_scenario_changed.sum()), 3)
    led.check("outlier-test verdict unchanged everywhere",
              int(verdict.breach_changed.sum()), 0)

    # The disclosure-implied case is the only one with an external comparator, so
    # it is the row the manuscript leads with.
    implied = verdict[(verdict.bank == "SVB")
                      & (verdict.deposit_case == "implied_by_disclosure")].iloc[0]
    led.check("SVB implied worst loss under 2024",
              implied.worst_delta_eve_pct_2024, -32.587907)
    led.check("SVB implied loss over Tier 1 under 2024",
              implied.loss_over_tier1_pct_2024, 42.776198)

    # Schwab's binding scenario does not change: its parallel exposure stays worse
    # than even the strengthened steepener.
    for as_of, worst in [("2021-12-31", -89.653875), ("2022-12-31", -161.832014)]:
        row = verdict[(verdict.bank == "Schwab") & (verdict.as_of == as_of)].iloc[0]
        led.check(f"Schwab {as_of[:4]} still parallel_up",
                  int(row.worst_scenario_2024 == "parallel_up"), 1)
        led.check(f"Schwab {as_of[:4]} worst unchanged",
                  row.worst_delta_eve_pct_2024, worst)


def main() -> int:
    led = Ledger()
    check_out_of_sample(led)
    check_transfer_correction(led)
    check_eve_profile(led)
    check_outlier_test(led)
    check_run_risk(led)
    check_robustness(led)
    check_disclosure_asymmetry(led)
    check_transfers(led)
    check_diagnostics(led)
    check_recalibration(led)
    return led.report()


if __name__ == "__main__":
    sys.exit(main())
