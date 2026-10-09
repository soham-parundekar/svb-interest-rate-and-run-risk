"""
Treasury par yield curves and the FRED series the model calibrates against.

Downloads land in data/raw/ and are reused on later runs. Nothing here transforms a
rate: the par curve is written exactly as Treasury publishes it, because turning par
yields into discount factors is Module B's job and doing it silently at load time is
how par-versus-spot errors get buried.

    python -m src.data.market_data
"""

from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"

# Treasury asks for a contact address on bulk requests, and SEC requires one. Same
# courtesy applies here.
USER_AGENT = "SVB-EVE research project (parundekarsoham@gmail.com)"

TREASURY_CSV = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv/{year}/all"
    "?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv"
)

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"

# Column header in Treasury's CSV -> tenor in years. Treasury has published a 4-month
# bill yield since late 2022; it is absent from the 2021 file and present in 2022, so
# the mapping is applied to whatever columns turn up rather than assumed.
TREASURY_TENORS = {
    "1 Mo": 1 / 12, "2 Mo": 2 / 12, "3 Mo": 0.25, "4 Mo": 4 / 12, "6 Mo": 0.5,
    "1 Yr": 1.0, "2 Yr": 2.0, "3 Yr": 3.0, "5 Yr": 5.0, "7 Yr": 7.0,
    "10 Yr": 10.0, "20 Yr": 20.0, "30 Yr": 30.0,
}

FRED_SERIES = {
    "DGS3MO": "3-month Treasury constant maturity, daily",
    "DFF": "Federal funds effective rate, daily",
    "DGS10": "10-year Treasury constant maturity, daily",
    "MORTGAGE30US": "Freddie Mac 30-year fixed mortgage rate, weekly",
}

# Hull-White's mean reversion and volatility are estimated from history rather than
# from swaption prices, so the sample window is a modelling choice worth naming. Twenty
# years spans two full easing cycles and the 2015-2019 tightening, and stops at the last
# valuation date so nothing after end-2022 leaks into a model used to price end-2021.
SHORT_RATE_WINDOW = ("2003-01-01", "2022-12-31")


def _fetch(url: str, destination: Path) -> Path:
    if destination.exists():
        return destination
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return destination


def download_treasury_curves(years: list[int]) -> Path:
    """
    Daily Treasury par yield curves, one file per year, combined into one tidy CSV.

    Output columns are date, tenor_years, par_yield_pct. Long format rather than wide
    because the set of published tenors changes between years and a wide file would
    carry a column of blanks for the 4-month bill through 2021.
    """
    frames = []
    for year in years:
        path = _fetch(TREASURY_CSV.format(year=year), RAW / f"treasury_par_{year}.csv")
        raw = pd.read_csv(path)
        raw["Date"] = pd.to_datetime(raw["Date"])
        available = [c for c in raw.columns if c in TREASURY_TENORS]
        if not available:
            raise ValueError(f"no recognised tenor columns in {path.name}: "
                             f"{list(raw.columns)}")
        tidy = raw.melt(id_vars="Date", value_vars=available,
                        var_name="tenor_label", value_name="par_yield_pct")
        tidy["tenor_years"] = tidy["tenor_label"].map(TREASURY_TENORS)
        frames.append(tidy.dropna(subset=["par_yield_pct"]))

    combined = (pd.concat(frames)
                .rename(columns={"Date": "date"})
                .sort_values(["date", "tenor_years"])
                [["date", "tenor_label", "tenor_years", "par_yield_pct"]])
    out = RAW / "treasury_par_curves.csv"
    combined.to_csv(out, index=False)
    return out


def download_fred(series: str) -> Path:
    return _fetch(FRED_CSV.format(series=series), RAW / f"fred_{series}.csv")


def load_fred(series: str) -> pd.Series:
    """
    One FRED series as a date-indexed float series.

    FRED marks missing daily observations with a lone "." (market holidays, mostly),
    which `to_numeric` turns into NaN; those dates are dropped rather than forward
    filled, since a holiday is an absent observation and not a repeat of yesterday.
    """
    frame = pd.read_csv(download_fred(series))
    date_col, value_col = frame.columns[0], frame.columns[1]
    frame[date_col] = pd.to_datetime(frame[date_col])
    values = pd.to_numeric(frame[value_col], errors="coerce")
    return pd.Series(values.values, index=frame[date_col], name=series).dropna()


def load_par_curves() -> pd.DataFrame:
    path = RAW / "treasury_par_curves.csv"
    if not path.exists():
        download_treasury_curves(sorted({2021, 2022}))
    frame = pd.read_csv(path, parse_dates=["date"])
    return frame


def curve_on(as_of: str | pd.Timestamp) -> pd.DataFrame:
    """
    The published par curve for one date, or the last business day before it.

    Both valuation dates fall on 31 December. In 2022 that was a Saturday, so the curve
    we want is Friday 30 December; in 2021 it was a Friday and trades normally. Reaching
    backwards is the convention a bank would use for a year-end mark.
    """
    target = pd.Timestamp(as_of)
    curves = load_par_curves()
    available = curves.loc[curves["date"] <= target, "date"]
    if available.empty:
        raise LookupError(f"no Treasury curve published on or before {target.date()}")
    chosen = available.max()
    return (curves[curves["date"] == chosen]
            .sort_values("tenor_years")
            .reset_index(drop=True))


def main() -> None:
    path = download_treasury_curves([2021, 2022])
    curves = pd.read_csv(path, parse_dates=["date"])
    print(f"Treasury par curves: {len(curves):,} rows, "
          f"{curves['date'].min().date()} to {curves['date'].max().date()}")

    for as_of in ("2021-12-31", "2022-12-31"):
        snapshot = curve_on(as_of)
        actual = snapshot["date"].iloc[0].date()
        tenors = ", ".join(f"{r.tenor_label}={r.par_yield_pct:.2f}"
                           for r in snapshot.itertuples())
        print(f"  {as_of} -> curve dated {actual}: {tenors}")

    for series, description in FRED_SERIES.items():
        values = load_fred(series)
        print(f"{series}: {len(values):,} observations, "
              f"{values.index.min().date()} to {values.index.max().date()} "
              f"({description})")


if __name__ == "__main__":
    main()
