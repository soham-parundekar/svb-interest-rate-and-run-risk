"""
Persist the two engine diagnostics the write-up needs to quote.

Both numbers already existed, but only ever on a terminal: the Monte Carlo pricing
error lived inside a test assertion and the curve-fit residual inside a print
statement. Neither could be cited, because nothing on disk held the observed value
and a test that asserts a 5bp tolerance does not tell a reader what the error
actually was.

So this writes them out. Two tables:

  curve_fit_diagnostics.csv   par-yield residual per tenor, at both valuation dates
  pricing_diagnostics.csv     simulated against closed-form discount factors

The pricing table keeps its Monte Carlo standard error. That is sampling error of
the simulation itself -- how precisely 20,000 paths pin down an expectation whose
true value is known analytically -- and it is the one place in this project where a
standard error means anything. It says nothing about statistical uncertainty in any
economic quantity, and should not be read as if it did.

    python -m src.analysis.diagnostics
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import DATA_PROCESSED, params
from ..models.hull_white import (
    HullWhite,
    calibrate_short_rate,
    monte_carlo_vs_closed_form,
)
from ..models.nss import build_curve

# The tenors the engine is checked at: short, belly, and the long end where SVB's
# book actually sat.
TEST_MATURITIES = [1.0, 5.0, 10.0, 20.0, 30.0]


def curve_fit_table() -> pd.DataFrame:
    """
    Par-yield residuals at both valuation dates, one row per published tenor.

    The residual reported is the honest one: refit a par yield from the fitted curve
    and compare it against what Treasury published, rather than comparing the fit
    against the bootstrapped zeros it was fitted through.
    """
    dates = params()["valuation_dates"]
    rows = []
    for label in ("base", "test"):
        as_of = str(dates[label])
        curve, diagnostic = build_curve(as_of)
        tenors = np.asarray(diagnostic["tenors"], dtype=float)
        par_error_bp = np.asarray(diagnostic["par_error_bp"], dtype=float)
        observed_par = np.asarray(diagnostic["par_yields"], dtype=float)

        for tenor, observed, error in zip(tenors, observed_par, par_error_bp):
            rows.append(dict(
                as_of=as_of,
                role=label,
                tenor_years=float(tenor),
                observed_par_pct=100.0 * float(observed),
                fitted_par_pct=100.0 * float(observed) + float(error) / 100.0,
                par_error_bp=float(error),
                fitted_zero_pct=100.0 * float(curve.zero_rate(tenor)),
                rmse_bp=float(curve.rmse_bp),
                max_abs_par_error_bp=float(diagnostic["max_abs_par_error_bp"]),
                max_abs_nss_vs_bootstrap_bp=float(
                    diagnostic["max_abs_nss_vs_bootstrap_bp"])))
    return pd.DataFrame(rows)


def pricing_table() -> pd.DataFrame:
    """
    Simulated discount factors against the closed-form Hull-White price.

    Run at the production path count and seed, so the figure quoted is the one the
    valuation stage actually used rather than a separately tuned run.
    """
    config = params()["hull_white"]
    curve, _ = build_curve(str(params()["valuation_dates"]["base"]))
    fit = calibrate_short_rate()
    model = HullWhite(a=fit.a, sigma=fit.sigma, curve=curve)

    table = monte_carlo_vs_closed_form(
        model, TEST_MATURITIES, int(config["paths"]),
        int(config["steps_per_year"]), int(config["seed"]))

    table.insert(0, "paths", int(config["paths"]))
    table.insert(1, "steps_per_year", int(config["steps_per_year"]))
    table["mean_reversion_a"] = float(fit.a)
    table["sigma"] = float(fit.sigma)
    table["tolerance_bp"] = float(config["mc_vs_closed_form_tolerance_bp"])
    return table


def main() -> None:
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)

    curves = curve_fit_table()
    pricing = pricing_table()

    with pd.option_context("display.width", 200):
        print("curve fit, worst par-yield residual by date")
        worst = (curves.groupby("as_of")
                 .agg(max_abs_par_error_bp=("par_error_bp",
                                            lambda s: s.abs().max()),
                      rmse_bp=("rmse_bp", "first"))
                 .reset_index())
        print(worst.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))

        print("\nMonte Carlo against closed form")
        print(pricing[["maturity", "simulated_df", "analytic_df", "error_bp",
                       "standard_error", "error_in_standard_errors"]].to_string(
            index=False, float_format=lambda v: f"{v:,.6f}"))

    worst_bp = pricing["error_bp"].abs().max()
    tolerance = float(pricing["tolerance_bp"].iloc[0])
    print(f"\nworst pricing error {worst_bp:.3f} bp against a {tolerance:.1f} bp gate")

    curves.to_csv(DATA_PROCESSED / "curve_fit_diagnostics.csv", index=False)
    pricing.to_csv(DATA_PROCESSED / "pricing_diagnostics.csv", index=False)
    print(f"written to {DATA_PROCESSED / 'curve_fit_diagnostics.csv'}")
    print(f"written to {DATA_PROCESSED / 'pricing_diagnostics.csv'}")


if __name__ == "__main__":
    main()
