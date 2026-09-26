from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import toml
from pydantic import BaseModel, Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, TomlConfigSettingsSource


class SimulationSettings(BaseModel):
    start: datetime
    end: datetime
    timestep_hours: float = 0.25
    name: str = "example_simulation"
    solver: Literal["cbc", "gurobi", "highs"] = "highs"
    # Default 8 matches the previous hard-coded solve_model default; set 1 for
    # batch runs that parallelize across scenarios (--jobs).
    solver_threads: int | None = 8
    solver_mip_gap: float | None = None
    solver_time_limit_s: int | None = None
    # Stream solver output to stdout — useful to diagnose hangs or slow solves.
    solver_tee: bool = False
    # Warm-up / spin-up lead-in before the reporting window (hours). The MPC runs
    # from (start - warmup_hours) so positions whose gate closes before `start`
    # (e.g. the first delivery day's DA auction, which closes on D-1) are committed
    # on a full forward horizon during warm-up instead of defaulting to zero.
    # Warm-up steps advance the state (SoC, committed positions) but are excluded
    # from all recorded results and KPIs. Should exceed the DA gate lead (> ~12 h)
    # to remove the start-of-horizon initialization artifact; 0 disables warm-up.
    warmup_hours: float = Field(default=0.0, ge=0.0)


class ForecastErrorSettings(BaseModel):
    # Per-market decision- vs. settlement-price split: the MPC optimizes on a
    # synthetically perturbed price series (settlement + sigma * z, z ~ AR(1)
    # with unit variance), while cashflows are settled on the real CSV prices.
    # Applies to any market (DA or ID) that carries this block.
    enabled: bool = False
    sigma_eur_per_mwh: Annotated[float, Field(ge=0.0)] = 0.0
    rho: Annotated[float, Field(ge=0.0, lt=1.0)] = 0.944  # lag-1 autocorr per 15-min step
    seed: int = 1


class MarketDetail(BaseModel):
    enabled: bool = False
    source: str
    # Optional decision-price forecast for the MPC; settlement always uses `source`.
    # None = perfect price foresight (realized prices used inside the MPC window).
    forecast_source: str | None = None
    fee_eur_per_kwh: float
    forecast_error: ForecastErrorSettings = Field(default_factory=ForecastErrorSettings)


class Markets(BaseModel):
    dayahead: MarketDetail
    intraday: MarketDetail


class TradingDayahead(BaseModel):
    gate_closure_hour: str = "12:00"
    closes_previous_day: bool = True


class TradingIntraday(BaseModel):
    offset_minutes_before_delivery: int = 30


class FCRSettings(BaseModel):
    enabled: bool = False
    prices_source: str
    frequency_source: str | None = None
    breakeven_analysis: bool = True
    breakeven_include_zero_bid: bool = False
    gate_closure_hour: str = "08:00"
    gate_closure_closes_previous_day: bool = True
    gate_closure_timezone: str = "Europe/Berlin"
    product_hours: float = 4.0
    bid_block_mw: float = 1.0
    energy_reserve_minutes: float = 15.0
    reserve_penalty_eur_per_kwh: float = 10.0
    balance_penalty_eur_per_kwh: float = 0.0001
    frequency_nominal_hz: float = 50.0
    deadband_hz: float = 0.010
    full_activation_hz: float = 0.200


class TradingSettings(BaseModel):
    mode: Literal["none", "realistic"] = "none"
    dayahead: TradingDayahead
    intraday: TradingIntraday
    fcr: FCRSettings

    @model_validator(mode="after")
    def _fcr_requires_realistic_mode(self) -> "TradingSettings":
        # FCR always commits at its real D-1 gate closure; mode="none" leaves
        # DA/ID always-open. Mixing them silently is a footgun, so reject it.
        if self.fcr.enabled and self.mode == "none":
            raise ValueError(
                "optimization.trading.fcr.enabled=true requires trading.mode='realistic' "
                "(FCR has no 'none'/always-open semantics)."
            )
        return self


class ImbalanceSettings(BaseModel):
    enabled: bool = False
    source_pos: str
    source_neg: str
    imbalance_volume_penalty_eur_per_kwh: float = 1000.0


class MpcSettings(BaseModel):
    da_horizon_hours: int
    id_horizon_hours: int
    fcr_price_horizon_hours: int
    fcr_frequency_horizon_minutes: int
    terminal_condition: bool
    terminal_weight_eur_per_kwh: float


class CycleRegularization(BaseModel):
    enabled: bool
    cost_eur_per_kwh_throughput: float


class FlexibilitySettings(BaseModel):
    bounds_file: str
    cycle_regularization: CycleRegularization


class DepotSettings(BaseModel):
    eta_grid2depot: Annotated[float, Field(gt=0.0, le=1.0)]
    eta_depot2grid: Annotated[float, Field(gt=0.0, le=1.0)]
    grid_connection_limit: Annotated[float, Field(gt=0)]


class OptimizationSettings(BaseModel):
    markets: Markets
    trading: TradingSettings
    imbalance: ImbalanceSettings
    virtual_arbitrage: bool
    mpc: MpcSettings
    flexibility: FlexibilitySettings
    depot: DepotSettings


class ReferenceDrivingEnergyCostsSettings(BaseModel):
    enabled: bool = False
    static_price_eur_per_kwh: float | None = None
    energy_column: str = "Ref_driving_energy_kWh"
    # Charging efficiency used to convert the battery-side driving-energy demand
    # into grid-side metered energy for the reference cost: conventional depot
    # charging incurs the same losses as the optimized dispatch, so the meter
    # sees driving_energy / charging_efficiency. Kept as a fixed benchmark,
    # decoupled from optimization.depot.eta_grid2depot, so the S0 reference stays
    # a common yardstick across all scenarios.
    charging_efficiency: Annotated[float, Field(gt=0.0, le=1.0)] = 0.98


class PostprocessingSettings(BaseModel):
    # False: delete commit.csv / fcr_commit.csv after successful postprocessing
    # (they are the dominant disk cost per run; kpis.csv, dispatch.csv and the
    # HTML plots are kept). They must still be written by the simulation since
    # postprocessing reads them back from the run directory.
    save_commits: bool = True
    reference_driving_energy_costs: ReferenceDrivingEnergyCostsSettings = Field(
        default_factory=ReferenceDrivingEnergyCostsSettings
    )


BASE_DIR = Path(__file__).resolve().parent

_DEFAULT_TOML = BASE_DIR / "settings_quickstart.toml"


class Settings(BaseSettings):
    simulation: SimulationSettings
    optimization: OptimizationSettings
    postprocessing: PostprocessingSettings = Field(default_factory=PostprocessingSettings)

    _source_path: Path | None = PrivateAttr(default=None)

    model_config = SettingsConfigDict(toml_file=_DEFAULT_TOML, env_nested_delimiter="__")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return (TomlConfigSettingsSource(settings_cls),)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> "Settings":
        resolved = _DEFAULT_TOML if config_path is None else Path(config_path)
        if config_path is not None and not resolved.is_file():
            raise FileNotFoundError(f"Config file not found: {resolved}")

        cls.model_config["toml_file"] = resolved
        try:
            instance = cls()
        finally:
            cls.model_config["toml_file"] = _DEFAULT_TOML

        instance._source_path = resolved
        return instance

    def save_to_toml(self, path: str | Path | None = None):
        save_path = path or self._source_path

        if not save_path:
            raise ValueError("no save path provided or configured")

        data = self.model_dump(mode="json")

        with open(save_path, "w") as f:
            toml.dump(data, f)

    def get_source_path(self) -> Path | None:
        return self._source_path
