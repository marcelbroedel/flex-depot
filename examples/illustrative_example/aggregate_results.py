"""
Aggregate the illustrative-example runs (S1-S4) into a single comparison table.

Reads the run index written by run_all.sh / run_all.bat (scenario, run_dir,
runtime_s), collects kpis.csv and the settings.toml snapshot from each run
directory, and writes results/illustrative_example/comparison.csv plus a
formatted markdown table to stdout. Uses only values already present in the
run outputs.

Usage:
    python examples/illustrative_example/aggregate_results.py [run_index.csv]
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import tomllib

DEFAULT_INDEX = Path("results/illustrative_example/run_index.csv")

# KPIs that every scenario run must provide; anything else is market-specific
# and defaults to 0 (market disabled) or empty (forecast MAE).
_REQUIRED_KPIS = (
    "gross_profit_eur",
    "ref_energy_cost_eur",
    "total_potential_gross_profit_delta_eur",
    "fees_eur",
    "imb_cost_eur",
    "pass2_steps",
    "pass2_fraction_pct",
    "price_foresight",
)


def _markets_label(settings: dict) -> str:
    opt = settings["optimization"]
    parts = []
    if opt["markets"]["dayahead"]["enabled"]:
        parts.append("DA")
    if opt["markets"]["intraday"]["enabled"]:
        parts.append("ID")
    if opt["trading"]["fcr"]["enabled"]:
        parts.append("FCR")
    return "+".join(parts)


def _scenario_row(scenario: str, run_dir: Path, runtime_s: float) -> dict:
    kpi_csv = run_dir / "kpis.csv"
    settings_toml = run_dir / "settings.toml"
    if not kpi_csv.exists():
        raise FileNotFoundError(f"{scenario}: no kpis.csv in {run_dir} (postprocessing incomplete?)")
    if not settings_toml.exists():
        raise FileNotFoundError(f"{scenario}: no settings.toml snapshot in {run_dir}")

    kpis = pd.read_csv(kpi_csv).iloc[0].to_dict()
    with open(settings_toml, "rb") as f:
        settings = tomllib.load(f)

    missing = [k for k in _REQUIRED_KPIS if k not in kpis]
    if missing:
        raise ValueError(f"{scenario}: kpis.csv in {run_dir} is missing required KPIs: {missing}")

    ref_cost = float(kpis["ref_energy_cost_eur"])
    advantage = float(kpis["total_potential_gross_profit_delta_eur"])

    return {
        "scenario": scenario.upper(),
        "markets": _markets_label(settings),
        "price_foresight": str(kpis["price_foresight"]),
        "total_energy_cost_eur": -float(kpis["gross_profit_eur"]),
        "ref_cost_s0_eur": ref_cost,
        "cost_advantage_eur": advantage,
        "cost_advantage_pct": 100.0 * advantage / ref_cost if ref_cost else float("nan"),
        "da_cashflow_eur": float(kpis.get("da_cashflow_eur", 0.0)),
        "id_cashflow_eur": float(kpis.get("id_cashflow_eur", 0.0)),
        "fcr_revenue_eur": float(kpis.get("fcr_revenue_eur", 0.0)),
        "fcr_activation_cf_eur": float(kpis.get("fcr_activation_cf_eur", 0.0)),
        "fees_eur": float(kpis["fees_eur"]),
        "imb_cost_eur": float(kpis["imb_cost_eur"]),
        "pass2_steps": int(kpis["pass2_steps"]),
        "pass2_fraction_pct": float(kpis["pass2_fraction_pct"]),
        "da_forecast_mae_eur_per_kwh": kpis.get("da_forecast_mae_eur_per_kwh", ""),
        "id_forecast_mae_eur_per_kwh": kpis.get("id_forecast_mae_eur_per_kwh", ""),
        "solver": settings["simulation"]["solver"],
        "runtime_s": float(runtime_s),
    }


def _print_markdown(df: pd.DataFrame) -> None:
    def fmt(v) -> str:
        if isinstance(v, float):
            if pd.isna(v):
                return ""
            # keep small magnitudes (e.g. forecast MAE in EUR/kWh) readable
            return f"{v:.4f}" if 0 < abs(v) < 0.1 else f"{v:.2f}"
        return "" if (v is None or (isinstance(v, str) and not v)) else str(v)

    cols = list(df.columns)
    cells = [[fmt(v) for v in row] for row in df.itertuples(index=False)]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(cols)]
    print("| " + " | ".join(c.ljust(w) for c, w in zip(cols, widths)) + " |")
    print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for row in cells:
        print("| " + " | ".join(v.ljust(w) for v, w in zip(row, widths)) + " |")


def main() -> int:
    index_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INDEX
    if not index_path.exists():
        print(f"ERROR: run index not found: {index_path}", file=sys.stderr)
        return 1

    index = pd.read_csv(index_path)
    # Build one row per run, keyed by the raw scenario id (e.g. "s1", "s1_uni").
    rows_by_id = {
        str(r["scenario"]): _scenario_row(
            str(r["scenario"]), Path(str(r["run_dir"])), float(r["runtime_s"])
        )
        for _, r in index.iterrows()
    }

    # Merge each unidirectional companion (<sid>_uni) into its bidirectional base
    # row as extra columns, so comparison.csv keeps one row per scenario and the
    # figure can overlay the unidirectional advantage. Runs without a matching
    # companion get NaN (column stays present for a stable schema).
    UNI_SUFFIX = "_uni"
    base_ids = [sid for sid in rows_by_id if not sid.endswith(UNI_SUFFIX)]
    records = []
    for sid in base_ids:
        row = rows_by_id[sid]
        uni = rows_by_id.get(sid + UNI_SUFFIX)
        if uni is not None:
            # Fairness premise of #8: uni and bi are compared against the *same*
            # S0 anchor (identical driving energy and static price).
            if abs(uni["ref_cost_s0_eur"] - row["ref_cost_s0_eur"]) > 1e-6:
                print(
                    f"WARNING: {sid}: S0 reference differs between bidirectional "
                    f"({row['ref_cost_s0_eur']:.2f}) and unidirectional "
                    f"({uni['ref_cost_s0_eur']:.2f}) run - the advantage overlay is "
                    "only comparable if both share Ref_driving_energy_kWh and price.",
                    file=sys.stderr,
                )
            row["cost_advantage_uni_eur"] = uni["cost_advantage_eur"]
            row["cost_advantage_uni_pct"] = uni["cost_advantage_pct"]
        else:
            row["cost_advantage_uni_eur"] = float("nan")
            row["cost_advantage_uni_pct"] = float("nan")
        records.append(row)
    df = pd.DataFrame(records)

    # The uncontrolled-charging reference (S0) uses identical band data and the
    # same static price in every scenario, so it must be identical everywhere.
    ref = df["ref_cost_s0_eur"]
    if not ((ref - ref.iloc[0]).abs() < 1e-6).all():
        print(
            "WARNING: reference cost S0 differs between scenarios "
            f"({ref.tolist()}) - check that all TOMLs share band data, window and static price.",
            file=sys.stderr,
        )

    out_csv = index_path.parent / "comparison.csv"
    df.to_csv(out_csv, index=False)
    print(f"Comparison table written -> {out_csv}\n")
    _print_markdown(df)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
