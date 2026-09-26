import numpy as np
import pandas as pd
import pytest

from flex_dep_opt.config.settings import ForecastErrorSettings, MarketDetail
from flex_dep_opt.market.forecast_error import draw_ar1_shape, perturb_prices

_LONG_INDEX = pd.date_range("2026-01-01", periods=200_000, freq="15min", tz="UTC")


def test_same_seed_reproduces_identical_series():
    idx = pd.date_range("2026-01-01", periods=100, freq="15min", tz="UTC")
    z1 = draw_ar1_shape(idx, rho=0.9, seed=42)
    z2 = draw_ar1_shape(idx, rho=0.9, seed=42)
    pd.testing.assert_series_equal(z1, z2)

    z3 = draw_ar1_shape(idx, rho=0.9, seed=43)
    assert not z1.equals(z3)


def test_unit_marginal_variance():
    z = draw_ar1_shape(_LONG_INDEX, rho=0.944, seed=1)
    assert z.std() == pytest.approx(1.0, rel=0.02)
    assert z.mean() == pytest.approx(0.0, abs=0.05)


def test_lag1_autocorrelation_matches_rho():
    for rho in (0.0, 0.5, 0.944):
        z = draw_ar1_shape(_LONG_INDEX, rho=rho, seed=2).to_numpy()
        acf1 = np.corrcoef(z[:-1], z[1:])[0, 1]
        assert acf1 == pytest.approx(rho, abs=0.02)


def test_invalid_rho_raises():
    idx = pd.date_range("2026-01-01", periods=10, freq="15min", tz="UTC")
    with pytest.raises(ValueError):
        draw_ar1_shape(idx, rho=1.0, seed=1)
    with pytest.raises(ValueError):
        draw_ar1_shape(idx, rho=-0.1, seed=1)


def test_perturb_hits_target_rmse_in_eur_per_mwh():
    settlement = pd.Series(0.08, index=_LONG_INDEX)  # EUR/kWh
    z = draw_ar1_shape(_LONG_INDEX, rho=0.944, seed=3)
    sigma = 10.0  # EUR/MWh

    decision = perturb_prices(settlement, z, sigma)

    err_eur_per_mwh = (decision - settlement) * 1000.0
    rmse = float(np.sqrt((err_eur_per_mwh**2).mean()))
    assert rmse == pytest.approx(sigma, rel=0.02)


def test_sigma_zero_is_exact_identity():
    idx = pd.date_range("2026-01-01", periods=500, freq="15min", tz="UTC")
    settlement = pd.Series(np.linspace(-0.3, 0.4, len(idx)), index=idx, name="price")
    z = draw_ar1_shape(idx, rho=0.9, seed=4)

    decision = perturb_prices(settlement, z, 0.0)

    # regression case: bit-identical values, same name/index
    pd.testing.assert_series_equal(decision, settlement)


def test_perturbation_is_exactly_sigma_times_z():
    idx = pd.date_range("2026-01-01", periods=200, freq="15min", tz="UTC")
    settlement = pd.Series(0.1, index=idx)
    z = draw_ar1_shape(idx, rho=0.5, seed=5)

    decision = perturb_prices(settlement, z, 20.0)

    expected = settlement + 0.020 * z  # 20 EUR/MWh = 0.020 EUR/kWh
    pd.testing.assert_series_equal(decision, expected, check_names=False)


def test_z_must_cover_settlement_index():
    idx = pd.date_range("2026-01-01", periods=100, freq="15min", tz="UTC")
    settlement = pd.Series(0.1, index=idx)
    z = draw_ar1_shape(idx[:50], rho=0.9, seed=6)
    with pytest.raises(ValueError, match="does not cover"):
        perturb_prices(settlement, z, 10.0)


def test_negative_sigma_raises():
    idx = pd.date_range("2026-01-01", periods=10, freq="15min", tz="UTC")
    settlement = pd.Series(0.1, index=idx)
    z = draw_ar1_shape(idx, rho=0.9, seed=7)
    with pytest.raises(ValueError):
        perturb_prices(settlement, z, -1.0)


def test_settings_default_is_disabled():
    detail = MarketDetail(enabled=True, source="x.csv", fee_eur_per_kwh=0.008)
    assert detail.forecast_error.enabled is False
    assert detail.forecast_error.sigma_eur_per_mwh == 0.0


def test_settings_parse_forecast_error_block():
    detail = MarketDetail(
        enabled=True,
        source="x.csv",
        fee_eur_per_kwh=0.008,
        forecast_error={"enabled": True, "sigma_eur_per_mwh": 10.0, "rho": 0.9, "seed": 7},
    )
    fe = detail.forecast_error
    assert fe == ForecastErrorSettings(enabled=True, sigma_eur_per_mwh=10.0, rho=0.9, seed=7)


def test_settings_reject_invalid_values():
    with pytest.raises(ValueError):
        ForecastErrorSettings(sigma_eur_per_mwh=-1.0)
    with pytest.raises(ValueError):
        ForecastErrorSettings(rho=1.0)
