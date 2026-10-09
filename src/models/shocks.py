"""
Module F, scenarios. The six standardised interest rate shocks from the Basel
Committee's 2016 Interest Rate Risk in the Banking Book standard, USD calibration.

    parallel up / down    +/- 200bp at every tenor
    short up / down       dR(t) = +/- 300 * exp(-t/4)
    steepener             -0.65 * |dR_short(t)| + 0.90 * |dR_long(t)|
    flattener             +0.80 * |dR_short(t)| - 0.60 * |dR_long(t)|

with dR_long(t) = 150 * (1 - exp(-t/4)).

Two details that are easy to get wrong and change the answer.

The shock applies to the *zero* curve, so the instantaneous forward curve has to be
re-derived rather than shifted by the same amount. Only the parallel case leaves them
equal: for every other shape, f(t) = d/dt [t z(t)] picks up a t dR'(t) term, and
Hull-White is fitted to the forward curve, not the zero curve.

And the floor binds. The standard floors post-shock rates, historically at zero. At
end-2021 the entire front of the curve sits below 2%, so a -200bp parallel shock is
mostly absorbed by the floor and the realised down-shock is nothing like -200bp. A model
that skips the floor reports a symmetric EVE profile for a bank whose risk was anything
but.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import params


@dataclass(frozen=True)
class ShockShape:
    """One scenario, as a function of tenor returning the change in the zero rate."""

    name: str
    short_weight: float
    long_weight: float
    parallel: float = 0.0

    def __call__(self, tenors: np.ndarray) -> np.ndarray:
        config = params()["bcbs"]
        decay = float(config["decay_years"])
        t = np.asarray(tenors, dtype=float)

        short = float(config["short_bp"]) / 1e4 * np.exp(-t / decay)
        long = float(config["long_bp"]) / 1e4 * (1.0 - np.exp(-t / decay))
        return (self.parallel / 1e4
                + self.short_weight * short
                + self.long_weight * long)

    def derivative(self, tenors: np.ndarray) -> np.ndarray:
        """d/dt of the shock, needed to rebuild the forward curve."""
        config = params()["bcbs"]
        decay = float(config["decay_years"])
        t = np.asarray(tenors, dtype=float)

        short_prime = -float(config["short_bp"]) / 1e4 / decay * np.exp(-t / decay)
        long_prime = float(config["long_bp"]) / 1e4 / decay * np.exp(-t / decay)
        return self.short_weight * short_prime + self.long_weight * long_prime


def scenarios() -> dict[str, ShockShape]:
    """
    The six shapes, built from the standard's weights.

    The steepener and flattener are defined on the absolute sizes of the short and long
    shocks, so their weights carry the sign rather than the shock magnitudes.
    """
    parallel = float(params()["bcbs"]["parallel_bp"])
    return {
        "parallel_up": ShockShape("parallel_up", 0.0, 0.0, parallel),
        "parallel_down": ShockShape("parallel_down", 0.0, 0.0, -parallel),
        "short_up": ShockShape("short_up", 1.0, 0.0),
        "short_down": ShockShape("short_down", -1.0, 0.0),
        "steepener": ShockShape("steepener", -0.65, 0.90),
        "flattener": ShockShape("flattener", 0.80, -0.60),
    }


class ShockedCurve:
    """
    A fitted curve with a BCBS shock applied to its zero rates and a floor on top.

    Wraps rather than refits, so the shocked curve keeps the same discount and forward
    accessors the rest of the project calls. The forward curve is rebuilt from the
    definition f(t) = d/dt [t z(t)] = f_base(t) + dR(t) + t dR'(t), which is why
    ShockShape carries its own derivative.

    Where the floor binds the analytic derivative no longer describes the curve, so the
    forward is differenced numerically there instead. That leaves a kink at the point
    the floor starts binding, which is a real feature of the standard rather than an
    artefact: the standard prescribes the floor.
    """

    def __init__(self, base, shape: ShockShape, floor: float | None = None):
        self.base = base
        self.shape = shape
        self.floor = (float(params()["bcbs"]["post_shock_floor"])
                      if floor is None else floor)
        self.as_of = getattr(base, "as_of", "")
        self.beta = getattr(base, "beta", None)

    def _unfloored(self, tenors):
        return self.base.zero_rate(tenors) + self.shape(np.asarray(tenors, dtype=float))

    def zero_rate(self, tenors):
        return np.maximum(self._unfloored(tenors), self.floor)

    def discount(self, tenors):
        t = np.asarray(tenors, dtype=float)
        return np.exp(-self.zero_rate(t) * t)

    def forward_rate(self, tenors):
        t = np.atleast_1d(np.asarray(tenors, dtype=float))
        analytic = (self.base.forward_rate(t) + self.shape(t) + t * self.shape.derivative(t))

        # Numerical fallback wherever the floor has bitten, since the analytic
        # derivative describes the unfloored curve.
        binding = self._unfloored(t) < self.floor
        if binding.any():
            step = 1e-5
            upper = (t + step) * self.zero_rate(t + step)
            lower = np.maximum(t - step, 0.0) * self.zero_rate(np.maximum(t - step, 0.0))
            numerical = (upper - lower) / (t + step - np.maximum(t - step, 0.0))
            analytic = np.where(binding, numerical, analytic)

        analytic = np.maximum(analytic, self.floor)
        return analytic if np.ndim(tenors) else float(analytic[0])


def shocked_curves(base) -> dict[str, ShockedCurve]:
    return {name: ShockedCurve(base, shape) for name, shape in scenarios().items()}


def shock_table(base, tenors=(0.25, 1, 2, 5, 10, 20, 30)) -> pd.DataFrame:
    """Realised change in the zero rate under each scenario, after the floor."""
    t = np.asarray(tenors, dtype=float)
    rows = [dict(tenor=tenor, base_zero_pct=z * 100)
            for tenor, z in zip(t, base.zero_rate(t))]
    frame = pd.DataFrame(rows)
    for name, curve in shocked_curves(base).items():
        frame[f"{name}_bp"] = (curve.zero_rate(t) - base.zero_rate(t)) * 1e4
    return frame
