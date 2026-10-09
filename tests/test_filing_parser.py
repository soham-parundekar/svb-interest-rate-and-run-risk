"""
Parser tests for the cell conventions in the 10-K tables.

Each case here is a bug that was actually hit while building the extraction, kept so it
cannot come back. The alignment one in particular is silent: nothing raises, the numbers
just shift one column left and a fair value gets filed as an unrealized loss.
"""

import pandas as pd
import pytest

from src.data.filings import (
    clean_label,
    cut_at,
    label_rows,
    numeric_row,
    sectioned_rows,
    to_number,
)


class TestToNumber:
    @pytest.mark.parametrize("text,expected", [
        ("$ 15,799", 15799.0),
        ("( 1,343 )", -1343.0),
        ("(27.7)", -27.7),
        ("1.05 %", 1.05),
        ("98,202", 98202.0),
    ])
    def test_reads_filing_formats(self, text, expected):
        assert to_number(text) == pytest.approx(expected)

    def test_dash_is_zero_not_missing(self):
        """Filers print an em-dash where the value is exactly zero."""
        assert to_number("—") == 0.0

    def test_padding_is_missing_not_zero(self):
        """A currency glyph in its own cell carries no value at all."""
        assert to_number("$") is None
        assert to_number("") is None
        assert to_number("N/A") is None

    def test_percent_outside_the_bracket(self):
        """
        Schwab writes a negative percentage as "(4.5)%", with the sign bracket inside
        the percent sign. Testing endswith(")") before stripping symbols reads this as
        positive and then fails to parse it at all, so the figure vanishes.
        """
        assert to_number("(4.5)%") == pytest.approx(-4.5)
        assert to_number("(3.2) %") == pytest.approx(-3.2)


class TestCleanLabel:
    @pytest.mark.parametrize("raw,expected", [
        ("U.S. agency debentures (1)", "U.S. agency debentures"),
        ("First Mortgages (1,2)", "First Mortgages"),     # Schwab's comma-joined form
        ("HELOCs (1)(2)", "HELOCs"),
        ("Total AFS securities", "Total AFS securities"),
    ])
    def test_strips_footnote_markers(self, raw, expected):
        assert clean_label(raw) == expected

    def test_keeps_dashes_inside_a_label(self):
        assert clean_label("Agency-issued CMO—fixed rate") == "Agency-issued CMO—fixed rate"


class TestRowAlignment:
    def test_dash_preserves_column_positions(self):
        """
        SVB's "Agency-issued CMO-variable rate" line prints a dash in Unrealized Losses.
        Dropping it leaves four figures instead of five and slides the $101m fair value
        into the unrealized-loss slot.
        """
        row = pd.Series(["Agency-issued CMO—variable rate", "100", "1", "—", "101", "—"])
        assert numeric_row(row) == [100.0, 1.0, 0.0, 101.0, 0.0]

    def test_width_assertion_catches_a_short_row(self):
        frame = pd.DataFrame([["Agency MBS", "64,439", "124", "(887)", "63,676"]])
        with pytest.raises(ValueError, match="expected 6"):
            label_rows(frame, width=6)

    def test_section_headers_are_not_data(self):
        frame = pd.DataFrame([
            ["Available for sale securities", "", "", ""],
            ["U.S. agency mortgage-backed securities", "85,994", "—", "8,306"],
            ["Held to maturity securities", "", "", ""],
            ["U.S. agency mortgage-backed securities", "173,074", "1,442", "15,580"],
        ])
        headers = {"Available for sale securities": "AFS",
                   "Held to maturity securities": "HTM"}
        rows = sectioned_rows(frame, headers, width=3)
        assert [r[0] for r in rows] == ["AFS", "HTM"]
        assert rows[1][2][0] == 173074.0


class TestCutAt:
    def test_stops_at_the_prior_year_block(self):
        """
        Schwab stacks both years in one table. Without the cut the component lines add
        to roughly twice the stated total.
        """
        frame = pd.DataFrame([
            ["December 31, 2021", ""],
            ["U.S. Treasury securities", "21,394"],
            ["December 31, 2020", ""],
            ["U.S. Treasury securities", "10,631"],
        ])
        trimmed = cut_at(frame, "December 31, 2020")
        assert len(trimmed) == 2
        assert label_rows(trimmed, width=1) == [("U.S. Treasury securities", [21394.0])]

    def test_returns_everything_when_the_label_is_absent(self):
        frame = pd.DataFrame([["Total", "1"]])
        assert len(cut_at(frame, "December 31, 1999")) == 1
