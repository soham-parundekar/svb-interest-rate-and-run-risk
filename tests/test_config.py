"""
The config override used by the robustness sweep.

It exists so a sensitivity run can change one assumption without editing the committed
file. The thing worth testing is that it always puts the original back: a leaked
override would silently change every later result in the same process.
"""

import pytest

from src.config import overridden, params


def test_override_reaches_the_modules_that_read_it():
    """A module holding `from ..config import params` must see the change."""
    from src.models.mbs import PrepaymentModel

    base = PrepaymentModel.from_config().cpr_max
    with overridden(**{"mbs.cpr_max": 0.60}):
        assert PrepaymentModel.from_config().cpr_max == pytest.approx(0.60)
    assert PrepaymentModel.from_config().cpr_max == pytest.approx(base)


def test_original_is_restored_even_after_an_error():
    base = params()["mbs"]["cpr_max"]
    with pytest.raises(RuntimeError):
        with overridden(**{"mbs.cpr_max": 0.99}):
            raise RuntimeError("boom")
    assert params()["mbs"]["cpr_max"] == pytest.approx(base)


def test_nested_overrides_unwind_in_order():
    base = params()["mbs"]["cpr_max"]
    with overridden(**{"mbs.cpr_max": 0.50}):
        with overridden(**{"mbs.cpr_max": 0.60}):
            assert params()["mbs"]["cpr_max"] == pytest.approx(0.60)
        assert params()["mbs"]["cpr_max"] == pytest.approx(0.50)
    assert params()["mbs"]["cpr_max"] == pytest.approx(base)


def test_unknown_key_is_rejected():
    """A typo in a dotted path should fail loudly, not silently add a dead key."""
    with pytest.raises(KeyError, match="not a config key"):
        with overridden(**{"mbs.nonexistent": 1.0}):
            pass


def test_committed_config_estimates_rather_than_forcing_hull_white():
    """
    The override hook must be empty in the committed file, or a normal run would quietly
    use hard-coded parameters instead of estimating them from the short-rate history.
    """
    hw = params()["hull_white"]
    assert not hw.get("override_a")
    assert not hw.get("override_sigma")
