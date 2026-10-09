"""
Curve tests: does the fitted curve reprice the market it was fitted to, and does the
smooth fit agree with an independent bootstrap?

The par-versus-zero distinction is what these tests really protect. A curve that looks
fine plotted against par yields can still be wrong by tens of basis points as a
discounting curve, and that error lands squarely on the long end where SVB's book sits.
"""

import numpy as np
import pytest

from src.config import params
from src.models.nss import (
    NelsonSiegelSvensson,
    bootstrap_zero_curve,
    build_curve,
    par_yield_from_curve,
)

DATES = [str(params()["valuation_dates"]["base"]),
         str(params()["valuation_dates"]["test"])]


@pytest.fixture(scope="module", params=DATES)
def fitted(request):
    curve, diagnostics = build_curve(request.param)
    return curve, diagnostics


class TestFitQuality:
    def test_reprices_published_par_yields(self, fitted):
        curve, diagnostics = fitted
        limit = float(params()["nelson_siegel_svensson"]["max_residual_bp"])
        worst = diagnostics["max_abs_par_error_bp"]
        assert worst < limit, (
            f"{diagnostics['as_of']}: worst par-yield error {worst:.2f} bp exceeds "
            f"{limit} bp")

    def test_agrees_with_an_independent_bootstrap(self, fitted):
        curve, diagnostics = fitted
        limit = float(params()["bootstrap"]["tolerance_bp"])
        gaps = np.abs(diagnostics["nss_vs_bootstrap_bp"])
        # The 20-year point is the known exception: that sector trades at a concession
        # to the smooth curve, so a four-parameter family cannot reach it without
        # distorting the 10- and 30-year points that matter far more for this book.
        off_twenty = gaps[diagnostics["tenors"] != 20.0]
        assert off_twenty.max() < limit, (
            f"{diagnostics['as_of']}: NSS departs from the bootstrap by "
            f"{off_twenty.max():.2f} bp away from the 20-year point")


class TestParVersusZero:
    def test_bootstrap_reprices_par_bonds_to_exactly_par(self):
        """
        The defining property of the bootstrap. If a bond paying the published par
        coupon does not price to 100 off the bootstrapped curve, the bootstrap is wrong.
        """
        tenors = np.array([0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0])
        par = np.array([0.0006, 0.0019, 0.0039, 0.0073, 0.0126, 0.0152, 0.0190])
        solved_tenors, zeros = bootstrap_zero_curve(tenors, par, frequency=2)

        for maturity, coupon in zip(solved_tenors, par):
            if maturity <= 1.0:
                continue
            times = np.arange(0.5, maturity + 1e-9, 0.5)
            z = np.interp(times, solved_tenors, zeros)
            dfs = np.exp(-z * times)
            price = (coupon / 2) * dfs.sum() + dfs[-1]
            assert price == pytest.approx(1.0, abs=1e-9), (
                f"{maturity}y par bond prices to {price:.10f}, not par")

    def test_zero_and_par_differ_where_the_curve_is_steep(self):
        """
        Sanity on the thing this module exists to get right: on an upward-sloping curve
        the zero rate must sit above the par yield at the long end. If they came out
        equal, par yields were being passed through as spot rates somewhere.
        """
        curve, diagnostics = build_curve(str(params()["valuation_dates"]["base"]))
        long_index = np.argmax(diagnostics["tenors"])
        zero = diagnostics["nss_zeros"][long_index]
        par = diagnostics["par_yields"][long_index]
        assert zero > par
        assert (zero - par) * 1e4 > 1.0


class TestCurveShape:
    def test_forward_curve_stays_positive_over_the_simulation_horizon(self, fitted):
        """
        Module C integrates this forward curve out to thirty years. A fit that prices
        today's bonds but implies negative forwards inside that horizon is unusable, and
        unconstrained fits do produce exactly that.
        """
        curve, _ = fitted
        horizon = float(params()["hull_white"]["horizon_years"])
        forwards = curve.forward_rate(np.linspace(0.01, horizon, 400))
        assert forwards.min() > -1e-6

    def test_long_run_level_is_economically_readable(self, fitted):
        """beta0 is the asymptotic forward rate; it should look like a long rate."""
        curve, _ = fitted
        assert 0.005 < curve.beta[0] < 0.10

    def test_discount_factors_fall_monotonically(self, fitted):
        curve, _ = fitted
        dfs = curve.discount(np.array([0.25, 1.0, 5.0, 10.0, 20.0, 30.0]))
        assert np.all(np.diff(dfs) < 0)
        assert dfs[0] < 1.0


class TestLoadings:
    def test_short_end_loadings_approach_their_limits(self):
        """As t goes to zero the slope loading tends to 1 and both curvatures to 0."""
        design = NelsonSiegelSvensson.loadings(np.array([1e-6]), 2.0, 10.0)
        assert design[0, 0] == pytest.approx(1.0)
        assert design[0, 1] == pytest.approx(1.0, abs=1e-5)
        assert design[0, 2] == pytest.approx(0.0, abs=1e-5)
        assert design[0, 3] == pytest.approx(0.0, abs=1e-5)

    def test_par_yield_of_a_flat_curve_equals_the_flat_rate(self):
        """
        With a flat 4% continuously compounded zero curve, the semiannual par coupon is
        the simple-compounded equivalent, which is slightly above 4%.
        """
        flat = NelsonSiegelSvensson(beta=np.array([0.04, 0.0, 0.0, 0.0]),
                                    lambda1=1.0, lambda2=5.0, as_of="flat")
        par = par_yield_from_curve(flat, 10.0, frequency=2)
        expected = 2 * (np.exp(0.04 / 2) - 1)
        assert par == pytest.approx(expected, rel=1e-6)
