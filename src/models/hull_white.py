"""
Module C. One-factor Hull-White short rate, historically calibrated.

    dr = (theta(t) - a*r) dt + sigma dW

Two things about this calibration should be said plainly, because they shape every
number downstream.

First, theta(t) is fitted analytically so the model reproduces the Module B forward
curve exactly at time zero. That part is standard and exact: the model prices today's
Treasury curve by construction, not by approximation.

Second, a and sigma are estimated from the *historical* path of the short rate, by OLS
on an Ornstein-Uhlenbeck discretisation. They are not backed out of swaption implied
volatilities, because that data is not free. This is a real modelling choice with real
consequences: a historical calibration carries no market price of interest rate risk, so
it will understate the value of the optionality in a mortgage book relative to what a
dealer would quote. The direction of that bias is known - it makes prepayment options
look cheap - and the OAS solved in Module D absorbs whatever is left over.

The model is Gaussian and will produce negative short-rate paths. Those are kept rather
than floored. Flooring inside the simulation would break the closed-form bond price the
unit test checks against, and the test is the only evidence that the engine is right.
Floors are applied where they belong economically: on the BCBS shock scenarios, which
the standard requires, and on deposit rates.

    python -m src.models.hull_white
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import params
from ..data.market_data import load_fred
from .nss import NelsonSiegelSvensson

TRADING_DAYS = 252.0


@dataclass(frozen=True)
class OrnsteinUhlenbeckFit:
    """Mean reversion speed and short-rate volatility, both annualised."""

    a: float
    sigma: float
    long_run_mean: float
    observations: int
    start: str
    end: str
    r_squared: float


def fit_ornstein_uhlenbeck(series: pd.Series, start: str, end: str,
                           sampling_days: int) -> OrnsteinUhlenbeckFit:
    """
    Regress the change in the short rate on its previous level.

    Discretising dr = a(m - r)dt + sigma dW over a step of length dt gives

        r_t - r_{t-1} = a*m*dt - a*dt*r_{t-1} + sigma*sqrt(dt)*e_t

    so the slope carries -a*dt and the residual standard deviation carries
    sigma*sqrt(dt). Both come back annualised.
    """
    window = series.loc[start:end]
    if sampling_days > 1:
        window = window.resample(f"{sampling_days}D").last().dropna()

    rates = window.to_numpy(dtype=float) / 100.0
    if len(rates) < 100:
        raise ValueError(f"only {len(rates)} observations between {start} and {end} "
                         f"at a {sampling_days}-day step; sample is too short to "
                         f"identify mean reversion")

    previous, change = rates[:-1], np.diff(rates)
    dt = sampling_days / TRADING_DAYS

    design = np.column_stack([np.ones_like(previous), previous])
    coefficients, *_ = np.linalg.lstsq(design, change, rcond=None)
    intercept, slope = coefficients

    residuals = change - design @ coefficients
    total = change - change.mean()
    r_squared = 1.0 - float(residuals @ residuals) / float(total @ total)

    a = -slope / dt
    sigma = float(residuals.std(ddof=2)) / np.sqrt(dt)
    long_run_mean = intercept / (-slope) if slope != 0 else float("nan")

    return OrnsteinUhlenbeckFit(
        a=float(a), sigma=float(sigma), long_run_mean=float(long_run_mean),
        observations=len(rates), start=str(window.index.min().date()),
        end=str(window.index.max().date()), r_squared=float(r_squared))


def calibrate_short_rate(series: pd.Series | None = None) -> OrnsteinUhlenbeckFit:
    """
    Estimate (a, sigma) on the specification the project actually uses.

    Two choices here are worth defending, because the obvious alternatives give numbers
    that are not usable.

    Sampling step. Mean reversion is a low-frequency property, and daily changes in the
    three-month bill are almost pure noise with respect to the level. Run daily, this
    regression returns an R-squared of 0.00000, a mean reversion speed of 0.002 - a
    half-life of three and a half centuries - and an implied long-run mean of 86%. None
    of that is a usable parameter; it is the regression telling us the slope is not
    identified at that frequency. At a monthly step the same estimator behaves.

    Sample window. Running 2003 to 2022 fails for a different reason: more than half of
    those years sit at the zero bound, where the level genuinely carries no information
    about the next change, and the estimate collapses again. Starting in 1990 spans four
    complete easing and tightening cycles and returns a half-life near eight years with
    a long-run mean around 1.8%, both of which are economically readable. The window
    stops at the later valuation date so nothing after a pricing date can inform a model
    used to price it.

    The residual caveat belongs in the report rather than in a comment: OLS estimates of
    mean reversion on a persistent series are biased downward in finite samples, so if
    anything this understates how fast rates revert. `main` prints the sensitivity.
    """
    config = params()["hull_white"]

    # An explicit pair short-circuits the estimate. Only the robustness sweep sets
    # these; the committed config leaves them empty so a normal run always estimates.
    forced_a, forced_sigma = config.get("override_a"), config.get("override_sigma")
    if forced_a and forced_sigma:
        return OrnsteinUhlenbeckFit(
            a=float(forced_a), sigma=float(forced_sigma), long_run_mean=float("nan"),
            observations=0, start="(overridden)", end="(overridden)",
            r_squared=float("nan"))

    if series is None:
        series = load_fred(config["short_rate_series"])

    fit = fit_ornstein_uhlenbeck(series, str(config["calibration_start"]),
                                 str(config["calibration_end"]),
                                 int(config["sampling_days"]))

    half_life = np.log(2) / fit.a if fit.a > 0 else float("inf")
    limit = float(config["max_half_life_years"])
    if not 0 < half_life <= limit:
        raise ValueError(
            f"estimated mean reversion half-life is {half_life:.1f} years "
            f"(a = {fit.a:.4f}), outside the plausible range of 0 to {limit:.0f}. "
            f"The regression has not identified mean reversion on this sample "
            f"(R^2 = {fit.r_squared:.5f}); check the sampling step and window before "
            f"using these parameters.")
    return fit


def calibration_sensitivity(series: pd.Series | None = None) -> pd.DataFrame:
    """
    The same estimator across sampling steps and windows.

    Kept as a first-class function rather than a one-off script because the instability
    it documents is a finding in its own right, and the report quotes it.
    """
    if series is None:
        series = load_fred(params()["hull_white"]["short_rate_series"])
    rows = []
    for start in ("1982-01-01", "1990-01-01", "2000-01-01", "2003-01-01"):
        for sampling_days, label in ((1, "daily"), (21, "monthly"),
                                     (63, "quarterly")):
            try:
                fit = fit_ornstein_uhlenbeck(series, start, "2022-12-31",
                                             sampling_days)
            except ValueError:
                continue
            rows.append(dict(
                window=f"{start[:4]}-2022", step=label, observations=fit.observations,
                a=fit.a, half_life_years=(np.log(2) / fit.a if fit.a > 0
                                          else float("inf")),
                sigma_bp=fit.sigma * 1e4, long_run_mean_pct=fit.long_run_mean * 100,
                r_squared=fit.r_squared))
    return pd.DataFrame(rows)


@dataclass(frozen=True)
class HullWhite:
    """
    A calibrated Hull-White model sitting on one Module B curve.

    Internally the short rate is split as r(t) = x(t) + alpha(t), where x is a driftless
    mean-reverting Gaussian starting at zero and alpha is a deterministic function that
    carries the initial term structure:

        alpha(t) = f(0,t) + sigma^2 / (2 a^2) * (1 - exp(-a t))^2

    Working with x rather than r directly means the simulation can be stepped exactly,
    with no discretisation bias in the short rate itself, and it keeps the fit to the
    initial curve exact no matter how coarse the time grid is.
    """

    a: float
    sigma: float
    curve: NelsonSiegelSvensson

    def alpha(self, t):
        """
        The deterministic part of the short rate, carrying the initial term structure.

            alpha(t) = f(0,t) + sigma^2 / (2 a^2) * (1 - exp(-a t))^2

        The second term is the convexity adjustment that keeps the model arbitrage-free;
        dropping it is a common slip and shows up as simulated discount factors that
        drift away from the fitted curve at long maturities.
        """
        t = np.asarray(t, dtype=float)
        decay = 1.0 - np.exp(-self.a * t)
        return self.curve.forward_rate(t) + (self.sigma ** 2) / (2 * self.a ** 2) * decay ** 2

    def B(self, t, T):
        """The usual Hull-White B(t,T) = (1 - exp(-a(T-t))) / a."""
        tau = np.asarray(T, dtype=float) - np.asarray(t, dtype=float)
        return (1.0 - np.exp(-self.a * tau)) / self.a

    def bond_price(self, t, T, r):
        """
        Closed-form zero-coupon bond price P(t, T) given the short rate at t.

            P(t,T) = A(t,T) exp(-B(t,T) r(t))
            ln A  = ln[P(0,T)/P(0,t)] + B(t,T) f(0,t)
                    - sigma^2 / (4a) * B(t,T)^2 * (1 - exp(-2 a t))

        This is the benchmark the Monte Carlo engine is tested against.
        """
        t = np.asarray(t, dtype=float)
        T = np.asarray(T, dtype=float)
        r = np.asarray(r, dtype=float)

        p0t = self.curve.discount(t)
        p0T = self.curve.discount(T)
        b = self.B(t, T)
        log_a = (np.log(p0T / p0t) + b * self.curve.forward_rate(t)
                 - (self.sigma ** 2) / (4 * self.a) * b ** 2
                 * (1.0 - np.exp(-2 * self.a * t)))
        # Scalar in, scalar out. `curve.discount` promotes its argument, so without the
        # squeeze a single bond price comes back as a one-element array and every
        # caller has to remember to index it.
        price = np.exp(log_a - b * r)
        return price if np.ndim(price) else float(price)

    # ------------------------------------------------------------------ simulation

    def simulate(self, horizon_years: float, steps_per_year: int, paths: int,
                 seed: int, antithetic: bool = True):
        """
        Simulate the short rate on a uniform grid, stepping x(t) exactly.

        Over one step of length d, x is Gaussian with mean x*exp(-a d) and variance
        sigma^2 (1 - exp(-2 a d)) / (2a). No Euler drift error enters the short rate.

        Antithetic pairing halves the number of independent draws for a given path
        count and cuts the standard error on every price computed from them. The same
        normals are reused across rate shocks elsewhere in the project so that finite
        differences measure curvature rather than simulation noise.

        Returns (times, short_rate, discount_factor), each shaped (paths, steps+1) for
        the two path arrays.
        """
        steps = int(round(horizon_years * steps_per_year))
        d = horizon_years / steps
        times = np.linspace(0.0, horizon_years, steps + 1)

        normals = _draw_normals(paths, steps, seed, antithetic)

        decay = np.exp(-self.a * d)
        step_sd = self.sigma * np.sqrt((1.0 - np.exp(-2 * self.a * d)) / (2 * self.a))

        x = np.zeros((paths, steps + 1))
        for i in range(steps):
            x[:, i + 1] = x[:, i] * decay + step_sd * normals[:, i]

        short_rate = x + self.alpha(times)[None, :]

        # Discounting integrates the short rate along each path. The trapezoid rule is
        # used rather than a left-endpoint sum: on a monthly grid the left-endpoint
        # version biases discount factors by several basis points at thirty years,
        # which is enough to fail the closed-form check for the wrong reason.
        integrand = 0.5 * (short_rate[:, 1:] + short_rate[:, :-1]) * d
        discount = np.exp(-np.cumsum(integrand, axis=1))
        discount = np.concatenate([np.ones((paths, 1)), discount], axis=1)

        return times, short_rate, discount


def _draw_normals(paths: int, steps: int, seed: int, antithetic: bool) -> np.ndarray:
    rng = np.random.default_rng(seed)
    if not antithetic:
        return rng.standard_normal((paths, steps))
    if paths % 2:
        raise ValueError("antithetic sampling needs an even path count")
    half = rng.standard_normal((paths // 2, steps))
    return np.concatenate([half, -half], axis=0)


def build(curve: NelsonSiegelSvensson,
          fit: OrnsteinUhlenbeckFit | None = None) -> tuple[HullWhite,
                                                            OrnsteinUhlenbeckFit]:
    fit = fit or calibrate_short_rate()
    return HullWhite(a=fit.a, sigma=fit.sigma, curve=curve), fit


# ------------------------------------------------------------------------ the test

def monte_carlo_vs_closed_form(model: HullWhite, maturities, paths: int,
                               steps_per_year: int, seed: int) -> pd.DataFrame:
    """
    The engine's correctness check: does E[exp(-int_0^T r ds)] equal P(0,T)?

    If simulated discount factors do not average to the curve the model was built on,
    nothing priced with this engine can be trusted, so this runs before anything else
    uses it. The comparison is reported in basis points of continuously compounded
    yield, which is the scale the rest of the project thinks in.
    """
    maturities = np.atleast_1d(np.asarray(maturities, dtype=float))
    horizon = float(maturities.max())
    times, _, discount = model.simulate(horizon, steps_per_year, paths, seed)

    rows = []
    for maturity in maturities:
        index = int(np.argmin(np.abs(times - maturity)))
        sample = discount[:, index]
        simulated = float(sample.mean())
        standard_error = float(sample.std(ddof=1) / np.sqrt(len(sample)))
        analytic = float(model.curve.discount(maturity))

        simulated_yield = -np.log(simulated) / maturity
        analytic_yield = -np.log(analytic) / maturity
        rows.append(dict(
            maturity=maturity, simulated_df=simulated, analytic_df=analytic,
            standard_error=standard_error,
            error_bp=(simulated_yield - analytic_yield) * 1e4,
            error_in_standard_errors=((simulated - analytic) / standard_error
                                      if standard_error else np.nan)))
    return pd.DataFrame(rows)


def main() -> None:
    from .nss import build_curve

    config = params()
    hw_config = config["hull_white"]

    print("Ornstein-Uhlenbeck estimates across sampling steps and windows")
    print(calibration_sensitivity().to_string(
        index=False, float_format=lambda v: f"{v:,.4f}"))

    fit = calibrate_short_rate()
    print(f"\nChosen specification: {hw_config['short_rate_series']}, "
          f"{fit.start} to {fit.end}, {hw_config['sampling_days']}-day step "
          f"({fit.observations:,} observations)")
    print(f"  mean reversion a = {fit.a:.4f}   (half-life "
          f"{np.log(2) / fit.a:.2f} years)")
    print(f"  volatility sigma = {fit.sigma:.5f}   ({fit.sigma * 1e4:.0f} bp per year)")
    print(f"  long-run mean    = {fit.long_run_mean * 100:.2f}%")
    print(f"  regression R^2   = {fit.r_squared:.5f}")

    for as_of in (config["valuation_dates"]["base"], config["valuation_dates"]["test"]):
        curve, _ = build_curve(str(as_of))
        model, _ = build(curve, fit)
        print(f"\n{'=' * 72}\nMonte Carlo against closed form, {as_of}")
        for paths in (2000, 8000, hw_config["paths"]):
            table = monte_carlo_vs_closed_form(
                model, [1, 5, 10, 20, 30], paths,
                hw_config["steps_per_year"], hw_config["seed"])
            worst = table["error_bp"].abs().max()
            print(f"  {paths:>6,} paths   worst |error| = {worst:6.3f} bp   "
                  + "  ".join(f"{int(r.maturity)}y:{r.error_bp:+.2f}"
                              for r in table.itertuples()))


if __name__ == "__main__":
    main()
