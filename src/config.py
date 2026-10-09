"""Load config/params.yaml once and hand it round as a plain dict."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PARAMS_FILE = ROOT / "config" / "params.yaml"

DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
FIGURES = ROOT / "reports" / "figures"


@lru_cache(maxsize=1)
def params() -> dict:
    with PARAMS_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def grid(spec: list) -> tuple[float, float, int]:
    """Unpack a [min, max, points] triple from the config."""
    low, high, count = spec
    return float(low), float(high), int(count)
