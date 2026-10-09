"""
Figures for the report.

Each one is here because it carries an argument that a table makes you work for. Nothing
is plotted twice in different clothes, and every title says what the chart shows rather
than announcing that analysis has occurred.

    python -m src.plots
"""

from __future__ import annotations

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

from .config import DATA_PROCESSED, DATA_RAW, FIGURES, params
from .models.nss import build_curve
from .models.shocks import shocked_curves

# One colour per bank, used everywhere, so the reader learns it once.
SVB = "#B4443C"
SCHWAB = "#2F6690"
NEUTRAL = "#4A4A48"
GRID = "#D9D9D6"

plt.rcParams.update({
    "figure.dpi": 140,
    "savefig.dpi": 200,
    "savefig.bbox": "tight",
    "font.size": 9,
    "axes.titlesize": 10,
    "axes.titleweight": "semibold",
    "axes.labelsize": 9,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.edgecolor": NEUTRAL,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
    "legend.frameon": False,
    "figure.facecolor": "white",
})

PCT = FuncFormatter(lambda v, _: f"{v:,.0f}%")
BILLIONS = FuncFormatter(lambda v, _: f"${v / 1000:,.0f}bn")


def _save(fig, name: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    path = FIGURES / name
    fig.savefig(path)
    plt.close(fig)
    print(f"  {path.relative_to(FIGURES.parents[1])}")


def curve_shift() -> None:
    """The rate move that did the damage, in zero-coupon terms."""
    config = params()["valuation_dates"]
    base, _ = build_curve(str(config["base"]))
    test, _ = build_curve(str(config["test"]))
    tenors = np.linspace(0.08, 30, 300)

    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ax.plot(tenors, base.zero_rate(tenors) * 100, color=NEUTRAL, lw=1.8,
            label="31 Dec 2021")
    ax.plot(tenors, test.zero_rate(tenors) * 100, color=SVB, lw=1.8,
            label="30 Dec 2022")
    ax.fill_between(tenors, base.zero_rate(tenors) * 100,
                    test.zero_rate(tenors) * 100, color=SVB, alpha=0.08)
    ax.set_xlabel("maturity (years)")
    ax.set_ylabel("zero-coupon rate")
    ax.yaxis.set_major_formatter(PCT)
    ax.set_title("The Treasury curve rose about 400bp at the front and inverted")
    ax.legend(loc="lower right")
    _save(fig, "01_curve_shift.png")


def bcbs_shocks() -> None:
    """What the six standardised shocks actually do once the zero floor is applied."""
    config = params()["valuation_dates"]
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.6), sharey=True)
    tenors = np.linspace(0.08, 30, 300)

    for ax, date in zip(axes, (str(config["base"]), str(config["test"]))):
        curve, _ = build_curve(date)
        ax.plot(tenors, curve.zero_rate(tenors) * 100, color="black", lw=2.0,
                label="base", zorder=5)
        for (name, shocked), style in zip(shocked_curves(curve).items(),
                                          ["-", "-", "--", "--", ":", ":"]):
            ax.plot(tenors, shocked.zero_rate(tenors) * 100, ls=style, lw=1.1,
                    label=name.replace("_", " "))
        ax.set_xlabel("maturity (years)")
        ax.yaxis.set_major_formatter(PCT)
        ax.set_title(f"{date}")
    axes[0].set_ylabel("zero-coupon rate")
    axes[1].legend(ncol=2, fontsize=7.5, loc="lower right")
    fig.suptitle("The zero floor absorbs most of the down shocks at end-2021, "
                 "none at end-2022", y=1.02, fontsize=10, fontweight="semibold")
    _save(fig, "02_bcbs_shocks.png")


def out_of_sample() -> None:
    """Predicted against disclosed price per $100 of amortized cost at end-2022."""
    combined = pd.read_csv(DATA_PROCESSED / "combined_validation.csv")
    detail = pd.read_csv(DATA_PROCESSED / "out_of_sample_validation.csv")

    fig, (left, right) = plt.subplots(1, 2, figsize=(9.5, 3.8))

    svb = detail[(detail["bank"] == "SVB") & (detail["classification"] == "HTM")].iloc[0]
    bars = ["model\nprediction", "SVB\ndisclosed"]
    values = [svb["predicted_price"], svb["actual_price"]]
    left.bar(bars, values, color=[SVB, NEUTRAL], width=0.55)
    for x, v in zip(bars, values):
        left.text(x, v + 0.6, f"{v:.2f}", ha="center", fontsize=9,
                  fontweight="semibold")
    left.set_ylim(70, 90)
    left.set_ylabel("price per $100 of amortized cost")
    left.set_title(f"SVB held-to-maturity at 31 Dec 2022: "
                   f"{svb['price_error_pct']:+.1f}% error")
    left.grid(axis="x", visible=False)

    labels, predicted, disclosed, original = [], [], [], []
    for row in combined.itertuples():
        labels.append(row.bank)
        predicted.append(row.predicted_price)
        disclosed.append(row.disclosed_price)
        original.append(row.original_cost_price)

    x = np.arange(len(labels))
    width = 0.26
    right.bar(x - width, predicted, width, label="model prediction", color=SVB)
    right.bar(x, disclosed, width, label="as disclosed", color=GRID,
              edgecolor=NEUTRAL, lw=0.6)
    right.bar(x + width, original, width, label="on original cost", color=SCHWAB)
    right.set_xticks(x, labels)
    right.set_ylim(70, 100)
    right.set_ylabel("price per $100")
    right.set_title("Whole securities book: Schwab's disclosed basis\n"
                    "is flattered by the AFS-to-HTM transfer")
    right.legend(fontsize=8)
    right.grid(axis="x", visible=False)
    _save(fig, "03_out_of_sample.png")


def eve_profile() -> None:
    """Modelled EVE sensitivity against what SVB itself published."""
    scenarios = pd.read_csv(DATA_PROCESSED / "eve_scenarios.csv")
    disclosed = pd.read_csv(DATA_RAW / "rate_sensitivity_disclosed.csv")

    svb = disclosed[(disclosed["bank"] == "SVB")
                    & (disclosed["as_of"] == "2021-12-31")].dropna(subset=["eve_pct"])
    svb = svb.sort_values("shock_bp")

    fig, ax = plt.subplots(figsize=(6.4, 3.8))
    ax.axhline(0, color=NEUTRAL, lw=0.8)

    ax.plot(svb["shock_bp"], svb["eve_pct"], "o-", color=NEUTRAL, lw=1.8, ms=6,
            label="SVB disclosed (FY2021 10-K)")

    shocks = [-200, -100, 0, 100, 200]
    for case, colour, style, name in (
            ("implied_by_disclosure", SVB, "-", "model, deposit decay implied by that "
                                                "disclosure"),
            ("conservative", SCHWAB, "--", "model, 2-year uninsured half-life")):
        block = scenarios[(scenarios["bank"] == "SVB")
                          & (scenarios["as_of"] == "2021-12-31")
                          & (scenarios["deposit_case"] == case)]
        if block.empty:
            continue
        series = []
        for shock in shocks:
            name_key = "base" if shock == 0 else f"parallel_{shock:+d}bp"
            row = block[block["scenario"] == name_key]
            series.append(float(row["delta_eve_pct"].iloc[0]) if not row.empty
                          else np.nan)
        ax.plot(shocks, series, style, color=colour, lw=1.8, marker="s", ms=4,
                label=name)

    ax.set_xlabel("parallel rate shock (bp)")
    ax.set_ylabel("change in economic value of equity")
    ax.yaxis.set_major_formatter(PCT)
    ax.set_title("One calibrated parameter reproduces SVB's published EVE profile")
    ax.legend(fontsize=7.8, loc="lower left")
    _save(fig, "04_eve_profile.png")


def deposit_heatmap() -> None:
    """The project's central figure: the hedge, and the assumption holding it up."""
    heatmap = pd.read_csv(DATA_PROCESSED / "eve_heatmap.csv")
    implied = pd.read_csv(DATA_PROCESSED / "implied_deposit_half_life.csv")

    banks = [b for b in ("SVB", "Schwab") if b in set(heatmap["bank"])]
    fig, axes = plt.subplots(1, len(banks), figsize=(5.1 * len(banks), 4.0),
                             squeeze=False)

    blocks = [heatmap[heatmap["bank"] == b] for b in banks]
    limit = max(abs(np.nanpercentile(b["delta_eve_pct"], [2, 98])).max()
                for b in blocks)

    for ax, bank, block in zip(axes[0], banks, blocks):
        grid = block.pivot_table(index="beta", columns="uninsured_half_life",
                                 values="delta_eve_pct")
        mesh = ax.pcolormesh(grid.columns, grid.index, grid.values,
                             cmap="RdYlBu", vmin=-limit, vmax=limit, shading="auto")
        contour = ax.contour(grid.columns, grid.index, grid.values, levels=[0],
                             colors="black", linewidths=1.4)
        ax.clabel(contour, fmt="EVE unchanged", fontsize=7,
                  manual=[(grid.columns.max() * 0.78, grid.index.min() + 0.04)])

        if bank == "SVB" and not implied.empty:
            row = implied.iloc[0]
            ax.plot(row["implied_uninsured_half_life"], row["beta"], "k*", ms=14,
                    markeredgecolor="white", markeredgewidth=0.8)
            ax.annotate("implied by SVB's\nown disclosure",
                        (row["implied_uninsured_half_life"], row["beta"]),
                        textcoords="offset points", xytext=(-78, 16), fontsize=7.5,
                        ha="left")

        ax.set_xlabel("uninsured deposit half-life (years)")
        ax.set_title(f"{bank}, EVE change at +200bp")
        ax.grid(False)
    axes[0][0].set_ylabel("deposit beta")

    bar = fig.colorbar(mesh, ax=axes[0], fraction=0.035, pad=0.02)
    bar.set_label("change in EVE (%)")
    fig.suptitle("The deposit hedge works only where deposits stay",
                 y=1.01, fontsize=10, fontweight="semibold")
    _save(fig, "05_deposit_heatmap.png")


def run_risk_chart() -> None:
    """Equity against the size of a run, with what actually happened marked on it."""
    profile = pd.read_csv(DATA_PROCESSED / "run_risk_profile.csv")
    summary = pd.read_csv(DATA_PROCESSED / "run_risk_summary.csv")
    test_date = str(params()["valuation_dates"]["test"])

    fig, ax = plt.subplots(figsize=(6.6, 4.0))
    ax.axhline(0, color="black", lw=0.9)

    for bank, colour in (("SVB", SVB), ("Schwab", SCHWAB)):
        block = profile[(profile["bank"] == bank)
                        & (profile["as_of"] == test_date)].sort_values("run_fraction")
        if block.empty:
            continue
        ax.plot(block["run_fraction"] * 100, block["market_value_equity"],
                color=colour, lw=1.9, label=f"{bank}, 31 Dec 2022")
        tainted = block[block["htm_tainted"]]
        if not tainted.empty:
            first = tainted.iloc[0]
            ax.plot(first["run_fraction"] * 100, first["market_value_equity"], "o",
                    color=colour, ms=7, markeredgecolor="white")

        # Past this point the waterfall is dry: every security pledged or sold and the
        # withdrawal still unfunded. Equity is positive there but the bank has failed,
        # so the region is shaded rather than left looking survivable.
        dry = block[block["unmet"] > 1e-6]
        if not dry.empty:
            start = float(dry.iloc[0]["run_fraction"]) * 100
            ax.axvspan(start, 100, color=colour, alpha=0.08, lw=0)
            ax.annotate("outflow can no longer be funded", (start, 0),
                        textcoords="offset points", xytext=(6, 14), fontsize=7.5,
                        color=colour)

    svb = summary[(summary["bank"] == "SVB") & (summary["as_of"] == test_date)]
    if not svb.empty:
        top = ax.get_ylim()[1]
        one_day = float(svb["realised_run_fraction"].iloc[0]) * 100
        two_day = float(svb["two_day_run_fraction"].iloc[0]) * 100
        modelled = float(svb["x_liquidity"].iloc[0]) * 100

        ax.axvline(one_day, color=NEUTRAL, ls=":", lw=1.3)
        ax.annotate("9 Mar 2023:\n>$40bn withdrawn", (one_day, top * 0.82),
                    textcoords="offset points", xytext=(6, 0), fontsize=7.5)
        ax.axvline(two_day, color="black", ls="-.", lw=1.3)
        ax.annotate("two-day demand:\n+$100bn expected", (two_day, top * 0.60),
                    textcoords="offset points", xytext=(-96, 0), fontsize=7.5)
        if np.isfinite(modelled):
            ax.annotate(f"model: funding exhausted at {modelled:.0f}%",
                        (modelled, top * 0.30), textcoords="offset points",
                        xytext=(-150, 0), fontsize=7.5, color=SVB)

    ax.set_xlabel("run size (% of uninsured deposits withdrawn)")
    ax.set_ylabel("market-value equity")
    ax.yaxis.set_major_formatter(BILLIONS)
    ax.set_title("Marking the held-to-maturity book costs SVB three quarters "
                 "of its equity")
    ax.set_ylim(bottom=-1000)
    ax.legend(loc="center left", fontsize=8)
    _save(fig, "06_run_risk.png")


def prepayment_curve() -> None:
    """The S-curve, and where each bank's book sat on it at the two dates."""
    from .models.mbs import PrepaymentModel, mortgage_spread

    model = PrepaymentModel.from_config()
    incentive = np.linspace(-0.06, 0.04, 400)

    fig, ax = plt.subplots(figsize=(6.2, 3.6))
    ax.plot(incentive * 100, model.cpr(incentive) * 100, color=NEUTRAL, lw=2.0)

    config = params()["valuation_dates"]
    wac_fee = float(params()["mbs"]["servicing_and_guarantee_fee"])
    for date, colour, label in ((str(config["base"]), SCHWAB, "31 Dec 2021"),
                                (str(config["test"]), SVB, "30 Dec 2022")):
        curve, _ = build_curve(date)
        mortgage = float(curve.zero_rate(10.0)) + mortgage_spread(curve, date)
        # SVB's HTM agency MBS carried a 1.55% book yield at end-2021.
        point = 0.0155 + wac_fee - mortgage
        ax.plot(point * 100, model.cpr(point) * 100, "o", color=colour, ms=9,
                markeredgecolor="white", markeredgewidth=1.0, label=label, zorder=5)

    ax.set_xlabel("refinancing incentive: pool WAC less the prevailing mortgage rate")
    ax.set_ylabel("conditional prepayment rate")
    ax.yaxis.set_major_formatter(PCT)
    ax.xaxis.set_major_formatter(PCT)
    ax.set_title("SVB's 2021-vintage pools fell off the prepayment curve during 2022")
    ax.legend()
    _save(fig, "07_prepayment_curve.png")


def main() -> None:
    print("writing figures")
    for figure in (curve_shift, bcbs_shocks, prepayment_curve, out_of_sample,
                   eve_profile, deposit_heatmap, run_risk_chart):
        try:
            figure()
        except FileNotFoundError as missing:
            print(f"  skipped {figure.__name__}: {missing.filename} not built yet")


if __name__ == "__main__":
    main()
