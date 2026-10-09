"""
Module D, mortgage half. Pathwise valuation of agency pass-throughs.

This is where SVB's problem actually lives. At end-2021 the bank held $64bn of
agency MBS yielding 1.55% and another $25bn of CMO and CMBS on top. Those are
2020 and 2021 vintage pools: their borrowers are paying around 2%, so once the
mortgage rate went through 5% nobody refinanced, the pools stopped returning
principal early, and the effective life of the book stretched out exactly when
the bank needed it to shorten. A model that prices these as bullets to their
contractual maturity misses the mechanism; one that assumes a constant
prepayment speed misses it too, because the speed is the thing that changes.

So prepayment is modelled as a function of refinancing incentive, the incentive
is computed along each simulated rate path, and the resulting cash flows are
discounted on the same path. The prepayment option is worth what it is worth
because it is exercised in the states where rates fell.

The S-curve parameters are stylised. They are not an agency model and not a
dealer model, and they are held fixed across both banks and both valuation
dates so that nothing in the out-of-sample test can be tuned after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import params
from ..data.market_data import load_fred
from .hull_white import HullWhite

MONTHS_PER_YEAR = 12


@dataclass(frozen=True)
class PrepaymentModel:
    """
    Conditional prepayment rate as a logistic function of refinancing incentive.

        CPR(I) = cpr_min + (cpr_max - cpr_min) / (1 + exp(-k (I - I0)))
        SMM    = 1 - (1 - CPR)^(1/12)

    I is the borrower's coupon less the prevailing mortgage rate, in decimal. A
    borrower paying 2% against a 6% market rate has an incentive of -4% and is
    not going anywhere; the curve floors at cpr_min, which stands for the moves,
    defaults and curtailments that happen whatever rates do. A borrower 2% in
    the money refinances fast, and the curve approaches cpr_max.
    """

    cpr_min: float
    cpr_max: float
    midpoint: float
    steepness: float

    @classmethod
    def from_config(cls) -> "PrepaymentModel":
        config = params()["mbs"]
        return cls(cpr_min=float(config["cpr_min"]), cpr_max=float(config["cpr_max"]),
                   midpoint=float(config["incentive_midpoint"]),
                   steepness=float(config["steepness"]))

    def cpr(self, incentive):
        """Annual prepayment rate. `incentive` is in decimal, so 0.01 is one point."""
        x = self.steepness * (np.asarray(incentive, dtype=float) - self.midpoint) * 100
        return self.cpr_min + (self.cpr_max - self.cpr_min) / (1.0 + np.exp(-x))

    def smm(self, incentive):
        """Single monthly mortality implied by the annual rate."""
        return 1.0 - (1.0 - self.cpr(incentive)) ** (1.0 / MONTHS_PER_YEAR)


def mortgage_spread(curve, as_of: str) -> float:
    """
    Spread of the primary mortgage rate over the model's own ten-year zero rate.

    Anchored at the valuation date rather than averaged over history. Freddie Mac's
    survey rate is observable market data on the same footing as the Treasury curve, and
    the spread is not stable enough to average: it ran 1.59% at end-2021 and 2.59% at
    end-2022, as the Fed stopped buying MBS and rate volatility rose. Using a single
    sample-average spread would put the end-2022 mortgage rate 75bp too low, understate
    how far out of the money these pools were, and flatter the prediction the whole
    project is being judged on.

    Held constant along simulated paths, which assumes the primary-secondary spread does
    not itself move with rates. That is a simplification and it is listed as one.
    """
    survey = load_fred(params()["mbs"]["mortgage_spread_source"])
    observed = survey.loc[:as_of]
    if observed.empty:
        raise LookupError(f"no mortgage survey rate published on or before {as_of}")
    latest = float(observed.iloc[-1]) / 100.0
    return latest - float(curve.zero_rate(10.0))


@dataclass
class MortgagePool:
    """One bucket of agency pass-throughs, treated as a single representative pool."""

    balance: float
    coupon: float            # rate paid through to the investor
    wac: float               # rate the borrower pays; coupon plus servicing and g-fee
    remaining_months: int

    @classmethod
    def from_position(cls, balance: float, book_yield: float,
                      bucket: str | None = None,
                      remaining_years: float | None = None) -> "MortgagePool":
        """
        Build the representative pool for one maturity bucket.

        The bucket decides the remaining life. Pass-throughs reported in the under-one-
        year bucket are pools that have nearly run off, and pricing them as fresh
        thirty-year collateral overstates their duration by an order of magnitude.
        """
        config = params()["mbs"]
        if remaining_years is None:
            by_bucket = config.get("bucket_wam_years") or {}
            remaining_years = float(by_bucket.get(
                bucket, config["weighted_average_maturity_years"]))
        return cls(balance=balance, coupon=book_yield,
                   wac=book_yield + float(config["servicing_and_guarantee_fee"]),
                   remaining_months=max(int(round(remaining_years * MONTHS_PER_YEAR)), 1))


def ten_year_zero_along_paths(model: HullWhite, times: np.ndarray,
                              short_rate: np.ndarray) -> np.ndarray:
    """
    The ten-year zero rate at each date on each path, from the closed-form bond price.

    B(t, t+10) depends only on the tenor, so it is a constant here; everything else in
    ln A is deterministic in t. That makes the whole surface a rank-one update of the
    simulated short rate and costs one vector operation instead of a nested loop.
    """
    tenor = 10.0
    b = (1.0 - np.exp(-model.a * tenor)) / model.a
    p0t = model.curve.discount(times)
    p0T = model.curve.discount(times + tenor)
    log_a = (np.log(p0T / p0t) + b * model.curve.forward_rate(times)
             - (model.sigma ** 2) / (4 * model.a) * b ** 2
             * (1.0 - np.exp(-2 * model.a * times)))
    log_price = log_a[None, :] - b * short_rate
    return -log_price / tenor


def pool_cash_flows(pool: MortgagePool, prepayment: PrepaymentModel,
                    mortgage_rate: np.ndarray) -> np.ndarray:
    """
    Monthly cash flow per path: scheduled interest, scheduled principal, prepayment.

    Amortisation runs on the WAC, because that is the rate the borrower's level payment
    is computed from, while the investor receives the pass-through coupon. Prepayment is
    applied to the balance remaining *after* the scheduled principal for that month,
    which is the standard convention and avoids double-counting the same dollar.

    `mortgage_rate` is shaped (paths, months). The return is the same shape.
    """
    paths, months = mortgage_rate.shape
    horizon = min(months, pool.remaining_months)

    balance = np.full(paths, pool.balance, dtype=float)
    flows = np.zeros((paths, months), dtype=float)

    monthly_wac = pool.wac / MONTHS_PER_YEAR
    monthly_coupon = pool.coupon / MONTHS_PER_YEAR

    for month in range(horizon):
        remaining_term = pool.remaining_months - month
        if remaining_term <= 0:
            break

        # Level payment on the current balance over the remaining term. The zero-rate
        # branch matters: a pool whose WAC rounds to zero would otherwise divide by
        # zero rather than amortise linearly.
        if monthly_wac > 1e-12:
            growth = (1.0 + monthly_wac) ** remaining_term
            payment = balance * monthly_wac * growth / (growth - 1.0)
            scheduled_interest = balance * monthly_wac
        else:
            payment = balance / remaining_term
            scheduled_interest = np.zeros_like(balance)

        scheduled_principal = np.minimum(payment - scheduled_interest, balance)
        after_scheduled = balance - scheduled_principal

        incentive = pool.wac - mortgage_rate[:, month]
        prepaid = after_scheduled * prepayment.smm(incentive)

        investor_interest = balance * monthly_coupon
        flows[:, month] = investor_interest + scheduled_principal + prepaid
        balance = after_scheduled - prepaid

    return flows


def price_from_flows(flows: np.ndarray, discount: np.ndarray, times: np.ndarray,
                     oas: float) -> float:
    """
    Present value of pathwise cash flows at a constant option-adjusted spread.

    The OAS is applied on top of the simulated path discount factor, so it carries
    everything the rate model does not: liquidity, the gap between a historical and a
    risk-neutral calibration of rate volatility, and whatever the stylised prepayment
    curve gets wrong about this particular book.
    """
    spread_discount = discount * np.exp(-oas * times)[None, :]
    return float((flows * spread_discount).sum(axis=1).mean())


def monthly_grid(model: HullWhite, horizon_years: float, paths: int, seed: int
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Simulate on a monthly grid and return the cash-flow dates only (dropping t=0)."""
    times, short_rate, discount = model.simulate(
        horizon_years, MONTHS_PER_YEAR, paths, seed)
    return times[1:], short_rate[:, 1:], discount[:, 1:]


def value_mbs(model: HullWhite, pool: MortgagePool, prepayment: PrepaymentModel,
              spread_over_ten_year: float, oas: float, paths: int, seed: int,
              horizon_years: float | None = None) -> dict:
    """
    Value one pool and report the diagnostics that make the number interpretable.

    Average life is the balance-weighted time to principal return and is the single
    most useful number here: it is what extension risk does to a mortgage book, and it
    is what a reader will want to compare between end-2021 and end-2022.
    """
    horizon = horizon_years or pool.remaining_months / MONTHS_PER_YEAR
    times, short_rate, discount = monthly_grid(model, horizon, paths, seed)

    ten_year = ten_year_zero_along_paths(model, times, short_rate)
    mortgage_rate = ten_year + spread_over_ten_year

    flows = pool_cash_flows(pool, prepayment, mortgage_rate)
    value = price_from_flows(flows, discount, times, oas)

    # Average life needs principal only, so the investor's coupon is stripped back out.
    principal = _principal_only(pool, prepayment, mortgage_rate)
    returned = principal.sum(axis=1).mean()
    average_life = (float((principal * times[None, :]).sum(axis=1).mean() / returned)
                    if returned > 0 else float("nan"))

    incentive = pool.wac - mortgage_rate
    return dict(value=value, average_life=average_life,
                mean_cpr=float(prepayment.cpr(incentive).mean()),
                mean_incentive=float(incentive.mean()),
                principal_returned=float(returned))


def _principal_only(pool: MortgagePool, prepayment: PrepaymentModel,
                    mortgage_rate: np.ndarray) -> np.ndarray:
    """Re-run the amortisation keeping principal alone, for the average-life figure."""
    stripped = MortgagePool(balance=pool.balance, coupon=0.0, wac=pool.wac,
                            remaining_months=pool.remaining_months)
    return pool_cash_flows(stripped, prepayment, mortgage_rate)


def age_pool(pool: MortgagePool, model: HullWhite, prepayment: PrepaymentModel,
             spread_over_ten_year: float, paths: int, seed: int,
             months: int = MONTHS_PER_YEAR) -> MortgagePool:
    """
    Roll a pool forward by a year: shorten the term, run down the balance.

    This is the step that makes the end-2022 test out of sample rather than a
    re-solve. The balance is reduced by twelve months of scheduled amortisation and
    modelled prepayment under the *end-2021* model, because that is all an analyst
    standing at end-2021 could have known. Nothing about the realised 2022 path is used
    to decide how much principal came back.
    """
    horizon = months / MONTHS_PER_YEAR
    times, short_rate, discount = monthly_grid(model, horizon, paths, seed)
    mortgage_rate = ten_year_zero_along_paths(model, times, short_rate) \
        + spread_over_ten_year

    principal = _principal_only(pool, prepayment, mortgage_rate)
    repaid = float(principal.sum(axis=1).mean())

    return MortgagePool(balance=max(pool.balance - repaid, 0.0), coupon=pool.coupon,
                        wac=pool.wac,
                        remaining_months=max(pool.remaining_months - months, 1))
