"""
The correctness test for the short-rate engine.

If simulated discount factors do not average back to the curve the model was built on,
every price computed downstream is wrong in a way no amount of calibration will reveal.
So this is the test that gates the rest: Monte Carlo against the closed-form Hull-White
bond price, checked for convergence as the path count grows.
"""

import numpy as np
import pytest

from src.config import params
from src.models.hull_white import (
    HullWhite,
    calibrate_short_rate,
    monte_carlo_vs_closed_form,
)
from src.models.nss import build_curve

MATURITIES = [1.0, 5.0, 10.0, 20.0, 30.0]


@pytest.fixture(scope="module")
def model() -> HullWhite:
    curve, _ = build_curve(str(params()["valuation_dates"]["base"]))
    fit = calibrate_short_rate()
    return HullWhite(a=fit.a, sigma=fit.sigma, curve=curve)


class TestCalibration:
    def test_mean_reversion_is_economically_plausible(self):
        """
        Guards the specification choice. Run daily on the 2003-2022 window the same
        estimator returns a half-life of 365 years, which is the regression failing to
        identify anything rather than a fact about interest rates.
        """
        fit = calibrate_short_rate()
        half_life = np.log(2) / fit.a
        assert 1.0 < half_life < 25.0
        assert 0.0 < fit.long_run_mean < 0.10
        assert 0.001 < fit.sigma < 0.05


class TestMonteCarloAgainstClosedForm:
    def test_discount_factors_match_the_initial_curve(self, model):
        config = params()["hull_white"]
        table = monte_carlo_vs_closed_form(
            model, MATURITIES, int(config["paths"]),
            int(config["steps_per_year"]), int(config["seed"]))
        tolerance = float(config["mc_vs_closed_form_tolerance_bp"])
        worst = table["error_bp"].abs().max()
        assert worst < tolerance, (
            f"worst Monte Carlo pricing error {worst:.3f} bp exceeds {tolerance} bp\n"
            f"{table.to_string(index=False)}")

    def test_error_shrinks_as_paths_grow(self, model):
        """
        Convergence, not just accuracy at one path count. A bias in the discretisation
        would hold the error roughly constant as paths increase; sampling noise falls.
        """
        config = params()["hull_white"]
        errors = []
        for paths in (2000, 8000, 32000):
            table = monte_carlo_vs_closed_form(
                model, MATURITIES, paths, int(config["steps_per_year"]),
                int(config["seed"]))
            errors.append(table["error_bp"].abs().max())
        assert errors[-1] < errors[0], f"no convergence across path counts: {errors}"
        assert errors[-1] < 0.5

    def test_simulated_prices_sit_within_sampling_error(self, model):
        config = params()["hull_white"]
        table = monte_carlo_vs_closed_form(
            model, MATURITIES, int(config["paths"]),
            int(config["steps_per_year"]), int(config["seed"]))
        worst = table["error_in_standard_errors"].abs().max()
        assert worst < 4.0, (
            f"simulated discount factor sits {worst:.1f} standard errors from the "
            f"analytic price\n{table.to_string(index=False)}")


class TestBondPriceAnalytics:
    def test_prices_today_curve_exactly_at_time_zero(self, model):
        """At t=0 the closed form must collapse onto the fitted discount curve."""
        for maturity in MATURITIES:
            r0 = float(model.curve.forward_rate(0.0))
            analytic = float(model.bond_price(0.0, maturity, r0))
            curve = float(model.curve.discount(maturity))
            assert analytic == pytest.approx(curve, rel=1e-10)

    def test_price_falls_as_the_short_rate_rises(self, model):
        rates = np.array([0.00, 0.02, 0.05])
        prices = np.array([float(model.bond_price(1.0, 11.0, r)) for r in rates])
        assert np.all(np.diff(prices) < 0)

    def test_B_is_monotone_and_bounded_by_one_over_a(self, model):
        tenors = np.array([0.5, 1.0, 5.0, 10.0, 30.0])
        b = model.B(0.0, tenors)
        assert np.all(np.diff(b) > 0)
        assert np.all(b < 1.0 / model.a)
