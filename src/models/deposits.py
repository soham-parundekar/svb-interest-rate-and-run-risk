"""
Module E. What a deposit base is worth, and what happens to that worth when it can run.

A non-maturity deposit has no contractual maturity and pays less than wholesale funding,
so it is worth less than its face amount to whoever owes it. Modelled as a balance that
decays at a constant half-life while paying the depositor a fraction beta of the market
rate:

    V = sum_t  D0 * exp(-t ln2 / h) * [(1 - beta) * y_t - c] * DF_t

The bracket is what the bank saves each year per dollar of deposit: it funds at
beta * y_t plus c of servicing cost, where wholesale money would cost y_t. The
franchise value V is the present value of that saving, and the economic value of the
liability is the book balance less V.

Why this module decides the whole comparison. A deposit franchise is a natural hedge
against rising rates - as rates rise the saving per dollar grows, so V grows, so the
liability gets cheaper and offsets the fall in asset values. Drechsler, Savov and
Schnabl (2021) make that case. The hedge has one requirement: the deposits have to stay.
Its strength is set entirely by beta and h, and h is not a market parameter - it is a
behavioural assumption about depositors who, in SVB's case, were overwhelmingly
uninsured, concentrated in one industry, and connected to each other.

So the deposit base is split in two. The uninsured layer runs fast, the insured layer
runs slow, and the heatmap over (beta, h) is the argument: at the short half-lives a
concentrated uninsured base can plausibly show, the hedge stops working.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import params

LN2 = float(np.log(2.0))


@dataclass(frozen=True)
class DepositLayer:
    """One behavioural slice of the deposit base."""

    name: str
    balance: float
    half_life_years: float
    beta: float
    interest_bearing: bool = True


def franchise_value(curve, balance: float, half_life_years: float, beta: float,
                    servicing_cost: float, horizon_years: float = 30.0,
                    steps_per_year: int = 12) -> float:
    """
    Present value of funding below market on a decaying balance.

    The short rate y_t is taken as the curve's own instantaneous forward rate, which is
    the rate at which money can be rolled overnight at t under the fitted curve. Using
    the forward rather than a flat spot rate matters at end-2021, when the curve was
    steep: the saving on a deposit base is much larger in the forward years than the
    spot short rate suggests, and that is precisely the hedge being claimed.

    Rates are floored at zero. Deposits cannot be repriced below it in practice, so a
    model that lets the saving go negative in a down shock overstates the franchise.
    """
    steps = int(round(horizon_years * steps_per_year))
    times = np.linspace(0.0, horizon_years, steps + 1)[1:]
    step = horizon_years / steps

    surviving = balance * np.exp(-times * LN2 / half_life_years)
    short_rate = np.maximum(curve.forward_rate(times), 0.0)
    margin = (1.0 - beta) * short_rate - servicing_cost
    discount = curve.discount(times)

    return float((surviving * margin * discount * step).sum())


def layer_value(curve, layer: DepositLayer, servicing_cost: float,
                horizon_years: float = 30.0) -> dict:
    """
    Economic value of one deposit layer.

    A non-interest-bearing balance has a beta of zero by construction: there is no rate
    to pass through. That is what made SVB's 67% non-interest-bearing mix look like such
    a cheap funding base at end-2021, and also what made it so sensitive when those
    balances started moving into money market funds.
    """
    beta = 0.0 if not layer.interest_bearing else layer.beta
    value = franchise_value(curve, layer.balance, layer.half_life_years, beta,
                            servicing_cost, horizon_years)
    return dict(layer=layer.name, balance=layer.balance,
                half_life_years=layer.half_life_years, beta=beta,
                franchise_value=value,
                economic_value=layer.balance - value,
                franchise_pct=100.0 * value / layer.balance if layer.balance else 0.0)


def build_layers(sheet: pd.DataFrame, bank: str, as_of: str,
                 uninsured_half_life: float, insured_half_life: float,
                 beta: float) -> list[DepositLayer]:
    """
    Split one bank's deposits into the layers Module F and Module G both need.

    Time deposits are left out of the non-maturity franchise: they have a contractual
    term, so a decay half-life does not describe them. They are small at both banks
    ($1.7bn at SVB, nothing at Schwab in 2021) and are priced as term funding instead.
    """
    deposits = sheet[(sheet["bank"] == bank) & (sheet["as_of"] == as_of)
                     & (sheet["instrument"] == "deposits")]
    layers = []
    for (classification, rate_type), block in deposits.groupby(
            ["classification", "rate_type"], sort=False):
        if classification.startswith("time"):
            continue
        uninsured = classification.endswith("uninsured")
        balance = float(block["amortized_cost"].sum())
        if balance <= 0:
            continue
        layers.append(DepositLayer(
            name=f"{'uninsured' if uninsured else 'insured'}"
                 f"_{'interest_bearing' if rate_type == 'floating' else 'noninterest'}",
            balance=balance,
            half_life_years=uninsured_half_life if uninsured else insured_half_life,
            beta=beta, interest_bearing=(rate_type == "floating")))
    return layers


def value_deposits(curve, sheet: pd.DataFrame, bank: str, as_of: str,
                   uninsured_half_life: float | None = None,
                   insured_half_life: float | None = None,
                   beta: float | None = None) -> dict:
    """Economic value of the whole deposit base, with the per-layer detail kept."""
    config = params()["deposits"]
    base = config["base_case"]
    uninsured_half_life = uninsured_half_life or float(base["uninsured_half_life_years"])
    insured_half_life = insured_half_life or float(base["insured_half_life_years"])
    beta = base["beta"] if beta is None else beta

    layers = build_layers(sheet, bank, as_of, uninsured_half_life, insured_half_life,
                          beta)
    detail = [layer_value(curve, layer, float(config["servicing_cost"]))
              for layer in layers]

    # Time deposits carry a contractual term, so they are priced as term funding at the
    # curve rather than run through the decay model.
    term = sheet[(sheet["bank"] == bank) & (sheet["as_of"] == as_of)
                 & (sheet["instrument"] == "deposits")
                 & (sheet["classification"].str.startswith("time"))]
    term_balance = float(term["amortized_cost"].sum())

    franchise = sum(d["franchise_value"] for d in detail)
    non_maturity_balance = sum(d["balance"] for d in detail)
    return dict(
        bank=bank, as_of=as_of, beta=beta,
        uninsured_half_life=uninsured_half_life, insured_half_life=insured_half_life,
        non_maturity_balance=non_maturity_balance, time_balance=term_balance,
        franchise_value=franchise,
        economic_value=non_maturity_balance - franchise + term_balance,
        detail=pd.DataFrame(detail))
