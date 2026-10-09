"""
Pull the tables we need out of SVB's and Schwab's 10-K filings.

The filings are inline-XBRL documents produced by Workiva. Their tables carry a lot
of presentation scaffolding: dollar signs sit in their own <td>, so do percent signs,
negative numbers are wrapped in parentheses rather than signed, and em-dashes stand in
for zero. Anything that reads these tables has to strip that layer off first, which is
what `table_to_frame` does.

We locate tables by the text they contain rather than by position. Table indices shift
whenever a filer re-flows a document, and we need the same code to work across four
filings from two issuers in two different reporting styles.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# The filings declare an XML prolog but are served and parsed as HTML. lxml copes fine;
# the warning is noise.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Cells that mean "nothing here" rather than a number. The long dash is the one filers
# actually use; the others show up after whitespace normalisation.
# Cells that carry presentation rather than data. `table_to_frame` drops any column
# made up entirely of these. Note the dash is deliberately absent: a dash means zero
# and a column of zeros is data.
PADDING_CELLS = {"", "$", "%", "(", ")"}

# A footnote marker trailing a row label, e.g. "U.S. agency debentures (1)".
FOOTNOTE_SUFFIX = re.compile(r"\s*\((\d{1,2})\)\s*$")


@dataclass(frozen=True)
class Filing:
    """One 10-K, with the EDGAR coordinates needed to cite it."""

    bank: str           # "SVB" or "Schwab"
    fiscal_year: int    # year of the balance-sheet date, not the filing date
    cik: str
    accession: str
    path: Path

    @property
    def as_of(self) -> pd.Timestamp:
        return pd.Timestamp(f"{self.fiscal_year}-12-31")

    @property
    def url(self) -> str:
        acc = self.accession.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/{int(self.cik)}/{acc}/"
            f"{self.path.name}"
        )


def squash(text: str) -> str:
    """Collapse runs of whitespace, including the non-breaking spaces Workiva emits."""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def load_tables(filing: Filing) -> list:
    """Every <table> in the filing, in document order."""
    html = filing.path.read_text(encoding="utf-8", errors="replace")
    return BeautifulSoup(html, "lxml").find_all("table")


def find_table(tables: list, *required: str, max_chars: int | None = None,
               skip: int = 0):
    """
    First table whose flattened text contains all of `required` (case-insensitive).

    `max_chars` rejects oversized matches. Several of the phrases we search for also
    appear in the MD&A summary tables, which run to a few thousand characters and are
    not what we want; a length ceiling is a cheap way to exclude them.

    `skip` steps past earlier matches, which matters because both issuers repeat the
    securities and deposit tables in MD&A and again in the financial-statement notes.
    """
    wanted = [r.lower() for r in required]
    seen = 0
    for idx, table in enumerate(tables):
        text = squash(table.get_text(" ", strip=True))
        if max_chars is not None and len(text) > max_chars:
            continue
        low = text.lower()
        if all(w in low for w in wanted):
            if seen < skip:
                seen += 1
                continue
            return idx, table
    raise LookupError(f"no table matching {required!r} (skip={skip})")


def _cell_text(cell) -> str:
    return squash(cell.get_text(" ", strip=True))


def table_to_frame(table) -> pd.DataFrame:
    """
    Flatten an HTML table to a DataFrame of strings, dropping presentation-only columns.

    Columns that hold nothing but currency or percent symbols get removed, because they
    carry no data and would otherwise shift the alignment between header and body rows.
    Row labels stay in column 0; everything else is left as text for `to_number` to deal
    with, since a column can mix yields, dollar amounts and footnote markers.
    """
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if cells:
            rows.append([_cell_text(c) for c in cells])
    if not rows:
        return pd.DataFrame()

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    frame = pd.DataFrame(rows)

    keep = [
        col for col in frame.columns
        if not frame[col].map(lambda v: v in PADDING_CELLS).all()
    ]
    return frame[keep].reset_index(drop=True)


# A cell holding only a dash. Workiva writes these where the value is exactly zero,
# which is NOT the same as the empty padding cells that carry the table's layout.
# Conflating the two silently shifts every later value in the row one slot to the left:
# SVB's "Agency-issued CMO-variable rate" line has a dash in Unrealized Losses, so a
# parser that drops dashes reads its $101m fair value as a $101m unrealized loss.
DASH_CELLS = {"—", "–", "-"}

def to_number(raw: str) -> float | None:
    """
    Parse one cell into a float. Returns None for padding, 0.0 for an explicit dash.

    Filers write negatives as "( 1,343 )" rather than with a minus sign, strip the
    thousands separators into the display string, and put the dollar sign in a
    neighbouring cell.
    """
    if raw is None:
        return None
    text = squash(str(raw))
    if text in DASH_CELLS:
        return 0.0
    if text in PADDING_CELLS or text in {"N/A", "NM", "n/a"}:
        return None

    # Symbols come off before the parentheses are read. Schwab writes a negative
    # percentage as "(4.5)%", with the percent sign outside the bracket, so a parser
    # that tests endswith(")") first decides the number is positive and then fails to
    # parse "4.5)%" at all - the figure disappears instead of flipping sign.
    text = text.replace("$", "").replace(",", "").replace("%", "").strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").strip()
    if text in DASH_CELLS:
        return 0.0
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return -value if negative else value


def clean_label(raw: str) -> str:
    """Row label with the trailing footnote marker removed."""
    return FOOTNOTE_SUFFIX.sub("", squash(raw)).strip()


def numeric_row(row: pd.Series, start: int = 1) -> list:
    """
    Numbers in a row, left to right, with dashes kept as zeros and padding dropped.

    Keeping the zeros is what preserves column alignment; see the note on DASH_CELLS.
    """
    return [v for v in (to_number(x) for x in row.iloc[start:]) if v is not None]


def label_rows(frame: pd.DataFrame, width: int | None = None) -> list:
    """
    (label, numbers) for each row carrying both a label and at least one figure.

    Section headers such as "Residential MBS:" have a label and no figures, so they fall
    out here, which is what we want: the securities tables nest the MBS sub-types under
    that header and the sub-type rows hold the data.

    Pass `width` to assert every data row yields exactly that many numbers. Any filing
    whose layout defeats the dash/padding rule then fails loudly at extraction instead
    of quietly feeding a misaligned portfolio into the valuation code.
    """
    out = []
    for _, row in frame.iterrows():
        label = clean_label(row.iloc[0])
        values = numeric_row(row)
        if not (label and values) or label in PADDING_CELLS or label in DASH_CELLS:
            continue
        if width is not None and len(values) != width:
            raise ValueError(
                f"row {label!r} produced {len(values)} figures, expected {width}: "
                f"{values}"
            )
        out.append((label, values))
    return out


def cut_at(frame: pd.DataFrame, label: str) -> pd.DataFrame:
    """
    Rows above the first occurrence of `label` in column 0.

    Schwab stacks the current and prior year in one table rather than giving each its
    own, so the current-year block ends where the prior-year date header begins. Without
    the cut, the component lines add to roughly twice the stated total, which is how
    this was noticed.
    """
    for pos, value in enumerate(frame.iloc[:, 0]):
        if clean_label(value).lower() == label.lower():
            return frame.iloc[:pos].reset_index(drop=True)
    return frame


def sectioned_rows(frame: pd.DataFrame, headers: dict[str, str],
                   width: int | None = None, default: str | None = None) -> list:
    """
    (section, label, numbers) where `headers` maps a header's text to a section name.

    Schwab reports available-for-sale and held-to-maturity in a single table, separated
    only by a bare label row. Reading that table without tracking which side of the
    header a row falls on would file $173bn of HTM agency MBS as AFS.

    Matching is on the full row label so "Held to maturity securities" and "Total held
    to maturity securities" stay distinguishable; the latter is a data row.

    `default` names the section for rows appearing before any header. Schwab's FY2021
    maturity table carries no header at all because the bank held nothing outside AFS
    that year.
    """
    out = []
    section = default
    for _, row in frame.iterrows():
        label = clean_label(row.iloc[0])
        values = numeric_row(row)
        if not label or label in PADDING_CELLS or label in DASH_CELLS:
            continue
        if not values:
            for text, name in headers.items():
                if label.lower() == text.lower():
                    section = name
                    break
            continue
        if width is not None and len(values) != width:
            raise ValueError(
                f"row {label!r} produced {len(values)} figures, expected {width}: "
                f"{values}"
            )
        out.append((section, label, values))
    return out
