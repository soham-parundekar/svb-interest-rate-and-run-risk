"""
Every figure the report quotes, checked against the data it came from.

A research write-up drifts from its own output the moment a parameter changes and the
prose does not. This file is the guard against that: if a number in `reports/report.md`
stops matching the CSV it was taken from, a test fails and says which one.

It needs `make results` to have run. Before that the processed outputs do not exist and
the module skips rather than failing, so `make test` is still meaningful on a fresh
checkout.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.config import DATA_PROCESSED, DATA_RAW

REQUIRED = ["out_of_sample_validation.csv", "combined_validation.csv",
            "eve_scenarios.csv", "implied_deposit_half_life.csv",
            "outlier_test.csv", "run_risk_summary.csv",
            "run_risk_sensitivity.csv", "bucketed_balance_sheet.csv"]

pytestmark = pytest.mark.skipif(
    not all((DATA_PROCESSED / name).exists() for name in REQUIRED),
    reason="model output not built yet; run `make results` first")


def processed(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA_PROCESSED / name)


def raw(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA_RAW / name)


# ----------------------------------------------------------- the curve in Figure 1

def test_figure_one_caption_quotes_the_right_curve():
    """
    An earlier draft of the caption gave par yields under an axis labelled "zero-coupon
    rate" - the exact conflation this project spends a module avoiding. The four figures
    the caption now carries are pinned here so the slip cannot come back.
    """
    from src.data.market_data import curve_on
    from src.models.nss import build_curve

    expected = {"2021-12-31": {"par_2y": 0.73, "par_10y": 1.52,
                               "zero_2y": 0.74, "zero_10y": 1.60},
                "2022-12-31": {"par_2y": 4.41, "par_10y": 3.88,
                               "zero_2y": 4.36, "zero_10y": 3.92}}

    for as_of, want in expected.items():
        par = curve_on(as_of).set_index("tenor_years")["par_yield_pct"]
        curve, _ = build_curve(as_of)
        assert par.loc[2.0] == pytest.approx(want["par_2y"], abs=0.005)
        assert par.loc[10.0] == pytest.approx(want["par_10y"], abs=0.005)
        assert curve.zero_rate(2.0) * 100 == pytest.approx(want["zero_2y"], abs=0.02)
        assert curve.zero_rate(10.0) * 100 == pytest.approx(want["zero_10y"], abs=0.02)

    # And the distinction has to be real at the long end, or the module is pointless.
    curve, _ = build_curve("2021-12-31")
    par = curve_on("2021-12-31").set_index("tenor_years")["par_yield_pct"]
    assert curve.zero_rate(10.0) * 100 - par.loc[10.0] > 0.05


# ------------------------------------------------------- what the banks disclosed

class TestDisclosures:
    def test_svb_published_an_eve_table_in_2021(self):
        """Section 1. The figures the whole project is anchored to."""
        d = raw("rate_sensitivity_disclosed.csv")
        svb = d[(d.bank == "SVB") & (d.as_of == "2021-12-31")].set_index("shock_bp")
        assert svb.loc[0, "eve_amount"] == pytest.approx(20672, abs=0.5)
        assert svb.loc[100, "eve_pct"] == pytest.approx(-13.9, abs=0.01)
        assert svb.loc[200, "eve_pct"] == pytest.approx(-27.7, abs=0.01)

    def test_svb_published_no_eve_in_2022(self):
        """The disclosure change the project is named after."""
        d = raw("rate_sensitivity_disclosed.csv")
        svb = d[(d.bank == "SVB") & (d.as_of == "2022-12-31")]
        assert not svb.empty, "FY2022 Item 7A rows were not extracted at all"
        assert svb["eve_pct"].isna().all()
        assert svb["eve_amount"].isna().all()
        assert svb.set_index("shock_bp").loc[200, "nii_pct"] == pytest.approx(3.5, abs=0.01)

    def test_schwab_never_published_eve(self):
        d = raw("rate_sensitivity_disclosed.csv")
        schwab = d[d.bank == "Schwab"]
        assert schwab["eve_pct"].isna().all()
        assert len(schwab) == 4      # +/-100bp at two dates

    def test_the_validation_target(self):
        """$76,169m of fair value against $91,327m of amortized cost."""
        s = raw("securities_detail.csv")
        row = s[(s.bank == "SVB") & (s.as_of == "2022-12-31")
                & (s.classification == "HTM")
                & s.security_type.str.startswith("Total")].iloc[0]
        assert row.amortized_cost == pytest.approx(91327, abs=0.5)
        assert row.fair_value == pytest.approx(76169, abs=0.5)

    def test_the_transfers_that_change_the_comparison(self):
        """Section 4. Schwab moved $206.8bn of cost at $188.6bn of fair value."""
        t = raw("securities_transfers.csv")
        schwab = t[(t.bank == "Schwab") & (t.transfer == "total")].iloc[0]
        assert schwab.fair_value_transferred == pytest.approx(188600, abs=0.5)
        assert schwab.original_cost == pytest.approx(206828, abs=0.5)
        assert schwab.unamortized_at_year_end == pytest.approx(17521, abs=0.5)

        svb = t[t.bank == "SVB"].iloc[0]
        assert svb.unrealized_loss_at_transfer == pytest.approx(132, abs=0.5)
        # The point of the contrast: SVB's distortion is immaterial, Schwab's is not.
        assert svb.unrealized_loss_at_transfer < 0.01 * schwab.unrealized_loss_at_transfer


# -------------------------------------------------------------- the headline test

class TestOutOfSample:
    def test_svb_htm_prediction(self):
        o = processed("out_of_sample_validation.csv").set_index(
            ["bank", "classification"])
        row = o.loc[("SVB", "HTM")]
        assert row.predicted_price == pytest.approx(80.36, abs=0.02)
        assert row.actual_price == pytest.approx(83.41, abs=0.02)
        assert row.price_error_pct == pytest.approx(-3.66, abs=0.05)

    def test_error_is_conservative_in_direction(self):
        """
        The model should not be flattering the bank. Predicting a smaller loss than the
        one actually disclosed would be the awkward direction for this exercise.
        """
        o = processed("out_of_sample_validation.csv")
        assert (o["price_error"] < 0).all()

    def test_schwab_reconciles_on_an_original_cost_basis(self):
        c = processed("combined_validation.csv").set_index("bank")
        schwab = c.loc["Schwab"]
        assert schwab.predicted_price == pytest.approx(85.72, abs=0.02)
        assert schwab.disclosed_price == pytest.approx(92.07, abs=0.02)
        assert schwab.original_cost_price == pytest.approx(87.47, abs=0.02)
        assert schwab.error_pct == pytest.approx(-2.00, abs=0.05)
        # The adjustment has to be doing real work, or the section is overstated.
        assert abs(schwab.error_vs_disclosed) > 3 * abs(schwab.error_vs_original_cost)

    def test_svb_needs_no_such_adjustment(self):
        c = processed("combined_validation.csv").set_index("bank")
        assert c.loc["SVB", "unamortized_transfer_loss"] == pytest.approx(0.0, abs=1.0)


# ------------------------------------------------- the deposit assumption finding

class TestImpliedDepositDecay:
    def test_implied_half_life(self):
        i = processed("implied_deposit_half_life.csv").iloc[0]
        assert i.implied_uninsured_half_life == pytest.approx(3.59, abs=0.02)
        assert i.beta == pytest.approx(0.60, abs=1e-9)

    def test_uncalibrated_points_match_the_disclosure(self):
        """
        Only +100bp was fitted. The base level and the +200bp figure are predictions,
        and they are what make the exercise a test rather than a curve fit.
        """
        i = processed("implied_deposit_half_life.csv").iloc[0]
        assert i.model_base_eve == pytest.approx(20672, rel=0.05)

        e = processed("eve_scenarios.csv")
        imp = e[(e.bank == "SVB") & (e.as_of == "2021-12-31")
                & (e.deposit_case == "implied_by_disclosure")].set_index("scenario")
        assert imp.loc["parallel_+200bp", "delta_eve_pct"] == pytest.approx(-27.7, abs=0.3)
        # Down shocks are positive, small, and non-monotonic: -100bp gains more than
        # -200bp, which is what SVB disclosed and what the zero floor produces.
        down_100 = imp.loc["parallel_-100bp", "delta_eve_pct"]
        down_200 = imp.loc["parallel_-200bp", "delta_eve_pct"]
        assert 0 < down_200 < down_100

    def test_half_life_dominates_the_answer(self):
        """Section 5. The same assets, two deposit assumptions, very different risk."""
        e = processed("eve_scenarios.csv")
        svb = e[(e.bank == "SVB") & (e.as_of == "2021-12-31")]
        implied = svb[(svb.deposit_case == "implied_by_disclosure")
                      & (svb.scenario == "parallel_+200bp")].iloc[0]
        conservative = svb[(svb.deposit_case == "conservative")
                           & (svb.scenario == "parallel_+200bp")].iloc[0]
        assert conservative.delta_eve_pct == pytest.approx(-62.5, abs=0.3)
        assert conservative.delta_eve_pct < 2 * implied.delta_eve_pct
        # Asset values are identical; only the liability side moved.
        assert implied.assets == pytest.approx(conservative.assets, rel=1e-6)

    def test_svb_breaches_the_outlier_threshold_on_its_own_numbers(self):
        ot = processed("outlier_test.csv")
        row = ot[(ot.bank == "SVB") & (ot.as_of == "2021-12-31")
                 & (ot.deposit_case == "implied_by_disclosure")].iloc[0]
        assert row.loss_over_tier1_pct == pytest.approx(36.2, abs=0.2)
        assert bool(row.breaches_15pct)


# ------------------------------------------------------------------- run risk

class TestRunRisk:
    def test_the_taint_cliff(self):
        r = processed("run_risk_summary.csv").set_index(["bank", "as_of"])
        assert r.loc[("SVB", "2021-12-31"), "taint_as_pct_of_equity"] == pytest.approx(4.7, abs=0.1)
        assert r.loc[("SVB", "2022-12-31"), "taint_as_pct_of_equity"] == pytest.approx(74.8, abs=0.1)
        assert r.loc[("Schwab", "2022-12-31"), "taint_as_pct_of_equity"] == pytest.approx(30.5, abs=0.1)

    def test_modelled_threshold_was_exceeded_by_the_actual_run(self):
        r = processed("run_risk_summary.csv").set_index(["bank", "as_of"])
        svb = r.loc[("SVB", "2022-12-31")]
        assert svb.x_liquidity == pytest.approx(0.675, abs=0.005)
        assert svb.two_day_run_fraction == pytest.approx(0.846, abs=0.005)
        assert svb.two_day_run_fraction > svb.x_liquidity

    def test_schwab_never_reaches_either_threshold(self):
        r = processed("run_risk_summary.csv")
        schwab = r[r.bank == "Schwab"]
        assert schwab["x_liquidity"].isna().all()
        assert schwab["x_solvency"].isna().all()

    def test_equity_cushion_goes_at_a_four_point_discount(self):
        """Section 6. $4.0bn left after marking HTM; gone at roughly 4% forced-sale."""
        s = processed("run_risk_sensitivity.csv")
        full = s[(s.bank == "SVB") & (s.as_of == "2022-12-31")
                 & (s.secured_access == 0.0)].set_index("fire_sale_discount")
        assert full.loc[0.00, "equity_at_full_run"] == pytest.approx(4034, abs=2)
        assert full.loc[0.04, "equity_at_full_run"] < 0
        assert full.loc[0.02, "equity_at_full_run"] > 0


# ----------------------------------------------------------- the Schwab bug fix

def test_pass_through_bucket_concentration():
    """
    The asymmetry that hid a modelling error. Giving every pool a 25-year life barely
    touched SVB and roughly doubled the duration of a third of Schwab's book.
    """
    sheet = processed("bucketed_balance_sheet.csv")
    pass_throughs = ("Agency-issued MBS", "Agency-issued CMO - fixed rate",
                     "U.S. agency mortgage-backed securities")
    shares = {}
    for bank in ("SVB", "Schwab"):
        block = sheet[(sheet.bank == bank) & (sheet.as_of == "2021-12-31")
                      & sheet.label.isin(pass_throughs)]
        shares[bank] = (block[block.bucket == "gt_10y"].amortized_cost.sum()
                        / block.amortized_cost.sum())
    assert shares["SVB"] == pytest.approx(0.986, abs=0.002)
    assert shares["Schwab"] == pytest.approx(0.713, abs=0.002)
