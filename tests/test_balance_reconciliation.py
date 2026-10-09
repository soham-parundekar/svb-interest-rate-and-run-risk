"""
Module A's reconciliation, run as a test.

Four filings, two issuers, two year-ends. A bucketing error in any one of them would
corrupt every valuation built on it and nobody re-derives these totals by hand, so the
check belongs in the suite rather than in a notebook cell.
"""

import pandas as pd
import pytest

from src.data.buckets import build, reconcile


@pytest.fixture(scope="module")
def sheet() -> pd.DataFrame:
    return build()


def test_every_bucketed_total_ties_to_the_filing(sheet):
    checks = reconcile(sheet)
    failed = checks[~checks["passed"]]
    assert failed.empty, (
        f"{len(failed)} bucketed total(s) do not match the reported balance sheet:\n"
        f"{failed.to_string(index=False)}")


def test_all_four_filings_are_present(sheet):
    pairs = set(zip(sheet["bank"], sheet["as_of"]))
    assert pairs == {("SVB", "2021-12-31"), ("SVB", "2022-12-31"),
                     ("Schwab", "2021-12-31"), ("Schwab", "2022-12-31")}


def test_securities_carry_a_book_yield(sheet):
    """
    Every fixed-rate securities position needs a yield: Module D uses it as the coupon
    assumption, and a missing one silently becomes a zero-coupon bond.
    """
    securities = sheet[(sheet["instrument"] == "securities")
                       & (sheet["rate_type"] == "fixed")]
    missing = securities[securities["book_yield"].isna()]
    assert missing.empty, f"positions with no book yield:\n{missing.to_string()}"

    # The lower bound allows genuinely negative yields. SVB held $61m of foreign
    # government debt yielding -0.81% in the under-one-year bucket at end-2021, which
    # is what European sovereign bills traded at; it is a real disclosure, not a parsing
    # artefact. The bounds are there to catch a percent/decimal mix-up, which would show
    # up as a yield of 1.05 rather than 0.0105.
    assert securities["book_yield"].between(-0.02, 0.10).all(), (
        f"book yields outside plausible range:\n"
        f"{securities[~securities['book_yield'].between(-0.02, 0.10)].to_string()}")


def test_uninsured_share_matches_the_disclosures(sheet):
    """
    The split that carries the thesis. SVB discloses $166.0bn uninsured in U.S. offices
    plus $16.1bn of foreign deposits against $189.2bn of total deposits at end-2021;
    Schwab discloses $134.8bn against $443.8bn.
    """
    deposits = sheet[sheet["instrument"] == "deposits"]
    shares = {}
    for (bank, as_of), block in deposits.groupby(["bank", "as_of"]):
        uninsured = block[block["bucket"] == "uninsured"]["amortized_cost"].sum()
        shares[(bank, as_of)] = uninsured / block["amortized_cost"].sum()

    assert shares[("SVB", "2021-12-31")] == pytest.approx(0.963, abs=0.01)
    assert shares[("Schwab", "2021-12-31")] == pytest.approx(0.304, abs=0.01)
    # The gap is the whole comparison; assert it is large rather than merely different.
    assert shares[("SVB", "2021-12-31")] > shares[("Schwab", "2021-12-31")] + 0.5


def test_svb_loan_book_is_overwhelmingly_floating(sheet):
    """SVB disclosed 91% variable-rate at end-2021 and 92% at end-2022."""
    loans = sheet[(sheet["bank"] == "SVB") & (sheet["instrument"] == "loans")]
    for as_of, expected in (("2021-12-31", 0.91), ("2022-12-31", 0.92)):
        block = loans[loans["as_of"] == as_of]
        floating = block[block["rate_type"] == "floating"]["amortized_cost"].sum()
        assert floating / block["amortized_cost"].sum() == pytest.approx(
            expected, abs=0.01)
