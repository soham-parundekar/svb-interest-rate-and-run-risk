"""
How far the headline result moves when the judgement calls move.

The limitations section of the report names several assumptions that cannot be read off
a filing: how long the representative pool in the over-ten-years bucket actually runs,
how steep the prepayment curve is, how fast the short rate reverts. Saying results are
"sensitive" to those and leaving it there is not worth much. This module puts a number
on each.

Every variant re-runs the same end-2022 out-of-sample test with a single parameter
changed and everything else, including the random seed, held fixed. What is being
compared is the predicted SVB held-to-maturity price against the 83.41 per $100 the bank
disclosed, so each row answers: if this assumption were wrong in this direction, how
wrong would the answer be?

The sweep runs at a lower path count than the headline result. The Monte Carlo error at
4,000 paths is a fraction of a basis point on a bond price and common random numbers are
shared across variants, so differences between rows are far more precise than the path
count alone suggests.

    python -m src.analysis.robustness
"""

from __future__ import annotations

import pandas as pd

from ..config import DATA_PROCESSED, overridden
from . import validation

SWEEP_PATHS = 4000

# The figure every variant is scored against: SVB's disclosed HTM fair value over its
# disclosed amortized cost at 31 December 2022.
DISCLOSED_HTM_PRICE = 100.0 * 76169.0 / 91327.0


def svb_htm_prediction(paths: int = SWEEP_PATHS) -> float:
    """Predicted SVB held-to-maturity price per $100 under the current config."""
    outcome = validation.run(paths=paths)
    table = validation.summary_table(outcome)
    row = table[(table["bank"] == "SVB") & (table["classification"] == "HTM")].iloc[0]
    return float(row["predicted_price"])


def sweep() -> pd.DataFrame:
    """
    One row per assumption variant, scored against the disclosed price.

    The variants are chosen to bracket what a reviewer would plausibly argue for rather
    than to flatter the base case: a shorter pass-through life and a faster prepayment
    curve both push the prediction up toward the disclosed figure, and both are included.
    """
    variants = [
        ("base case", {}),
        ("over-10y bullet maturity 15y", {"securities.bucket_mid_years.gt_10y": 15.0}),
        ("over-10y bullet maturity 25y", {"securities.bucket_mid_years.gt_10y": 25.0}),
        ("pass-through life 20y", {"mbs.bucket_wam_years.gt_10y": 20.0}),
        ("pass-through life 28y", {"mbs.bucket_wam_years.gt_10y": 28.0}),
        ("prepayment floor 2% / ceiling 60%",
         {"mbs.cpr_min": 0.02, "mbs.cpr_max": 0.60}),
        ("prepayment floor 8% / ceiling 35%",
         {"mbs.cpr_min": 0.08, "mbs.cpr_max": 0.35}),
        ("prepayment curve twice as steep", {"mbs.steepness": 3.0}),
        ("servicing and guarantee fee 25bp",
         {"mbs.servicing_and_guarantee_fee": 0.0025}),
        ("servicing and guarantee fee 75bp",
         {"mbs.servicing_and_guarantee_fee": 0.0075}),
        ("mean reversion half-life 14y (a = 0.05)",
         {"hull_white.override_a": 0.05, "hull_white.override_sigma": 0.00619}),
        ("mean reversion half-life 4.6y (a = 0.15)",
         {"hull_white.override_a": 0.15, "hull_white.override_sigma": 0.00619}),
        ("short-rate volatility 100bp",
         {"hull_white.override_a": 0.0889, "hull_white.override_sigma": 0.0100}),
    ]

    rows = []
    for label, changes in variants:
        if changes:
            with overridden(**changes):
                predicted = svb_htm_prediction()
        else:
            predicted = svb_htm_prediction()
        rows.append(dict(
            variant=label,
            changed=", ".join(f"{k}={v}" for k, v in changes.items()) or "-",
            predicted_price=predicted,
            disclosed_price=DISCLOSED_HTM_PRICE,
            error_pct=100.0 * (predicted - DISCLOSED_HTM_PRICE) / DISCLOSED_HTM_PRICE))

    frame = pd.DataFrame(rows)
    base = float(frame.loc[frame["variant"] == "base case", "predicted_price"].iloc[0])
    frame["move_from_base"] = frame["predicted_price"] - base
    return frame


def main() -> None:
    frame = sweep()
    with pd.option_context("display.width", 200, "display.max_colwidth", 44):
        print(frame[["variant", "predicted_price", "error_pct",
                     "move_from_base"]].to_string(
            index=False, float_format=lambda v: f"{v:,.2f}"))

    spread = frame["predicted_price"].max() - frame["predicted_price"].min()
    worst = frame["error_pct"].abs().max()
    print(f"\nacross every variant the predicted price spans {spread:.2f} points "
          f"and the error never exceeds {worst:.1f}%")
    print(f"disclosed price {DISCLOSED_HTM_PRICE:.2f} per $100")

    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    frame.to_csv(DATA_PROCESSED / "robustness_sweep.csv", index=False)
    print(f"written to {DATA_PROCESSED / 'robustness_sweep.csv'}")


if __name__ == "__main__":
    main()
