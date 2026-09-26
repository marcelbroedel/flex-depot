"""
Fast configuration tests for the illustrative-example scenarios S1-S4 (no MPC runs).

Asserts that the four TOMLs parse against the Settings model, enable exactly
the intended markets, differ ONLY in markets / forecast_error / name, share
identical band/penalty/terminal/fee parameters and simulation windows, and
that S4's imperfect-foresight AR(1) forecast error is the only scenario with a
non-trivial forecast_error block (DA + ID, distinct seeds, sigma > 0).
"""

from pathlib import Path

import pytest

from flex_dep_opt.config.settings import Settings

REPO_ROOT = Path(__file__).parent.parent
SCENARIO_DIR = REPO_ROOT / "examples/illustrative_example"
SCENARIO_IDS = ("s1", "s2", "s3", "s4")

# scenario -> (dayahead, intraday, fcr) enabled flags
_EXPECTED_MARKETS = {
    "s1": (True, False, False),
    "s2": (True, True, False),
    "s3": (True, True, True),
    "s4": (True, True, True),
}


@pytest.fixture(scope="module")
def scenarios() -> dict[str, Settings]:
    return {sid: Settings.load(SCENARIO_DIR / f"settings_{sid}.toml") for sid in SCENARIO_IDS}


def test_market_flags_per_scenario(scenarios):
    for sid, (da, intraday, fcr) in _EXPECTED_MARKETS.items():
        s = scenarios[sid]
        assert s.optimization.markets.dayahead.enabled is da, sid
        assert s.optimization.markets.intraday.enabled is intraday, sid
        assert s.optimization.trading.fcr.enabled is fcr, sid
        assert s.optimization.trading.mode == "realistic", sid


def test_forecast_error_only_in_s4(scenarios):
    # S1-S3 have perfect price foresight: no forecast CSV, forecast error off.
    for sid in ("s1", "s2", "s3"):
        s = scenarios[sid]
        for mk in (s.optimization.markets.dayahead, s.optimization.markets.intraday):
            assert mk.forecast_source is None, sid
            assert mk.forecast_error.enabled is False, sid

    # S4 is the imperfect-foresight scenario: AR(1) forecast error active on both
    # scheduled markets, valued via the realized `source` (no forecast CSV).
    s4 = scenarios["s4"]
    da_fe = s4.optimization.markets.dayahead.forecast_error
    id_fe = s4.optimization.markets.intraday.forecast_error
    assert s4.optimization.markets.dayahead.forecast_source is None
    assert s4.optimization.markets.intraday.forecast_source is None
    assert da_fe.enabled and id_fe.enabled
    assert da_fe.sigma_eur_per_mwh > 0.0 and id_fe.sigma_eur_per_mwh > 0.0
    # distinct seeds so DA and ID draw independent AR(1) shapes
    assert da_fe.seed != id_fe.seed


def test_shared_parameters_identical(scenarios):
    """Everything except markets, forecast_error and simulation.name is identical."""

    def comparable(s: Settings) -> dict:
        d = s.model_dump(mode="json")
        d["simulation"].pop("name")
        d["optimization"]["markets"]["dayahead"].pop("forecast_error")
        d["optimization"]["markets"]["intraday"].pop("enabled")
        d["optimization"]["markets"]["intraday"].pop("forecast_error")
        d["optimization"]["trading"]["fcr"].pop("enabled")
        return d

    reference = comparable(scenarios["s1"])
    for sid in ("s2", "s3", "s4"):
        assert comparable(scenarios[sid]) == reference, f"{sid} differs from s1 beyond scenario knobs"


def test_identical_simulation_window(scenarios):
    windows = {(s.simulation.start, s.simulation.end) for s in scenarios.values()}
    assert len(windows) == 1
