"""
Module D, portfolio half. Price every securities position and solve its OAS.

Two kinds of instrument live in these books and they need different machinery:

  Bullets - Treasuries, agency debentures, municipals, corporates, ABS and the
    rest. Cash flows are known, so they are priced analytically off the Module B
    discount curve. Running them through the Monte Carlo would give the same
    answer, since the simulated discount factors reprice that curve by
    construction, only with sampling noise added.

  Pass-throughs - residential agency MBS and fixed-rate CMO. Cash flows depend
    on the rate path, so they go through Module D's pathwise engine.

Agency CMBS is deliberately on the bullet side. Freddie K and Fannie DUS
collateral carries prepayment lockout and yield maintenance, so applying a
residential refinancing S-curve to it would invent an option the bonds do not
grant. Variable-rate CMO is priced at par; it reprices with the index.

The OAS is solved once per (bank, date, classification, security type) so the
model reproduces the fair value that type's note discloses at 31 December 2021.
It is then FROZEN. Everything in the end-2022 test is a consequence of the
curve having moved, not of anything re-fitted after the fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from ..config import params
from .hull_white import HullWhite
from .mbs import (
    MONTHS_PER_YEAR,
    MortgagePool,
    PrepaymentModel,
    age_pool,
    monthly_grid,
    pool_cash_flows,
    ten_year_zero_along_paths,
)

# Security types whose principal comes back early when borrowers refinance.
PASS_THROUGH_KEYS = ("agency issued mbs", "agency issued cmo fixed rate",
                     "u s agency mortgage backed securities")

# Priced at par: the coupon resets with the index, so there is no duration to speak of.
FLOATING_KEYS = ("agency issued cmo variable rate",)


def _key(label: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", str(label).lower()).strip()


def is_pass_through(label: str) -> bool:
    return _key(label) in PASS_THROUGH_KEYS


def is_floating(label: str) -> bool:
    return _key(label) in FLOATING_KEYS


@dataclass
class CashFlowSet:
    """
    Cash flows ready to be discounted, with the path discount factors beside them.

    Holding these rather than a price is what makes the OAS solve cheap: the flows do
    not depend on the spread, so the root-finder re-discounts the same arrays instead of
    re-simulating a mortgage book on every iteration.
    """

    times: np.ndarray                 # (steps,)
    flows: np.ndarray                 # (paths, steps); one row for a bullet
    discount: np.ndarray              # (paths, steps) or (1, steps)
    label: str = ""

    def price(self, oas: float) -> float:
        spread = np.exp(-oas * self.times)[None, :]
        return float((self.flows * self.discount * spread).sum(axis=1).mean())

    def average_life(self, principal: np.ndarray | None = None) -> float:
        weights = self.flows if principal is None else principal
        returned = weights.sum(axis=1).mean()
        if returned <= 0:
            return float("nan")
        return float((weights * self.times[None, :]).sum(axis=1).mean() / returned)


def bullet_cash_flows(curve, balance: float, coupon: float, maturity: float,
                      frequency: int = 2) -> CashFlowSet:
    """
    A level-coupon bond redeeming at par, discounted on the fitted curve.

    The coupon is the disclosed weighted-average book yield for the bucket. That is a
    yield rather than a coupon, so a bond bought at a premium is being approximated by
    a par bond of the same yield. The level error that introduces is absorbed by the
    OAS, which is calibrated to the disclosed fair value; what it does not absorb is the
    small duration difference between a premium bond and a par bond, and that is listed
    in the limitations.
    """
    step = 1.0 / frequency
    count = max(int(round(maturity * frequency)), 1)
    times = np.array([step * (i + 1) for i in range(count)])
    flows = np.full(count, balance * coupon / frequency)
    flows[-1] += balance
    discount = curve.discount(times)[None, :]
    return CashFlowSet(times=times, flows=flows[None, :], discount=discount)


def floating_cash_flows(curve, balance: float, reprice_years: float = 0.25
                        ) -> CashFlowSet:
    """A floater prices at par at its next reset, so one cash flow is enough."""
    times = np.array([reprice_years])
    flows = np.array([[balance]])
    return CashFlowSet(times=times, flows=flows,
                       discount=curve.discount(times)[None, :])


def pass_through_cash_flows(model: HullWhite, pool: MortgagePool,
                            prepayment: PrepaymentModel, spread: float,
                            paths: int, seed: int) -> tuple[CashFlowSet, np.ndarray]:
    """Pathwise mortgage cash flows, plus the principal-only stream for average life."""
    horizon = pool.remaining_months / MONTHS_PER_YEAR
    times, short_rate, discount = monthly_grid(model, horizon, paths, seed)
    mortgage_rate = ten_year_zero_along_paths(model, times, short_rate) + spread

    flows = pool_cash_flows(pool, prepayment, mortgage_rate)
    principal_only = pool_cash_flows(
        MortgagePool(balance=pool.balance, coupon=0.0, wac=pool.wac,
                     remaining_months=pool.remaining_months),
        prepayment, mortgage_rate)
    return CashFlowSet(times=times, flows=flows, discount=discount), principal_only


def build_cash_flows(position: pd.Series, model: HullWhite,
                     prepayment: PrepaymentModel, spread: float, paths: int,
                     seed: int, pool: MortgagePool | None = None
                     ) -> tuple[CashFlowSet, np.ndarray | None]:
    """Dispatch one position to the right pricing machinery."""
    balance = float(position["amortized_cost"])
    label = str(position["label"])

    if is_floating(label) or position["rate_type"] == "floating":
        return floating_cash_flows(model.curve, balance), None

    coupon = float(position["book_yield"])
    if is_pass_through(label):
        pool = pool or MortgagePool.from_position(balance, coupon,
                                                  bucket=str(position["bucket"]))
        flows, principal = pass_through_cash_flows(model, pool, prepayment, spread,
                                                   paths, seed)
        return flows, principal

    maturity = float(position["maturity_years"])
    return bullet_cash_flows(model.curve, balance, coupon, maturity), None


def solve_oas(flow_sets: list[CashFlowSet], target_value: float) -> float:
    """
    One spread that makes a group of positions reprice to its disclosed fair value.

    Price falls monotonically in the spread, so a bracketed root-finder is safe. When
    the target lies outside what the bracket can reach the bound itself is returned
    rather than an exception: that happens only for tiny residual buckets, and failing
    the whole portfolio over a $60m line of foreign government paper would be the wrong
    trade-off. `calibrate_portfolio` records which buckets hit the bound.
    """
    bounds = params()["oas"]["solver_bounds_bp"]
    low, high = float(bounds[0]) / 1e4, float(bounds[1]) / 1e4

    def mispricing(oas: float) -> float:
        return sum(f.price(oas) for f in flow_sets) - target_value

    if mispricing(low) < 0:
        return low
    if mispricing(high) > 0:
        return high
    return float(brentq(mispricing, low, high,
                        xtol=float(params()["oas"]["solver_tolerance"])))


def oas_at_bound(oas: float) -> bool:
    """Whether a solved spread is sitting on its bracket rather than inside it."""
    low, high = (float(b) / 1e4 for b in params()["oas"]["solver_bounds_bp"])
    return abs(oas - low) < 1e-9 or abs(oas - high) < 1e-9


@dataclass
class CalibratedBook:
    """Everything the end-2022 test needs carried forward from the end-2021 solve."""

    as_of: str
    bank: str
    oas: dict = field(default_factory=dict)          # (classification, label) -> spread
    pools: dict = field(default_factory=dict)        # (classification, label, bucket)
    positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    diagnostics: pd.DataFrame = field(default_factory=pd.DataFrame)


def calibrate_portfolio(sheet: pd.DataFrame, bank: str, as_of: str, model: HullWhite,
                        prepayment: PrepaymentModel, spread: float,
                        paths: int, seed: int) -> CalibratedBook:
    """
    Solve the OAS for every security type in one bank's book at one date.

    Grouping is by security type within classification, because that is the level at
    which the securities note discloses fair value. Finer would have nothing to
    calibrate against; coarser would let a mispriced municipal bucket hide inside the
    MBS book.
    """
    positions = sheet[(sheet["bank"] == bank) & (sheet["as_of"] == as_of)
                      & (sheet["instrument"] == "securities")].copy()

    book = CalibratedBook(as_of=as_of, bank=bank, positions=positions)
    rows = []

    for (classification, label), group in positions.groupby(
            ["classification", "label"], sort=False):
        flow_sets, pools = [], {}
        for _, position in group.iterrows():
            pool = None
            if is_pass_through(label) and position["rate_type"] != "floating":
                pool = MortgagePool.from_position(
                    float(position["amortized_cost"]),
                    float(position["book_yield"]), bucket=str(position["bucket"]))
                pools[position["bucket"]] = pool
            flows, _ = build_cash_flows(position, model, prepayment, spread, paths,
                                        seed, pool=pool)
            flow_sets.append(flows)

        target = float(group["fair_value"].sum())
        oas = solve_oas(flow_sets, target)
        modelled = sum(f.price(oas) for f in flow_sets)

        book.oas[(classification, label)] = oas
        for bucket, pool in pools.items():
            book.pools[(classification, label, bucket)] = pool

        rows.append(dict(
            bank=bank, as_of=as_of, classification=classification, label=label,
            amortized_cost=float(group["amortized_cost"].sum()),
            disclosed_fair_value=target, modelled_fair_value=modelled,
            oas_bp=oas * 1e4,
            # Flag a spread the solver could not reach inside its bracket. The only
            # position that does this is SVB's $100m variable-rate CMO, which prices at
            # par whatever the spread, so no finite OAS reproduces the $101m fair value
            # the filing shows. The residual is $0.3m and the position is 0.1% of the
            # book.
            at_solver_bound=oas_at_bound(oas),
            repricing_residual=modelled - target,
            instrument_kind=("pass_through" if is_pass_through(label)
                             else "floating" if is_floating(label) else "bullet")))

    book.diagnostics = pd.DataFrame(rows)
    return book
