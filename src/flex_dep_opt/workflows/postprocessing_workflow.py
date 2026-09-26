from __future__ import annotations

from pathlib import Path

import pandas as pd

from flex_dep_opt.config.settings import Settings
from flex_dep_opt.io.prices import (
    build_fees_from_settings,
    build_forecast_prices_from_settings,
    build_prices_from_settings,
    get_fcr_frequency_data,
)
from flex_dep_opt.io.results import read_latest_run_pointer, save_dispatch_to_csv, save_summary_to_csv
from flex_dep_opt.io.time import LOCAL_TIMEZONE, local_config_timestamp_to_utc, validate_regular_index
from flex_dep_opt.post.metrics import (
    compute_cashflows_per_step,
    compute_fcr_activation_energy,
    compute_fcr_cashflow_per_slot,
    compute_kpis,
    compute_market_aggregates,
)
from flex_dep_opt.post.plots import plot_market_cashflows_plotly, plot_mpc_dispatch_plotly
from flex_dep_opt.post.reference_energy_costs import (
    DEFAULT_REFERENCE_ENERGY_COLUMN,
    compute_reference_driving_energy_costs,
)


def postprocess_mpc_results(settings: Settings | None = None, run_dir: Path | None = None) -> None:
    """
    Postprocessing workflow for MPC results.

    Steps
    -----
    1) Read dispatch.csv and commit.csv from the latest run directory (LATEST.txt)
    2) Load prices and fees from settings and slice to [start, end]
    3) Compute metrics (cashflows, aggregates, KPIs)
    4) Export optional postprocessing CSVs (cashflows + KPI summary)
    5) Generate interactive Plotly HTML plots
    """
    results_root = Path("results")
    if run_dir is None:
        run_dir = read_latest_run_pointer(results_root)

    if settings is None:
        run_settings_path = run_dir / "settings.toml"
        if not run_settings_path.exists():
            raise FileNotFoundError(
                f"No settings.toml found in {run_dir}. Pass an explicit config with --config."
            )
        settings = Settings.load(run_settings_path)

    print(f"Settings file used: {settings.get_source_path()}")

    sim = settings.simulation

    dispatch_csv = run_dir / "dispatch.csv"
    commit_csv = run_dir / "commit.csv"

    if not dispatch_csv.exists():
        raise FileNotFoundError(f"Dispatch CSV not found: {dispatch_csv.resolve()}")
    if not commit_csv.exists():
        raise FileNotFoundError(f"Commit CSV not found: {commit_csv.resolve()}")

    # ------------------------------------------------------------------
    # Time window
    # ------------------------------------------------------------------
    start = local_config_timestamp_to_utc(sim.start, local_tz=LOCAL_TIMEZONE)
    end = local_config_timestamp_to_utc(sim.end, local_tz=LOCAL_TIMEZONE)

    # ------------------------------------------------------------------
    # Load dispatch
    # ------------------------------------------------------------------
    df = pd.read_csv(dispatch_csv)
    if "time" not in df.columns:
        raise ValueError(f"{dispatch_csv} must contain a 'time' column.")
    idx = pd.to_datetime(df["time"], utc=True).dt.tz_convert("UTC")

    dispatch = df.drop(columns=["time"])
    dispatch.index = idx
    dispatch = dispatch.loc[start:end]
    validate_regular_index(dispatch.index, timestep_hours=float(sim.timestep_hours), name="dispatch index")

    # ------------------------------------------------------------------
    # Load commit
    # ------------------------------------------------------------------
    cdf = pd.read_csv(commit_csv)

    # robust parsing if saved as strings
    if "delivery_time" in cdf.columns:
        cdf["delivery_time"] = pd.to_datetime(cdf["delivery_time"], utc=True).dt.tz_convert("UTC")
    if "current_time" in cdf.columns:
        cdf["current_time"] = pd.to_datetime(cdf["current_time"], utc=True).dt.tz_convert("UTC")

    commit_df = cdf
    if "delivery_time" in commit_df.columns:
        commit_df = commit_df[(commit_df["delivery_time"] >= start) & (commit_df["delivery_time"] <= end)]

    fcr_commit_df: pd.DataFrame | None = None
    fcr_commit_csv = run_dir / "fcr_commit.csv"
    if fcr_commit_csv.exists():
        fcr_cdf = pd.read_csv(fcr_commit_csv)
        if "slot_start" in fcr_cdf.columns:
            fcr_cdf["slot_start"] = pd.to_datetime(fcr_cdf["slot_start"], utc=True).dt.tz_convert(
                "Europe/Berlin"
            )
        if "committed_at" in fcr_cdf.columns:
            fcr_cdf["committed_at"] = pd.to_datetime(fcr_cdf["committed_at"], utc=True).dt.tz_convert(
                "Europe/Berlin"
            )
        fcr_commit_df = fcr_cdf

    # -------------------------------------------------------------------------
    # Prices + fees
    # -------------------------------------------------------------------------
    prices_by_market = build_prices_from_settings(settings)
    for mk, s in list(prices_by_market.items()):
        prices_by_market[mk] = s.loc[start:end]
        validate_regular_index(
            prices_by_market[mk].index,
            timestep_hours=float(sim.timestep_hours),
            name=f"{mk} price index",
        )

    fees_by_market = build_fees_from_settings(settings)

    dt = sim.timestep_hours

    # FCR KPIs are sourced from fcr_commit.csv (per-slot EUR, computed at gate
    # closure). The previous implementation multiplied per-step x_fcr by per-step
    # FCR price, which double-counted by the number of steps per slot.
    fcr_kpis: dict = {}
    if fcr_commit_df is not None and not fcr_commit_df.empty:
        accepted = (
            fcr_commit_df[fcr_commit_df["accepted"].astype(bool)]
            if "accepted" in fcr_commit_df.columns
            else fcr_commit_df
        )
        committed_mask = (
            accepted["x_fcr_kw"] > 0
            if "x_fcr_kw" in accepted.columns
            else pd.Series(False, index=accepted.index)
        )
        fcr_kpis = {
            "fcr_revenue_eur": float(accepted["fcr_revenue_eur"].sum())
            if "fcr_revenue_eur" in accepted.columns
            else 0.0,
            "fcr_slots_committed": int(committed_mask.sum()),
            "fcr_avg_capacity_mw": (
                float(accepted.loc[committed_mask, "x_fcr_mw"].mean())
                if "x_fcr_mw" in accepted.columns and committed_mask.any()
                else 0.0
            ),
        }

    # -------------------------------------------------------------------------
    # Compute metrics
    # -------------------------------------------------------------------------
    cf_df = compute_cashflows_per_step(dispatch, prices_by_market, timestep_hours=dt)

    # Add FCR cashflow column (one entry per slot, at the slot start) so the
    # cashflow plot shows one bar per 4 h FCR slot and the gross-profit KPI
    # includes FCR.
    fcr_cf_series = compute_fcr_cashflow_per_slot(cf_df.index, fcr_commit_df)
    if fcr_cf_series is not None:
        total_col = "Total Cashflow [€/step]"
        cum_col = "Cumulative Profit [€]"
        cf_df = cf_df.drop(columns=[c for c in (total_col, cum_col) if c in cf_df.columns])
        cf_df["FCR Cashflow [€/step]"] = fcr_cf_series
        cf_df[total_col] = cf_df.sum(axis=1)
        cf_df[cum_col] = cf_df[total_col].cumsum()

    energy_by_mk, energy_data, cash_data = compute_market_aggregates(
        dispatch, prices_by_market, timestep_hours=dt
    )

    # Surface FCR revenue in the cash sunburst.
    if fcr_kpis.get("fcr_revenue_eur", 0.0) > 0:
        cash_data.append(("FCR", "Sell", float(fcr_kpis["fcr_revenue_eur"])))

    # Surface FCR *activation* energy (actually used capacity) in the energy
    # sunburst — committed MW alone is just an offered headroom; what hit the
    # battery is what's worth showing on the energy chart.
    fcr_act = compute_fcr_activation_energy(dispatch, timestep_hours=dt)
    if fcr_act is not None:
        fcr_buy_kwh, fcr_sell_kwh = fcr_act
        if fcr_buy_kwh > 0:
            energy_data.append(("FCR", "Buy", fcr_buy_kwh))
        if fcr_sell_kwh > 0:
            energy_data.append(("FCR", "Sell", fcr_sell_kwh))
        # Intentionally not added to `energy_by_mk` so the headline
        # buy_kwh / sell_kwh KPIs stay pure scheduled-market volumes.

    # Battery-cycling (degradation-proxy) cost on realized grid-side throughput,
    # mirroring the optimization objective's main deg term (c_deg * sum(p_ch +
    # p_dis) * dt). Folded into gross profit below so the KPI is consistent with
    # the objective. (The objective's small hidden-FCR sub-timestep term is not
    # reconstructed here; the realized p_ch/p_dis throughput is the physical wear.)
    cyc_cfg = settings.optimization.flexibility.cycle_regularization
    c_deg = cyc_cfg.cost_eur_per_kwh_throughput if cyc_cfg.enabled else 0.0
    throughput_kwh = float((dispatch["p_ch_kw"] + dispatch["p_dis_kw"]).sum()) * dt
    cycling_cost_eur = c_deg * throughput_kwh

    kpis = compute_kpis(
        cf_df, energy_by_mk, fees_by_market, commit=commit_df, cycling_cost_eur=cycling_cost_eur
    )
    kpis.update(fcr_kpis)

    pass2_steps = int(dispatch["used_rebap"].astype(bool).sum()) if "used_rebap" in dispatch.columns else 0
    kpis["pass2_steps"] = pass2_steps
    kpis["pass2_fraction_pct"] = round(pass2_steps / len(dispatch) * 100, 4) if len(dispatch) > 0 else 0.0

    # -------------------------------------------------------------------------
    # Price foresight: MPC decisions used forecast prices where configured;
    # cashflows above are always settled on realized prices.
    # -------------------------------------------------------------------------
    mk_cfg = settings.optimization.markets
    forecast_markets = [
        mk
        for mk, detail in (("DA", mk_cfg.dayahead), ("ID", mk_cfg.intraday))
        if detail.enabled and detail.forecast_source
    ]
    kpis["price_foresight"] = "forecast" if forecast_markets else "perfect"

    if forecast_markets:
        forecast_prices_by_market = build_forecast_prices_from_settings(settings)
        for mk in forecast_markets:
            realized = prices_by_market[mk]
            forecast = forecast_prices_by_market[mk].loc[start:end]
            if not forecast.index.equals(realized.index):
                raise ValueError(
                    f"{mk} forecast prices do not cover the simulation window "
                    f"[{realized.index[0]} .. {realized.index[-1]}] — cannot compute forecast MAE."
                )
            mae = float((forecast - realized).abs().mean())
            kpis[f"{mk.lower()}_forecast_mae_eur_per_kwh"] = mae

    # -------------------------------------------------------------------------
    # Optional: persist postprocessing results next to dispatch/commit outputs
    # -------------------------------------------------------------------------
    cashflow_csv = run_dir / "cashflow.csv"
    kpi_csv = run_dir / "kpis.csv"

    # cashflows: keep DatetimeIndex in the CSV for later batches
    save_dispatch_to_csv(cf_df, cashflow_csv, include_time_column=True, output_tz=LOCAL_TIMEZONE)

    # -------------------------------------------------------------------------
    # Optional static-price reference scenario for driving energy
    # -------------------------------------------------------------------------
    reference_df = None
    reference_summary = None

    ref_cfg = settings.postprocessing.reference_driving_energy_costs
    if ref_cfg.enabled:
        if ref_cfg.static_price_eur_per_kwh is None:
            raise ValueError(
                "postprocessing.reference_driving_energy_costs.static_price_eur_per_kwh "
                "must be set when the reference calculation is enabled."
            )

        flex_file = settings.optimization.flexibility.bounds_file

        reference_df, reference_summary = compute_reference_driving_energy_costs(
            flex_file,
            start=start,
            end=end,
            static_price_eur_per_kwh=float(ref_cfg.static_price_eur_per_kwh),
            charging_efficiency=float(ref_cfg.charging_efficiency),
            energy_column=str(ref_cfg.energy_column or DEFAULT_REFERENCE_ENERGY_COLUMN),
        )
        reference_csv = run_dir / "reference_driving_energy_costs.csv"
        save_dispatch_to_csv(reference_df, reference_csv, include_time_column=True, output_tz=LOCAL_TIMEZONE)

        # Apply the same cycling cost to the reference so only the *incremental*
        # V2G/arbitrage wear enters the delta. The reference charges grid-side
        # energy to drive (no discharge-to-grid), so its throughput is
        # ref_grid_energy_kwh. Without this, the unidirectional case would be
        # unfairly penalized for baseline charging wear the reference also incurs.
        ref_cycling_cost_eur = c_deg * float(reference_summary["ref_grid_energy_kwh"])
        reference_gross_profit_eur = (
            -float(reference_summary["ref_energy_cost_eur"]) - ref_cycling_cost_eur
        )
        kpis.update({
            "ref_gross_profit_eur": reference_gross_profit_eur,
            "ref_cycling_cost_eur": ref_cycling_cost_eur,
            "ref_driving_energy_kwh": float(reference_summary["ref_driving_energy_kwh"]),
            "ref_grid_energy_kwh": float(reference_summary["ref_grid_energy_kwh"]),
            "ref_charging_efficiency": float(reference_summary["ref_charging_efficiency"]),
            "ref_static_price_eur_per_kwh": float(reference_summary["ref_static_price_eur_per_kwh"]),
            "ref_energy_cost_eur": float(reference_summary["ref_energy_cost_eur"]),
            "total_potential_gross_profit_delta_eur": (
                float(kpis["gross_profit_eur"]) - reference_gross_profit_eur
            ),
        })

        print(
            "Reference driving energy costs: "
            f"{reference_summary['ref_energy_cost_eur']:.2f} EUR for "
            f"{reference_summary['ref_driving_energy_kwh']:.2f} kWh driving energy "
            f"({reference_summary['ref_grid_energy_kwh']:.2f} kWh grid-side at "
            f"eta={reference_summary['ref_charging_efficiency']:.3f})"
        )

    # KPIs: single row, including optional reference scenario fields
    save_summary_to_csv(kpis, kpi_csv)

    # -------------------------------------------------------------------------
    # HTML output paths (same base names as config entries)
    # -------------------------------------------------------------------------
    dispatch_html = run_dir / "dispatch.html"
    cashflow_html = run_dir / "cashflow.html"

    # -------------------------------------------------------------------------
    # Plot 1: dispatch report
    # -------------------------------------------------------------------------
    fcr_cfg = settings.optimization.trading.fcr
    fcr_frequency_data_pp: pd.DataFrame | None = None
    if fcr_cfg.enabled and getattr(fcr_cfg, "frequency_source", None):
        try:
            fcr_frequency_data_pp = get_fcr_frequency_data(fcr_cfg.frequency_source)
            fcr_frequency_data_pp = fcr_frequency_data_pp.loc[
                (fcr_frequency_data_pp.index >= start) & (fcr_frequency_data_pp.index <= end)
            ]
            # align with the local-time plot axis
            fcr_frequency_data_pp.index = fcr_frequency_data_pp.index.tz_convert(LOCAL_TIMEZONE)
        except Exception:
            fcr_frequency_data_pp = None

    dispatch_plot = dispatch.copy()
    dispatch_plot.index = dispatch_plot.index.tz_convert(LOCAL_TIMEZONE)
    prices_plot = {mk: s.tz_convert(LOCAL_TIMEZONE) for mk, s in prices_by_market.items()}
    commit_plot = commit_df.copy()
    for col in ("delivery_time", "current_time", "next_time", "gate_closure_time"):
        if col in commit_plot.columns and pd.api.types.is_datetime64_any_dtype(commit_plot[col]):
            if getattr(commit_plot[col].dt, "tz", None) is not None:
                commit_plot[col] = commit_plot[col].dt.tz_convert(LOCAL_TIMEZONE)

    fig_dispatch = plot_mpc_dispatch_plotly(
        dispatch=dispatch_plot,
        prices_by_market=prices_plot,
        commit_df=commit_plot,
        fcr_commit_df=fcr_commit_df,
        title="MPC Flexband Dispatch and Market Positions",
        fcr_frequency_data=fcr_frequency_data_pp,
        fcr_product_hours=fcr_cfg.product_hours,
    )
    fig_dispatch.write_html(dispatch_html, include_plotlyjs="cdn")
    # webbrowser.open(dispatch_html.resolve().as_uri())

    # -------------------------------------------------------------------------
    # Plot 2: cashflow report (plots consume precomputed metrics)
    # -------------------------------------------------------------------------
    cf_plot = cf_df.copy()
    cf_plot.index = cf_plot.index.tz_convert(LOCAL_TIMEZONE)
    fig_cf = plot_market_cashflows_plotly(
        cf_df=cf_plot,
        energy_data=energy_data,
        cash_data=cash_data,
        kpis=kpis,
        reference_df=(
            reference_df.tz_convert(LOCAL_TIMEZONE)
            if reference_df is not None
            else None
        ),
        reference_summary=reference_summary,
        title="Market Cashflows",
    )
    fig_cf.write_html(cashflow_html, include_plotlyjs="cdn")
    # webbrowser.open(cashflow_html.resolve().as_uri())

    # -------------------------------------------------------------------------
    # Optional cleanup: commit logs are only needed as input to this step and
    # dominate the run directory size; drop them once KPIs and plots exist.
    # -------------------------------------------------------------------------
    if not settings.postprocessing.save_commits:
        commit_csv.unlink(missing_ok=True)
        fcr_commit_csv.unlink(missing_ok=True)
        print("Commit CSVs removed (postprocessing.save_commits = false)")

    print(f"Result CSV files saved -> {run_dir.as_posix()}")
    print(f"Result HTML plots saved -> {run_dir.as_posix()}")
    print("Postprocessing finished")
