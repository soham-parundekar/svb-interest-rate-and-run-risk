"""
Module B. Treasury par yields in, a usable zero and forward curve out.

The order of operations here is the whole point. Treasury publishes *par* yields: the
coupon a bond of that maturity would need to trade at 100. Discounting a cash flow with
a par yield as though it were a spot rate is the standard way this kind of
reconstruction goes quietly wrong, and the error grows with maturity and curve slope,
which is exactly where SVB's book sits.

So the curve is built in three steps:

  1. Bootstrap zero-coupon rates from the published par yields, sequentially, solving
     each node so a par bond of that maturity prices to exactly 100.
  2. Fit Nelson-Siegel-Svensson to those zeros. With the two decay parameters fixed the
     four betas are linear, so the inner solve is ordinary least squares and the outer
     search is a grid over (lambda1, lambda2).
  3. Round-trip: price par bonds off the fitted curve and compare the implied par yields
     against what Treasury actually published. That residual, in basis points, is the
     honest measure of curve quality, and it is the gate the tests enforce.

Zero rates are continuously compounded throughout, which is what Module C's Hull-White
calibration expects.

    python -m src.models.nss
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import brentq, least_squares

from ..config import params
from ..data.market_data import curve_on

# Treasury quotes bills on a coupon-equivalent basis. Anything maturing inside a year
# has a single cash flow, so it is discounted simple rather than as a coupon bond.
SINGLE_PAYMENT_MAX_YEARS = 1.0

COUPON_EPS = 1e-12


def _match_shape(values: np.ndarray, original):
    """Return a scalar when the caller passed a scalar, an array when it passed one."""
    return values if np.ndim(original) else float(values[0])


@dataclass(frozen=True)
class NelsonSiegelSvensson:
    """
    A fitted zero curve.

        z(t) = b0
             + b1 * (1 - exp(-t/L1)) / (t/L1)
             + b2 * [(1 - exp(-t/L1)) / (t/L1) - exp(-t/L1)]
             + b3 * [(1 - exp(-t/L2)) / (t/L2) - exp(-t/L2)]

    Because z(t) is the average of the instantaneous forward curve over [0, t], the
    forward has a closed form under the same parameters. Hull-White needs that forward
    curve to fit theta(t) analytically, which is the reason for choosing this family
    over a spline.
    """

    beta: np.ndarray      # b0, b1, b2, b3
    lambda1: float
    lambda2: float
    as_of: str
    rmse_bp: float = float("nan")

    @staticmethod
    def loadings(tenors: np.ndarray, lambda1: float, lambda2: float) -> np.ndarray:
        """Design matrix for the zero curve; columns match beta."""
        t = np.asarray(tenors, dtype=float)
        t = np.where(t <= 0, 1e-8, t)
        x1, x2 = t / lambda1, t / lambda2
        decay1, decay2 = np.exp(-x1), np.exp(-x2)
        slope1 = (1.0 - decay1) / x1
        slope2 = (1.0 - decay2) / x2
        return np.column_stack([np.ones_like(t), slope1, slope1 - decay1,
                                slope2 - decay2])

    def zero_rate(self, tenors):
        """Continuously compounded zero rate. Scalar in, scalar out."""
        values = self.loadings(np.atleast_1d(tenors), self.lambda1,
                               self.lambda2) @ self.beta
        return _match_shape(values, tenors)

    def discount(self, tenors):
        t = np.atleast_1d(np.asarray(tenors, dtype=float))
        values = np.exp(-np.atleast_1d(self.zero_rate(t)) * t)
        return _match_shape(values, tenors)

    def forward_rate(self, tenors) -> np.ndarray:
        """
        Instantaneous forward f(t) = d/dt [t * z(t)].

            f(t) = b0 + b1*exp(-t/L1) + b2*(t/L1)*exp(-t/L1) + b3*(t/L2)*exp(-t/L2)
        """
        t = np.atleast_1d(np.asarray(tenors, dtype=float))
        t = np.where(t < 0, 0.0, t)
        x1, x2 = t / self.lambda1, t / self.lambda2
        d1, d2 = np.exp(-x1), np.exp(-x2)
        b0, b1, b2, b3 = self.beta
        values = b0 + b1 * d1 + b2 * x1 * d1 + b3 * x2 * d2
        return _match_shape(values, tenors)


# ----------------------------------------------------------------------- bootstrap

def _coupon_dates(maturity: float, frequency: int) -> np.ndarray:
    """Payment times for a par bond, counting back from maturity."""
    step = 1.0 / frequency
    count = int(round(maturity * frequency))
    return np.array([maturity - step * i for i in range(count - 1, -1, -1)])


def bootstrap_zero_curve(tenors: np.ndarray, par_yields: np.ndarray,
                         frequency: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """
    Zero rates implied by the published par curve, solved one node at a time.

    Short tenors are single-payment instruments, so their zero rate falls out directly.
    Beyond a year, each par bond pays coupons at dates that fall between nodes already
    solved, so the unknown long zero is found by root-search with the intermediate
    discount factors taken from linear interpolation on zero rates. That interpolation
    is a choice; its effect is checked by the round-trip residual, which comes out in
    single basis points.

    Returns continuously compounded zero rates on the same tenor grid.
    """
    order = np.argsort(tenors)
    tenors, par_yields = np.asarray(tenors)[order], np.asarray(par_yields)[order]
    zeros = np.full_like(tenors, np.nan, dtype=float)

    known_t: list[float] = []
    known_z: list[float] = []

    for i, (maturity, par) in enumerate(zip(tenors, par_yields)):
        if maturity <= SINGLE_PAYMENT_MAX_YEARS:
            # One payment of (1 + y*T) at T, priced at par.
            zeros[i] = np.log(1.0 + par * maturity) / maturity
        else:
            times = _coupon_dates(maturity, frequency)
            coupon = par / frequency

            def par_price_error(z_long: float) -> float:
                # Coupon dates between the last solved node and this maturity are
                # interpolated against the TRIAL long rate, not against the previously
                # solved nodes alone. Interpolating without it leaves the intermediate
                # dates extrapolated flat off the last known node, and the resulting
                # curve then misprices its own par bonds by about half a basis point -
                # small, but it means the bootstrap no longer satisfies the one property
                # that defines it.
                grid_t = known_t + [float(maturity)]
                grid_z = known_z + [float(z_long)]
                z = np.interp(times, grid_t, grid_z, left=grid_z[0])
                dfs = np.exp(-z * times)
                return coupon * dfs.sum() + dfs[-1] - 1.0

            # The zero rate sits near the par yield; a wide bracket keeps the solve
            # robust when the curve is steep or inverted.
            zeros[i] = brentq(par_price_error, -0.02, 0.25, xtol=1e-14, maxiter=200)

        known_t.append(float(maturity))
        known_z.append(float(zeros[i]))

    return tenors, zeros


def par_yield_from_curve(curve: NelsonSiegelSvensson, maturity: float,
                         frequency: int = 2) -> float:
    """The coupon that prices a bond of this maturity to par off the fitted curve."""
    if maturity <= SINGLE_PAYMENT_MAX_YEARS:
        df = float(curve.discount(maturity))
        return (1.0 / df - 1.0) / maturity
    times = _coupon_dates(maturity, frequency)
    dfs = curve.discount(times)
    annuity = dfs.sum() / frequency
    if annuity < COUPON_EPS:
        return float("nan")
    return float((1.0 - dfs[-1]) / annuity)


# ----------------------------------------------------------------------------- fit

def grid_fit_zeros(tenors: np.ndarray, zero_rates: np.ndarray,
                   lambda1_grid: np.ndarray, lambda2_grid: np.ndarray
                   ) -> tuple[np.ndarray, float, float]:
    """
    Starting point for the real fit: least squares against bootstrapped zero rates.

    For a fixed pair of decay parameters the model is linear in the betas, so the inner
    problem solves exactly with `lstsq` and only the two lambdas need searching. This is
    fast and lands close enough for the refinement below to converge reliably, which
    matters because the par-yield objective has local minima.
    """
    best = None
    for lambda1 in lambda1_grid:
        for lambda2 in lambda2_grid:
            # The two humps collapse onto each other when the decays coincide, which
            # leaves the design matrix rank-deficient.
            if abs(lambda1 - lambda2) < 1e-6:
                continue
            design = NelsonSiegelSvensson.loadings(tenors, lambda1, lambda2)
            beta, *_ = np.linalg.lstsq(design, zero_rates, rcond=None)
            residual = design @ beta - zero_rates
            sse = float(residual @ residual)
            if best is None or sse < best[0]:
                best = (sse, beta, lambda1, lambda2)
    _, beta, lambda1, lambda2 = best
    return beta, lambda1, lambda2


def fit_nss(tenors: np.ndarray, zero_rates: np.ndarray, par_yields: np.ndarray,
            lambda1_grid: np.ndarray, lambda2_grid: np.ndarray,
            frequency: int = 2, as_of: str = "") -> NelsonSiegelSvensson:
    """
    Fit NSS so the curve reprices the par bonds Treasury actually quoted.

    Fitting to the bootstrapped zeros instead would be easier - it is linear in the
    betas - but it optimises the wrong thing. The bootstrap is an exact, unsmoothed
    transform of the quotes, so its long end inherits every kink in the 20- and 30-year
    sector; a least-squares fit to those zeros spends its flexibility matching curve
    noise and then misprices the 10-year par bond by double digits in basis points.

    Minimising par-yield error directly puts the error where it is observable and keeps
    it honest: the residual reported is the same number a reviewer would compute by
    pricing a bond off this curve and comparing it with the screen.

    The grid fit above supplies the starting point; `least_squares` then moves all six
    parameters together.
    """
    def residuals(theta: np.ndarray) -> np.ndarray:
        candidate = NelsonSiegelSvensson(beta=theta[:4], lambda1=theta[4],
                                         lambda2=theta[5], as_of=as_of)
        implied = np.array([par_yield_from_curve(candidate, float(t), frequency)
                            for t in tenors])
        return (implied - par_yields) * 1e4      # basis points

    # Bounds keep the parameters economically readable as well as well-fitting. Left
    # free, the solver finds offsetting solutions - beta0 of -36% against beta3 of +100%
    # with a 25-year decay - that reprice every quoted tenor to within a basis point and
    # then extrapolate to nonsense. beta0 is the asymptotic forward rate, so it is held
    # inside a plausible long-rate range.
    lower = np.array([0.0, -0.20, -0.20, -0.20, 0.05, 0.05])
    upper = np.array([0.15, 0.20, 0.20, 0.20, 30.0, 30.0])

    # Several starting points, because the par-yield objective is not convex and a
    # single start lands on a boundary solution: the 2021 curve pins beta0 at zero,
    # which says the long-run forward rate is zero and sends the forward curve negative
    # past forty years.
    short_rate, long_rate = float(zero_rates[0]), float(zero_rates[-1])
    grid_beta, grid_lambda1, grid_lambda2 = grid_fit_zeros(
        tenors, zero_rates, lambda1_grid, lambda2_grid)
    seeds = [np.concatenate([grid_beta, [grid_lambda1, grid_lambda2]])]
    for lam1, lam2 in ((1.0, 8.0), (2.0, 15.0), (0.5, 5.0), (3.0, 20.0)):
        # Textbook reading of the parameters: level, slope, two curvature terms.
        seeds.append(np.array([long_rate, short_rate - long_rate, 0.0, 0.0,
                               lam1, lam2]))

    horizon = np.linspace(0.01, params()["hull_white"]["horizon_years"], 400)
    best = None
    for seed in seeds:
        start = np.clip(seed, lower + 1e-9, upper - 1e-9)
        try:
            solution = least_squares(residuals, start, bounds=(lower, upper),
                                     xtol=1e-12, ftol=1e-12, max_nfev=20000)
        except ValueError:
            continue
        candidate = NelsonSiegelSvensson(
            beta=solution.x[:4], lambda1=float(solution.x[4]),
            lambda2=float(solution.x[5]), as_of=as_of,
            rmse_bp=float(np.sqrt(np.mean(solution.fun ** 2))))

        # A curve that prices today's bonds but implies negative forward rates inside
        # the simulation horizon is not usable: Module C integrates that forward curve
        # out to thirty years to fit theta(t).
        usable = bool((candidate.forward_rate(horizon) >= -1e-6).all())
        score = (not usable, candidate.rmse_bp)
        if best is None or score < best[0]:
            best = (score, candidate)

    return best[1]


def build_curve(as_of: str) -> tuple[NelsonSiegelSvensson, dict]:
    """
    Fit the curve for one valuation date and report how well it reprices the market.

    The diagnostics returned are the ones worth looking at: the fit residual against the
    bootstrapped zeros, and separately the round-trip par-yield error, which is what a
    reviewer would actually challenge.
    """
    config = params()["nelson_siegel_svensson"]
    snapshot = curve_on(as_of)

    excluded = set(config.get("exclude_tenors") or [])
    keep = ~snapshot["tenor_years"].round(10).isin(
        [round(float(t), 10) for t in excluded])
    snapshot = snapshot[keep]

    tenors = snapshot["tenor_years"].to_numpy(dtype=float)
    par_yields = snapshot["par_yield_pct"].to_numpy(dtype=float) / 100.0

    boot_tenors, boot_zeros = bootstrap_zero_curve(
        tenors, par_yields, frequency=params()["bootstrap"]["coupon_frequency"])

    lo1, hi1, n1 = config["lambda1_grid"]
    lo2, hi2, n2 = config["lambda2_grid"]
    curve = fit_nss(boot_tenors, boot_zeros, par_yields,
                    np.linspace(lo1, hi1, int(n1)), np.linspace(lo2, hi2, int(n2)),
                    frequency=params()["bootstrap"]["coupon_frequency"],
                    as_of=str(as_of))

    implied_par = np.array([par_yield_from_curve(curve, float(t))
                            for t in boot_tenors])
    par_error_bp = (implied_par - par_yields) * 1e4
    nss_vs_boot_bp = (curve.zero_rate(boot_tenors) - boot_zeros) * 1e4

    diagnostics = dict(
        as_of=str(as_of),
        curve_date=str(snapshot["date"].iloc[0].date()),
        tenors=boot_tenors,
        par_yields=par_yields,
        bootstrap_zeros=boot_zeros,
        nss_zeros=curve.zero_rate(boot_tenors),
        par_error_bp=par_error_bp,
        max_abs_par_error_bp=float(np.abs(par_error_bp).max()),
        nss_vs_bootstrap_bp=nss_vs_boot_bp,
        max_abs_nss_vs_bootstrap_bp=float(np.abs(nss_vs_boot_bp).max()),
    )
    return curve, diagnostics


def main() -> None:
    config = params()
    for as_of in (config["valuation_dates"]["base"], config["valuation_dates"]["test"]):
        curve, diag = build_curve(str(as_of))
        print(f"\n{'=' * 72}\n{as_of}  (Treasury curve dated {diag['curve_date']})")
        print(f"  lambda1={curve.lambda1:.3f}  lambda2={curve.lambda2:.3f}  "
              f"beta={np.array2string(curve.beta, precision=5)}")
        print(f"  par-yield fit RMSE:             {curve.rmse_bp:.2f} bp")
        print(f"  worst round-trip par error:     "
              f"{diag['max_abs_par_error_bp']:.2f} bp")
        print(f"  worst NSS vs bootstrap gap:     "
              f"{diag['max_abs_nss_vs_bootstrap_bp']:.2f} bp")
        print(f"\n  {'tenor':>7} {'par obs':>9} {'par fit':>9} {'err bp':>8} "
              f"{'boot z':>9} {'nss z':>9}")
        for t, po, pf, e, bz, nz in zip(
                diag["tenors"], diag["par_yields"] * 100,
                (diag["par_yields"] * 1e4 + diag["par_error_bp"]) / 100,
                diag["par_error_bp"], diag["bootstrap_zeros"] * 100,
                diag["nss_zeros"] * 100):
            print(f"  {t:7.3f} {po:9.3f} {pf:9.3f} {e:8.2f} {bz:9.3f} {nz:9.3f}")


if __name__ == "__main__":
    main()
