"""Load config/params.yaml once and hand it round as a plain dict."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PARAMS_FILE = ROOT / "config" / "params.yaml"

DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
FIGURES = ROOT / "reports" / "figures"

# Set only by `overridden`, for the robustness sweep. Everything else reads the file.
_override: dict | None = None


@lru_cache(maxsize=1)
def _from_file() -> dict:
    with PARAMS_FILE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def params() -> dict:
    return _override if _override is not None else _from_file()


@contextmanager
def overridden(**changes):
    """
    Run a block with config values replaced, then put the originals back.

    Each key is a dotted path into the config, so
    `overridden(**{"mbs.cpr_max": 0.6})` changes one leaf and leaves the rest alone.
    The swap happens behind `params()` rather than by patching the modules that call it,
    which means a module holding `from ..config import params` still sees the change.

    Used by the robustness sweep. Nothing in the main pipeline touches it, so a result
    produced by `make results` always comes from the committed config file.
    """
    global _override

    patched = copy.deepcopy(params())
    for dotted, value in changes.items():
        node = patched
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node[key]
        if leaf not in node:
            raise KeyError(f"{dotted!r} is not a config key; nothing to override")
        node[leaf] = value

    previous, _override = _override, patched
    try:
        yield patched
    finally:
        _override = previous


def grid(spec: list) -> tuple[float, float, int]:
    """Unpack a [min, max, points] triple from the config."""
    low, high, count = spec
    return float(low), float(high), int(count)
