import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyomo.environ as pyo
from tqdm.auto import tqdm

from flex_dep_opt.config.settings import Settings
from flex_dep_opt.domain.depot import Depot
from flex_dep_opt.io.flexibility import (
    align_and_validate_flexibility_bounds,
    read_flexibility_bounds_csv,
)
from flex_dep_opt.io.prices import (
    FCR_DROOP_COL,
    build_fees_from_settings,
    build_forecast_prices_from_settings,
    build_prices_from_settings,
    get_fcr_frequency_data,
    get_fcr_prices,
)
from flex_dep_opt.io.results import (
    make_run_dir,
    save_dispatch_to_csv,
    save_run_info_txt,
    save_table_to_csv,
    write_latest_run_pointer,
)
from flex_dep_opt.io.time import LOCAL_TIMEZONE, local_config_timestamp_to_utc, validate_regular_index
from flex_dep_opt.market.fcr import fcr_gate_closure_timestamp
from flex_dep_opt.market.trading import (
    build_market_activity_mask_for_time,
    gate_closure_timestamp,
)
from flex_dep_opt.opt.model import flexibility_commercialization
from flex_dep_opt.opt.solve import extract_dispatch, extract_objective_terms, solve_model

logger = logging.getLogger(__name__)

_FCR_BREAKEVEN_NAN = {
    "breakeven_eur_per_mw": float("nan"),
    "margin_eur_per_mw": float("nan"),
    "margin_pct": float("nan"),
    "opp_cost_eur": float("nan"),
    "d_energy_eur": float("nan"),
    "d_fcr_activation_eur": float("nan"),
    "d_fees_eur": float("nan"),
    "d_cycling_eur": float("nan"),
    "d_imbalance_eur": float("nan"),
    "d_penalties_eur": float("nan"),
}

_PENALTY_TERM_NAMES = (
    "obj_imb_vol_penalty",
    "obj_term_penalty",
    "obj_e_slack_penalty",
    "obj_reserve_penalty",
    "obj_balance_penalty",
)

# Declined-slot breakeven: if forcing the block ON drives more than this fraction
# of the opportunity cost into the FCR reserve-slack penalty, the slot had power
# room but no SoC room. the "entry price" is then a penalty artifact, not an
# economic signal, so we report no_soc_room instead.
_BREAKEVEN_SOC_ROOM_PENALTY_FRAC = 0.5


def _fcr_breakeven_metrics(
    terms_with: dict[str, float],
    terms_without: dict[str, float],
    *,
    bid_kw: float,
    rev_eur: float,
    fcr_price: float,
    covered_fraction: float,
) -> dict[str, float | str]:
    """
    Breakeven of one FCR bid from two window solves: one holding the bid
    (`terms_with`) and the counterfactual without it (`terms_without`), with all
    other FCR bids pinned.

    For a *committed* slot, `terms_with` is the actual solve A and
    `terms_without` is the slot forced to 0 (solve B). For a *declined*
    (zero-bid) slot it is the other way round: `terms_with` is the slot forced
    in (one block) and `terms_without` is the actual solve.

    The opportunity cost is what the rest of the objective loses by holding the
    bid: obj_without - (obj_with - rev). Divided by the bid volume (normalized to
    the full product like the revenue term) it is the EUR/MW capacity price at
    which the bid breaks even — comparable to the pay-as-cleared price. For a
    committed slot the margin (price - breakeven) is >= 0; for a declined slot
    it is <= 0 (the model left it because it did not pay).
    """
    opp_cost = terms_without["objective"] - (terms_with["objective"] - rev_eur)
    denom = (bid_kw / 1000.0) * covered_fraction
    breakeven = opp_cost / denom
    margin = fcr_price - breakeven

    def d(name: str) -> float:
        return terms_without[name] - terms_with[name]

    return {
        "breakeven_status": "ok",
        "breakeven_eur_per_mw": breakeven,
        "margin_eur_per_mw": margin,
        "margin_pct": margin / fcr_price if abs(fcr_price) > 1e-9 else float("nan"),
        "opp_cost_eur": opp_cost,
        # Signed objective-term deltas (B - A): how each component changes when the bid is
        # dropped. opp_cost = d_energy + d_fcr_activation + d_imbalance - d_fees - d_cycling - d_penalties.
        "d_energy_eur": d("obj_energy_cashflow"),
        "d_fcr_activation_eur": d("obj_fcr_activation_cashflow"),
        "d_fees_eur": d("obj_fee_cost"),
        "d_cycling_eur": d("obj_cycling_cost"),
        "d_imbalance_eur": d("obj_imb_cashflow"),
        "d_penalties_eur": sum(d(n) for n in _PENALTY_TERM_NAMES),
    }


class _TqdmLoggingHandler(logging.Handler):
    def emit(self, record):
        try:
            tqdm.write(self.format(record))
        except Exception:
            self.handleError(record)


def _configure_logging() -> None:
    root_logger = logging.getLogger()
    if not any(isinstance(h, _TqdmLoggingHandler) for h in root_logger.handlers):
        for h in list(root_logger.handlers):
            root_logger.removeHandler(h)
        handler = _TqdmLoggingHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
    logging.getLogger("pyomo").setLevel(logging.WARNING)
    logging.getLogger("pyomo.core").setLevel(logging.ERROR)


def run_mpc(settings: Settings, run_dir: Path | None = None) -> Path:
    """
    Rolling-horizon Model Predictive Control (MPC).

    At each simulation step:
      - Optimize a forward-looking time window (DA horizon)
      - Apply only the first decision (receding horizon)
      - Roll the energy state forward
      - Respect market gate closures via activity masks
      - Permanently commit market positions once gate closure is passed

    Notes
    -----
    Gate-closure times are computed in the market layer via `gate_closure_timestamp()`.
    This ensures mask enforcement and commit logging use the exact same rules.
    """
    _configure_logging()
    run_started_at = datetime.now(ZoneInfo(LOCAL_TIMEZONE))

    # ============================================================
    # 1) Read configuration blocks
    # ============================================================
    sim_cfg = settings.simulation
    opt_cfg = settings.optimization
    mpc_cfg = opt_cfg.mpc
    flex_cfg = opt_cfg.flexibility
    dep_cfg = opt_cfg.depot

    imb_cfg = opt_cfg.imbalance
    imb_enabled = imb_cfg.enabled

    terminal_enabled = bool(mpc_cfg.terminal_condition)
    terminal_weight = float(mpc_cfg.terminal_weight_eur_per_kwh)

    # ============================================================
    # 2) Load flexibility bounds (flex bands)
    # ============================================================
    flexibility_bounds_full = read_flexibility_bounds_csv(flex_cfg.bounds_file)

    fcr_prices_full = (
        get_fcr_prices(opt_cfg.trading.fcr.prices_source)
        if opt_cfg.trading.fcr.enabled
        else pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="Europe/Berlin"))
    )
    fcr_freq_source = opt_cfg.trading.fcr.frequency_source
    fcr_frequency_data_full: pd.DataFrame | None = None
    if opt_cfg.trading.fcr.enabled and fcr_freq_source:
        fcr_frequency_data_full = get_fcr_frequency_data(fcr_freq_source)

    fcr_product_hours = float(opt_cfg.trading.fcr.product_hours)
    fcr_bid_block_kw = float(opt_cfg.trading.fcr.bid_block_mw) * 1000.0
    fcr_breakeven_enabled = bool(opt_cfg.trading.fcr.breakeven_analysis)
    fcr_breakeven_include_zero_bid = bool(opt_cfg.trading.fcr.breakeven_include_zero_bid)

    fcr_energy_reserve_kwh_per_kw = float(opt_cfg.trading.fcr.energy_reserve_minutes) / 60.0
    fcr_reserve_penalty = float(opt_cfg.trading.fcr.reserve_penalty_eur_per_kwh)
    fcr_balance_penalty = float(opt_cfg.trading.fcr.balance_penalty_eur_per_kwh)

    def _fcr_gate_ts(slot_start: pd.Timestamp) -> pd.Timestamp:
        return fcr_gate_closure_timestamp(
            slot_start,
            hour=opt_cfg.trading.fcr.gate_closure_hour,
            closes_previous_day=opt_cfg.trading.fcr.gate_closure_closes_previous_day,
            timezone=opt_cfg.trading.fcr.gate_closure_timezone,
        )

    # ============================================================
    # 3) Time settings
    # ============================================================
    step_hours = float(sim_cfg.timestep_hours)
    da_horizon_hours = float(mpc_cfg.da_horizon_hours)
    id_horizon_hours = float(mpc_cfg.id_horizon_hours)
    fcr_price_horizon_hours = float(mpc_cfg.fcr_price_horizon_hours)
    fcr_frequency_horizon_minutes = float(mpc_cfg.fcr_frequency_horizon_minutes)
    da_horizon_steps = int(da_horizon_hours / step_hours)

    # ============================================================
    # 4) Load prices, fees and simulation time index
    # ============================================================
    prices_by_market = build_prices_from_settings(settings)
    # DECISION prices for the MPC window (forecast where configured, else realized).
    # Settlement/postprocessing always uses the realized `prices_by_market`.
    forecast_prices_by_market = build_forecast_prices_from_settings(settings)
    fees_by_market = build_fees_from_settings(settings)

    # `report_start` bounds the RESULTS window (what postprocessing/KPIs see).
    # `start` is the internal simulation start: a warm-up lead-in of `warmup_hours`
    # is prepended so positions whose gate closes before the reporting start
    # (notably the first delivery day's DA auction, closing on D-1) are committed
    # on a full forward horizon during warm-up rather than pinned to zero. Warm-up
    # steps drive the state forward but are excluded from all recorded outputs.
    report_start = local_config_timestamp_to_utc(sim_cfg.start, local_tz=LOCAL_TIMEZONE)
    end = local_config_timestamp_to_utc(sim_cfg.end, local_tz=LOCAL_TIMEZONE)

    warmup_hours = float(getattr(sim_cfg, "warmup_hours", 0.0) or 0.0)
    start = report_start - pd.Timedelta(hours=warmup_hours)

    if warmup_hours > 0.0 and opt_cfg.markets.dayahead.enabled and opt_cfg.trading.mode == "realistic":
        first_da_gate = gate_closure_timestamp("DA", report_start, opt_cfg)
        if start >= first_da_gate:
            logger.warning(
                f"warmup_hours={warmup_hours} may be too small: the first reporting day's DA gate "
                f"closes at {first_da_gate.tz_convert(LOCAL_TIMEZONE)}, but warm-up only reaches back to "
                f"{start.tz_convert(LOCAL_TIMEZONE)}. The start-of-horizon initialization artifact may "
                f"persist; increase warmup_hours beyond the DA gate lead."
            )

    for mk in prices_by_market:
        prices_by_market[mk] = prices_by_market[mk].loc[start:end]
        validate_regular_index(
            prices_by_market[mk].index,
            timestep_hours=step_hours,
            name=f"{mk} price index",
        )
        forecast_prices_by_market[mk] = forecast_prices_by_market[mk].loc[start:end]
        validate_regular_index(
            forecast_prices_by_market[mk].index,
            timestep_hours=step_hours,
            name=f"{mk} forecast price index",
        )

    # Every configured forecast must cover the exact realized decision index so
    # window slicing can never silently produce NaNs.
    mk_cfg = opt_cfg.markets
    for mk, detail in (("DA", mk_cfg.dayahead), ("ID", mk_cfg.intraday)):
        if not (detail.enabled and detail.forecast_source):
            continue
        realized_idx = prices_by_market[mk].index
        forecast_idx = forecast_prices_by_market[mk].index
        if not forecast_idx.equals(realized_idx):
            raise ValueError(
                f"{mk} forecast prices ({detail.forecast_source}) do not cover the full "
                f"simulation window incl. horizon lookahead: expected {len(realized_idx)} steps "
                f"[{realized_idx[0]} .. {realized_idx[-1]}], got {len(forecast_idx)} steps "
                f"[{forecast_idx[0] if len(forecast_idx) else 'empty'} .. "
                f"{forecast_idx[-1] if len(forecast_idx) else 'empty'}]."
            )

    full_index = prices_by_market[next(iter(prices_by_market))].index
    validate_regular_index(full_index, timestep_hours=step_hours, name="simulation decision index")

    if warmup_hours > 0.0 and full_index[0] >= report_start:
        raise ValueError(
            f"warmup_hours={warmup_hours} requested but no input data before the reporting start "
            f"{report_start.tz_convert(LOCAL_TIMEZONE)} — extend the input series back by at least "
            f"the warm-up length, or reduce warmup_hours."
        )

    # Full state index (N+1): add terminal state timestamp
    dt = pd.Timedelta(hours=step_hours)
    full_state_index = full_index.append(pd.DatetimeIndex([full_index[-1] + dt]))

    if flexibility_bounds_full is not None:
        flexibility_bounds_full = align_and_validate_flexibility_bounds(
            bounds=flexibility_bounds_full,
            time_index=full_state_index,
            expected_len=len(full_state_index),
        )

    imb_pos_full = prices_by_market.pop("IMB_POS", None)
    imb_neg_full = prices_by_market.pop("IMB_NEG", None)
    # No forecast semantics for imbalance (reBAP is ex-post; PASS2 is a
    # feasibility fallback, not a trading decision) — always realized.
    forecast_prices_by_market.pop("IMB_POS", None)
    forecast_prices_by_market.pop("IMB_NEG", None)

    fcr_enabled = opt_cfg.trading.fcr.enabled

    # slice fcr prices to simulation window
    fcr_prices_full = fcr_prices_full.loc[(fcr_prices_full.index >= start) & (fcr_prices_full.index <= end)]

    all_fcr_slot_starts = list(fcr_prices_full.index)

    # Slot-local cap_max and slot_hours, computed against the *full* simulation
    # horizon (not the rolling window) so:
    #   - cap_max[j] reflects headroom over the whole 4 h slot (avoids
    #     bidding more MW than later steps can physically deliver), and
    #   - slot_hours[j] = 4 for slots fully covered by the sim, so the
    #     optimizer does not under-bid partial-overlap slots that the next
    #     rolling window will finish covering.
    fcr_cap_max_by_slot: dict[pd.Timestamp, float] = {}
    fcr_slot_hours_by_slot: dict[pd.Timestamp, float] = {}
    if not fcr_prices_full.empty and flexibility_bounds_full is not None:
        P_up_full = flexibility_bounds_full["Power_upper_kW"]
        P_lo_full = flexibility_bounds_full["Power_lower_kW"]
        for slot_start in all_fcr_slot_starts:
            slot_end = slot_start + pd.Timedelta(hours=fcr_product_hours)
            slot_mask = (full_index >= slot_start) & (full_index < slot_end)
            n_covered = int(slot_mask.sum())
            if n_covered == 0:
                fcr_cap_max_by_slot[slot_start] = 0.0
                fcr_slot_hours_by_slot[slot_start] = 0.0
                continue
            covered_idx = full_index[slot_mask]
            P_up = P_up_full.loc[covered_idx].to_numpy(dtype=float)
            P_lo = P_lo_full.loc[covered_idx].to_numpy(dtype=float)
            cap = float(np.minimum(P_up, -P_lo).min())
            fcr_cap_max_by_slot[slot_start] = max(cap, 0.0)
            fcr_slot_hours_by_slot[slot_start] = n_covered * step_hours

    # ============================================================
    # 5) Model options
    # ============================================================
    virtual_arbitrage = opt_cfg.virtual_arbitrage

    cyc_cfg = flex_cfg.cycle_regularization
    c_cyc = cyc_cfg.cost_eur_per_kwh_throughput if cyc_cfg.enabled else 0.0

    # ============================================================
    # 6) Initialize committed market positions
    # ============================================================
    committed_positions = {mk: pd.Series(0.0, index=full_index) for mk in prices_by_market.keys()}

    committed_fcr_slots: dict[pd.Timestamp, float] = {slot: 0.0 for slot in all_fcr_slot_starts}

    # ============================================================
    # 7) Initialize energy state (band-consistent)
    # ============================================================
    if flexibility_bounds_full is not None:
        E_state = 0.5 * (
            flexibility_bounds_full["Capacity_lower_kWh"].iloc[0]
            + flexibility_bounds_full["Capacity_upper_kWh"].iloc[0]
        )
    else:
        E_state = 0.0

    # ============================================================
    # 8) Result containers
    # ============================================================
    rows = []
    commit_rows = []
    fcr_commit_rows = []

    # ============================================================
    # 9) Rolling-horizon MPC loop
    # ============================================================
    try:
        pbar = tqdm(total=len(full_index), desc="MPC", unit="step", dynamic_ncols=True)

        for i in range(len(full_index)):
            current_time = full_index[i]
            next_time = current_time + pd.Timedelta(hours=step_hours)
            pbar.set_postfix(time=str(current_time.tz_convert(LOCAL_TIMEZONE)), E=f"{E_state:.1f} kWh")

            # --------------------------------------------------------
            # 9.1) Define rolling optimization window
            # --------------------------------------------------------
            N_total = len(full_index)
            window_end = min(i + da_horizon_steps, N_total)  # exclusive end for decisions
            window_idx = full_index[i:window_end]  # decision timestamps
            window_state_idx = full_state_index[i : window_end + 1]  # state timestamps (decisions + 1)

            if len(window_idx) == 0:
                break

            # --------------------------------------------------------
            # 9.2) Slice prices and flexibility bounds
            # --------------------------------------------------------
            # DECISION prices: forecast where configured, realized otherwise.
            window_prices = {mk: forecast_prices_by_market[mk].loc[window_idx] for mk in prices_by_market}

            window_flexibility_bounds = (
                align_and_validate_flexibility_bounds(
                    bounds=flexibility_bounds_full,
                    time_index=window_state_idx,
                    expected_len=len(window_state_idx),
                )
                if flexibility_bounds_full is not None
                else None
            )

            window_imb_pos = (
                imb_pos_full.loc[window_idx] if (imb_enabled and imb_pos_full is not None) else None
            )
            window_imb_neg = (
                imb_neg_full.loc[window_idx] if (imb_enabled and imb_neg_full is not None) else None
            )

            # reBAP prices for FCR activation cashflow (Option A).
            # Available in PASS-1 when FCR is active, independently of allow_imbalance,
            # so the optimizer prices p_droop energy at the correct settlement price.
            fcr_rebap_pos = (
                imb_pos_full.loc[window_idx] if (fcr_enabled and imb_pos_full is not None) else None
            )
            fcr_rebap_neg = (
                imb_neg_full.loc[window_idx] if (fcr_enabled and imb_neg_full is not None) else None
            )

            # fcr gates
            window_start = window_idx[0]
            window_end_ts = window_idx[-1] + pd.Timedelta(
                hours=fcr_product_hours
            )  # last slot may start up to one product length before window end

            # Include any slot that *overlaps* the window, not only those starting
            # inside it. An already-running slot (gate closed, bid committed) still
            # needs its droop activation applied to every step it covers — otherwise
            # only the slot-start step ever sees nonzero p_droop.
            slot_duration = pd.Timedelta(hours=fcr_product_hours)
            window_fcr_prices = fcr_prices_full.loc[
                (fcr_prices_full.index + slot_duration > window_start)
                & (fcr_prices_full.index < window_end_ts)
            ]

            # gate-open mask for slots in this window
            # a slot is biddable if (a) its D-1 08:00 gate closure is still in
            # the future and (b) it starts within the FCR price foresight
            # horizon. Slots beyond the horizon are pinned to their committed
            # value (0 if never committed), i.e. not biddable yet.
            fcr_price_horizon_end = current_time + pd.Timedelta(hours=fcr_price_horizon_hours)
            fcr_gate_open_window: dict[pd.Timestamp, bool] = {}
            for slot in window_fcr_prices.index:
                gate_ts = _fcr_gate_ts(slot)
                within_horizon = slot <= fcr_price_horizon_end
                fcr_gate_open_window[slot] = (current_time < gate_ts) and bool(within_horizon)

            # --------------------------------------------------------
            # 9.3) Build market activity masks (gate-closures enforced here)
            # --------------------------------------------------------
            trading_masks = build_market_activity_mask_for_time(
                current_time=current_time,
                delivery_times=window_idx,
                optimization_cfg=opt_cfg,
            )

            # --------------------------------------------------------
            # 9.3b) Build DECISION masks = trading masks + price foresight
            # --------------------------------------------------------
            decision_masks = {mk: s.copy() for mk, s in trading_masks.items()}

            id_horizon_end = current_time + pd.Timedelta(hours=id_horizon_hours)
            if "ID" in decision_masks:
                decision_masks["ID"][window_idx > id_horizon_end] = False

            # frequency data covering this window
            if fcr_frequency_data_full is not None:
                window_freq_data = fcr_frequency_data_full.loc[
                    (fcr_frequency_data_full.index >= window_idx[0])
                    & (fcr_frequency_data_full.index <= window_idx[-1] + pd.Timedelta(hours=1))
                ].copy()
                # FCR frequency foresight: grid frequency is unpredictable, so
                # only the near term is treated as known. Beyond the frequency
                # horizon the droop signal is reset to 0.0 so the optimizer plans
                # for no FCR activation there. The current step (at current_time)
                # always keeps its real value.
                fcr_freq_horizon_end = current_time + pd.Timedelta(minutes=fcr_frequency_horizon_minutes)
                beyond_horizon = window_freq_data.index > fcr_freq_horizon_end
                window_freq_data.loc[beyond_horizon, FCR_DROOP_COL] = 0.0
            else:
                window_freq_data = None

            # --------------------------------------------------------
            # 9.4) Build and parameterize optimization model
            # --------------------------------------------------------
            def _build_model(allow_imbalance: bool, imb_penalty: float) -> object:
                _m = flexibility_commercialization(
                    depot=Depot(
                        dep_cfg.eta_grid2depot, dep_cfg.eta_depot2grid, dep_cfg.grid_connection_limit
                    ),
                    prices_by_market=window_prices,
                    fee_eur_per_kwh_by_market=fees_by_market,
                    timestep_hours=step_hours,
                    virtual_arbitrage=virtual_arbitrage,
                    cycling_cost_eur_per_kwh=c_cyc,
                    market_activity_mask=decision_masks,
                    committed_positions={
                        mk: committed_positions[mk].loc[window_idx] for mk in committed_positions
                    },
                    flexibility_bounds=window_flexibility_bounds,
                    allow_imbalance=allow_imbalance,
                    imbalance_prices_pos=window_imb_pos,
                    imbalance_prices_neg=window_imb_neg,
                    imbalance_volume_penalty_eur_per_kwh=imb_penalty,
                    fcr_prices=window_fcr_prices if not window_fcr_prices.empty else None,
                    fcr_frequency_data=window_freq_data,
                    fcr_product_hours=fcr_product_hours,
                    fcr_bid_block_kw=fcr_bid_block_kw,
                    fcr_cap_max_by_slot=fcr_cap_max_by_slot or None,
                    fcr_slot_hours_by_slot=fcr_slot_hours_by_slot or None,
                    fcr_energy_reserve_kwh_per_kw=fcr_energy_reserve_kwh_per_kw,
                    fcr_reserve_penalty_eur_per_kwh=fcr_reserve_penalty,
                    fcr_balance_penalty_eur_per_kwh=fcr_balance_penalty,
                    fcr_rebap_prices_pos=fcr_rebap_pos,
                    fcr_rebap_prices_neg=fcr_rebap_neg,
                )

                if hasattr(_m, "S_FCR"):
                    # _m._fcr_slot_starts is set inside the model (only the slots
                    # that actually overlap this window), so S_FCR indices map to it.
                    for j in _m.S_FCR:
                        slot = _m._fcr_slot_starts[j]
                        is_open = fcr_gate_open_window.get(slot, False)
                        committed_val = committed_fcr_slots.get(slot, 0.0)
                        _m.fcr_gate_open[j].set_value(1 if is_open else 0)
                        if not is_open:
                            _m.x_fcr_committed[j].set_value(committed_val)

                return _m

            model = _build_model(allow_imbalance=False, imb_penalty=0.0)

            # Helper to set E0 consistently on a given model
            def _set_E0(_model):
                if window_flexibility_bounds is not None:
                    lb0 = float(window_flexibility_bounds["Capacity_lower_kWh"].iloc[0])
                    ub0 = float(window_flexibility_bounds["Capacity_upper_kWh"].iloc[0])
                    E0 = float(E_state if i > 0 else 0.5 * (lb0 + ub0))
                    if not (lb0 - 1e-6 <= E0 <= ub0 + 1e-6):
                        logger.warning(
                            f"E0 out of bounds at {current_time}: E0={E0}, lb={lb0}, ub={ub0} (clamping)"
                        )
                    _model.E0.set_value(float(min(max(E0, lb0), ub0)))
                else:
                    _model.E0.set_value(float(E_state))

            # Helper to set terminal condition
            def _set_terminal_terms(_model):
                _model.w_term.set_value(0.0)
                _model.energy_term_hard.deactivate()

                if not terminal_enabled or window_flexibility_bounds is None:
                    return

                goal_time = full_state_index[-1]
                if goal_time not in window_state_idx:
                    return

                lb = float(window_flexibility_bounds.loc[goal_time, "Capacity_lower_kWh"])
                ub = float(window_flexibility_bounds.loc[goal_time, "Capacity_upper_kWh"])
                _model.Eterm.set_value(0.5 * (lb + ub))

                remaining_steps = N_total - i
                frac = min(1.0, da_horizon_steps / max(1, remaining_steps))
                _model.w_term.set_value(terminal_weight * frac)

                if remaining_steps == 1:
                    _model.energy_term_hard.activate()

            # --------------------------------------------------------
            # 9.5) Solve optimization problem (Two-pass)
            # --------------------------------------------------------
            solved = False
            used_rebap = False

            try:
                _set_E0(model)
                _set_terminal_terms(model)
                solve_model(
                    model,
                    solver_name=sim_cfg.solver,
                    threads=sim_cfg.solver_threads,
                    mip_gap=sim_cfg.solver_mip_gap,
                    time_limit_s=sim_cfg.solver_time_limit_s,
                )
                solved = True
                used_rebap = False

            except RuntimeError as e1:
                if not imb_enabled:
                    tqdm.write("ERROR - PASS1 infeasible and imbalance disabled -- abort")
                    logger.error(f"PASS1 infeasible at {current_time} and imbalance disabled. Details: {e1}")
                    solved = False
                else:
                    logger.info(f"PASS1 infeasible at {current_time} -- trying PASS2 (imbalance).")

                    model2 = _build_model(
                        allow_imbalance=True,
                        imb_penalty=imb_cfg.imbalance_volume_penalty_eur_per_kwh,
                    )

                    try:
                        _set_E0(model2)
                        _set_terminal_terms(model2)
                        solve_model(
                            model2,
                            solver_name=sim_cfg.solver,
                            threads=sim_cfg.solver_threads,
                            mip_gap=sim_cfg.solver_mip_gap,
                            time_limit_s=sim_cfg.solver_time_limit_s,
                        )
                        solved = True
                        used_rebap = True
                        model = model2
                    except RuntimeError as e2:
                        tqdm.write("ERROR - PASS2 also infeasible -- aborting")
                        logger.error(f"PASS2 also infeasible at {current_time}. Details: {e2}")
                        solved = False

            if not solved:
                logger.error(f"Both passes infeasible at current_time={current_time} -- aborting MPC loop.")
                break

            # --------------------------------------------------------
            # 9.6) Extract first-step dispatch
            # --------------------------------------------------------
            dispatch_window = extract_dispatch(model, window_idx)
            first_row = dispatch_window.iloc[0].copy()
            first_row["used_rebap"] = used_rebap
            first_row.name = window_idx[0]
            if current_time >= report_start:  # skip warm-up steps in recorded results
                rows.append(first_row)

            # --------------------------------------------------------
            # 9.7) Commit market positions at gate closure
            # --------------------------------------------------------
            trading_masks_next = build_market_activity_mask_for_time(
                current_time=next_time,
                delivery_times=window_idx,
                optimization_cfg=opt_cfg,
            )

            for mk in committed_positions:
                # Robustness: only process markets that exist in the masks
                if mk not in trading_masks or mk not in trading_masks_next:
                    continue

                p_col = f"p_{mk.lower()}_kw"
                if p_col not in dispatch_window.columns:
                    continue

                for tau in window_idx:
                    now_open = bool(trading_masks[mk].loc[tau])
                    next_open = bool(trading_masks_next[mk].loc[tau])

                    row = {
                        "market": mk,
                        "delivery_time": tau,
                        "current_time": current_time,
                        "next_time": next_time,
                        # Gate-closure timestamp from market layer (single source of truth)
                        "gate_closure_time": gate_closure_timestamp(mk, tau, opt_cfg),
                        "p_opt": float(dispatch_window.loc[tau, p_col]),
                        "committed_old": committed_positions[mk].loc[tau],
                        "committed_new": committed_positions[mk].loc[tau],
                        "commit_now": False,
                    }

                    # Mode "none": commit only the immediate first decision (as before)
                    if opt_cfg.trading.mode == "none" and tau == window_idx[0]:
                        committed_positions[mk].loc[tau] = row["p_opt"]
                        row["committed_new"] = row["p_opt"]
                        row["commit_now"] = True

                    # Realistic trading: commit exactly when the gate closes between now and next step
                    elif now_open and not next_open:
                        committed_positions[mk].loc[tau] = row["p_opt"]
                        row["committed_new"] = row["p_opt"]
                        row["commit_now"] = True

                    # Record commits for reporting-window deliveries only; the
                    # committed_positions state above is always updated (warm-up
                    # commits the first reporting day's DA at its D-1 gate).
                    if tau >= report_start:
                        commit_rows.append(row)

            # commit fcr slots at their gate closure
            if hasattr(model, "S_FCR"):
                # Snapshot the solve-A bids of every slot closing between now and the next step BEFORE any breakeven counterfactual solve overwrites the model's variable values.
                closing_slots = []
                for j in model.S_FCR:
                    slot = model._fcr_slot_starts[j]
                    gate_ts = _fcr_gate_ts(slot)
                    if current_time < gate_ts and next_time >= gate_ts:
                        closing_slots.append(
                            (
                                j,
                                slot,
                                gate_ts,
                                float(pyo.value(model.x_fcr[j])),
                                float(pyo.value(model.fcr_slot_revenue[j])),
                            )
                        )

                # Breakeven counterfactuals: re-solve the same window once per slot with that slot's FCR decision flipped and every other FCR bid pinned at its solve-A value. Purely diagnostic — nothing reads the model after this block, and the commitments below use the snapshotted bids, so results are identical flag on/off.
                #   - committed slot: force it OFF (z_fcr=0) -> opportunity cost of holding it; margin >= 0.
                #   - declined slot (zero bid, only with breakeven_include_zero_bid): force one block ON -> the "entry price" it would need; margin <= 0.
                breakeven_by_slot: dict[pd.Timestamp, dict] = {}
                do_breakeven = (
                    fcr_breakeven_enabled
                    and closing_slots
                    and (any(b[3] > 0 for b in closing_slots) or fcr_breakeven_include_zero_bid)
                )
                if do_breakeven:
                    terms_a = extract_objective_terms(model)
                    z_vals_a = {k: int(round(pyo.value(model.z_fcr[k]))) for k in model.S_FCR}
                    for k in model.S_FCR:
                        model.z_fcr[k].fix(z_vals_a[k])

                    for j, slot, gate_ts, bid_val, rev_eur in closing_slots:
                        if bid_val <= 0 and not fcr_breakeven_include_zero_bid:
                            continue
                        covered_fraction = (
                            float(fcr_slot_hours_by_slot.get(slot, fcr_product_hours)) / fcr_product_hours
                        )
                        fcr_price = float(window_fcr_prices.loc[slot])
                        if bid_val > 0:
                            forced_z = 0  # committed: flip off
                        else:
                            # Declined: flip one block on — but only if the slot can host one. The z_fcr bound is cap_max // block, so a slot with < 1 MW of capacity has bound 0 and can never be bid; there is no entry price to compute.
                            forced_z = 1 if (model.z_fcr[j].ub or 0) >= 1 else 0
                            if forced_z == 0:
                                breakeven_by_slot[slot] = {
                                    "breakeven_status": "no_capacity",
                                    **_FCR_BREAKEVEN_NAN,
                                }
                                continue
                        model.z_fcr[j].fix(forced_z)
                        try:
                            solve_model(
                                model,
                                solver_name=sim_cfg.solver,
                                threads=sim_cfg.solver_threads,
                                mip_gap=sim_cfg.solver_mip_gap,
                                time_limit_s=sim_cfg.solver_time_limit_s,
                            )
                            terms_cf = extract_objective_terms(model)
                            if bid_val > 0:
                                metrics = _fcr_breakeven_metrics(
                                    terms_a,
                                    terms_cf,
                                    bid_kw=bid_val,
                                    rev_eur=rev_eur,
                                    fcr_price=fcr_price,
                                    covered_fraction=covered_fraction,
                                )
                            else:
                                # Declined slot: the forced solve holds the bid, the actual solve (terms_a) is the "without" side.
                                metrics = _fcr_breakeven_metrics(
                                    terms_cf,
                                    terms_a,
                                    bid_kw=fcr_bid_block_kw,
                                    rev_eur=float(pyo.value(model.fcr_slot_revenue[j])),
                                    fcr_price=fcr_price,
                                    covered_fraction=covered_fraction,
                                )
                                metrics["breakeven_status"] = "ok_entry_price"
                                # SoC-room guard: the slot had power room (forced_z=1
                                # was feasible) but if most of the opp cost is just
                                # reserve-slack penalty, there was no SoC room and the
                                # entry price is a penalty artifact, not an economic signal.
                                forced_reserve_pen = (
                                    terms_cf["obj_reserve_penalty"] - terms_a["obj_reserve_penalty"]
                                )
                                if forced_reserve_pen > _BREAKEVEN_SOC_ROOM_PENALTY_FRAC * abs(
                                    metrics["opp_cost_eur"]
                                ):
                                    metrics = {
                                        "breakeven_status": "no_soc_room",
                                        **_FCR_BREAKEVEN_NAN,
                                    }
                            breakeven_by_slot[slot] = metrics
                        except RuntimeError as e:
                            logger.warning(
                                f"[{current_time}] FCR breakeven counterfactual failed for slot {slot}: {e}"
                            )
                            breakeven_by_slot[slot] = {
                                "breakeven_status": "solve_failed",
                                **_FCR_BREAKEVEN_NAN,
                            }
                        model.z_fcr[j].fix(z_vals_a[j])

                    for k in model.S_FCR:
                        model.z_fcr[k].unfix()

                for j, slot, gate_ts, bid_val, rev_eur in closing_slots:
                    committed_val = bid_val
                    committed_fcr_slots[slot] = committed_val
                    fcr_price_val = float(window_fcr_prices.loc[slot])
                    # Hours of this 4 h slot covered by the sim horizon; revenue is prorated to match the optimizer's fcr_revenue term (price is for the full 4 h product).
                    slot_hours = float(fcr_slot_hours_by_slot.get(slot, 4.0))

                    breakeven_cols = breakeven_by_slot.get(
                        slot,
                        {
                            "breakeven_status": "no_bid" if bid_val <= 0 else "disabled",
                            **_FCR_BREAKEVEN_NAN,
                        },
                    )

                    if slot >= report_start:  # skip warm-up slots in recorded results
                        fcr_commit_rows.append(
                            {
                                "slot_start": slot,
                                "gate_closure_time": gate_ts,
                                "committed_at": current_time,
                                "bid_kw": bid_val,
                                "x_fcr_kw": committed_val,
                                "x_fcr_mw": committed_val / 1000.0,
                                "fcr_price": fcr_price_val,
                                "slot_hours": slot_hours,
                                "fcr_revenue_eur": (committed_val / 1000.0)
                                * fcr_price_val
                                * (slot_hours / fcr_product_hours),
                                **breakeven_cols,
                            }
                        )

                    if bid_val > 0:
                        logger.info(
                            f"[{current_time}] FCR slot committed: {slot} - {committed_val:.1f} kW "
                            f"@ {fcr_price_val:.2f} €/MW"
                        )

            # --------------------------------------------------------
            # 9.8) Roll energy state forward
            # --------------------------------------------------------
            E_state = float(first_row["E_next_kWh"])
            pbar.update(1)

        pbar.close()

    # ============================================================
    # 10) Export results
    # ============================================================
    finally:
        # --- Read simulation "name" from settings ---
        name = sim_cfg.name.strip()
        if not name:
            raise ValueError(
                "simulation.name must be set in the settings file (e.g. 'illustrative_example')."
            )

        # --- Create per-run output directory (name + timestamp), unless caller
        #     supplied an explicit run_dir (e.g. batch mode names it per config) ---
        if run_dir is None:
            run_dir = make_run_dir("results", name, tz=LOCAL_TIMEZONE)
            write_latest_run_pointer(run_dir, results_root="results")
        else:
            run_dir = Path(run_dir)
            run_dir.mkdir(parents=True, exist_ok=True)

        # --- Snapshot the resolved settings as TOML for this run ---
        settings.save_to_toml(run_dir / "settings.toml")

        # --- Output filenames inside run_dir ---
        out_dispatch = run_dir / "dispatch.csv"
        out_commit = run_dir / "commit.csv"
        out_fcr_commit = run_dir / "fcr_commit.csv"

        # --- Write dispatch ---
        if rows:
            result = pd.DataFrame(rows)
            result.index.name = "time"
            save_dispatch_to_csv(result, out_dispatch, include_time_column=True, output_tz=LOCAL_TIMEZONE)

        # --- Write commit ---
        if commit_rows:
            commit_df = pd.DataFrame(commit_rows)
            commit_df = commit_df.sort_values(["delivery_time", "current_time"])
            save_table_to_csv(commit_df, out_commit, output_tz=LOCAL_TIMEZONE)

        # fcr commits log
        if fcr_commit_rows:
            fcr_commit_df = pd.DataFrame(fcr_commit_rows).sort_values("slot_start")
            save_table_to_csv(fcr_commit_df, out_fcr_commit, output_tz=LOCAL_TIMEZONE)

        run_finished_at = datetime.now(ZoneInfo(LOCAL_TIMEZONE))

        save_run_info_txt(
            run_dir=run_dir,
            simulation_name=name,
            config_path=settings.get_source_path(),
            solver_name=sim_cfg.solver,
            start_time=run_started_at,
            end_time=run_finished_at,
            tz=LOCAL_TIMEZONE,
        )

        print("MPC finished -- Postprocessing starts")

    return run_dir
