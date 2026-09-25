"""
Time-series detail figure of the illustrative example (4-day window,
170 mm full page width, five rows).

Reads dispatch.csv and the settings.toml snapshot from a run directory
(default: the 4-day quick-start run in
results/illustrative_example/detail_4day, i.e. the bundled
settings_quickstart.toml — scenario S3 setup over Fri 2026-02-06 to Tue
2026-02-10) and plots:

  (a) DA / ID prices
  (b) FCR capacity prices (one price per 4 h slot)
  (c) depot power within the power band
  (d) depot energy state within the energy band
  (e) committed positions: DA / ID stacked bars (buy = solid,
      sell = translucent), FCR committed capacity as symmetric band
      (±x_fcr, one rectangle per 4 h slot), reBAP net position where
      present

Style and palette are shared via figure_style.py (market colors:
DA = blue, ID = orange, FCR = green).

Requires the optional plotting dependency:  pip install -e .[paper]

Usage:
    python examples/illustrative_example/plot_detail.py [run_dir]
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import tomllib

from flex_dep_opt.io.prices import get_fcr_prices, read_prices_csv

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import matplotlib.colors as mcolors
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from figure_style import (
        BAND_GRAY,
        BASE_FONT_PT,
        FULL_WIDTH_MM,
        GRID_KW,
        MARKET_COLORS,
        MM_TO_INCH,
        apply_paper_style,
    )
    from matplotlib.patches import Patch
except ImportError:
    sys.exit("matplotlib is required for the figure scripts: pip install -e .[paper]")

DEFAULT_RUN_DIR = Path("results/illustrative_example/detail_4day")
LOCAL_TZ = "Europe/Berlin"

IMB_COLOR = "#7850A0"  # reBAP purple
SELL_ALPHA = 0.35  # translucent = sell, solid = buy


def _read_dispatch(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    idx = pd.to_datetime(df["time"], utc=True).dt.tz_convert(LOCAL_TZ)
    return df.drop(columns=["time"]).set_index(idx)


def _load_prices(run_dir: Path, index: pd.DatetimeIndex):
    """DA/ID price series and FCR capacity prices from the run's settings snapshot."""
    with open(run_dir / "settings.toml", "rb") as f:
        cfg = tomllib.load(f)

    mk_cfg = cfg["optimization"]["markets"]
    prices: dict[str, pd.Series] = {}
    for code, det in (("DA", mk_cfg["dayahead"]), ("ID", mk_cfg["intraday"])):
        if det["enabled"]:
            prices[code] = read_prices_csv(det["source"]).tz_convert(LOCAL_TZ).reindex(index)

    fcr_cfg = cfg["optimization"]["trading"]["fcr"]
    fcr_prices = None
    if fcr_cfg["enabled"]:
        # one price per 4 h slot -> forward-fill onto the dispatch steps
        fcr_prices = (
            get_fcr_prices(fcr_cfg["prices_source"]).tz_convert(LOCAL_TZ).reindex(index, method="ffill")
        )
    return prices, fcr_prices


def main() -> int:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_RUN_DIR
    dispatch_csv = run_dir / "dispatch.csv"
    if not dispatch_csv.exists() or not (run_dir / "settings.toml").exists():
        print(
            f"ERROR: {run_dir} must contain dispatch.csv and settings.toml "
            "(run the quick-start example first, see README)",
            file=sys.stderr,
        )
        return 1

    dispatch = _read_dispatch(dispatch_csv)
    prices, fcr_prices = _load_prices(run_dir, dispatch.index)

    # tz-naive local time for plotting (bars and lines share one x basis)
    x = dispatch.index.tz_localize(None)
    x_num = mdates.date2num(x.to_pydatetime())
    bar_width = (0.25 / 24.0) * 0.9  # matplotlib datetime widths are in days

    apply_paper_style()
    # Energy Informatics full-page format: 170 mm wide (max height 225 mm).
    fig, axes = plt.subplots(5, 1, sharex=True, figsize=(FULL_WIDTH_MM * MM_TO_INCH, 195 * MM_TO_INCH))
    ax_pr, ax_fcrp, ax_pw, ax_e, ax_pos = axes

    # (a) DA / ID prices
    for code in ("DA", "ID"):
        if code in prices:
            ax_pr.step(x, prices[code] * 1000.0, where="post", lw=1.3, label=code, color=MARKET_COLORS[code])
    ax_pr.set_ylabel("Price (€/MWh)")

    # (b) FCR capacity prices
    if fcr_prices is not None:
        ax_fcrp.step(x, fcr_prices, where="post", lw=1.3, color=MARKET_COLORS["FCR"])
    ax_fcrp.set_ylabel("FCR price (€/MW)")
    ax_fcrp.set_ylim(bottom=0)
    ax_fcrp.margins(y=0.2)

    # (c) power band + net depot power
    ax_pw.fill_between(
        x, dispatch["P_lower_kw"], dispatch["P_upper_kw"], color=BAND_GRAY, step="post", label="Power band"
    )
    ax_pw.step(x, dispatch["p_net_kw"], where="post", lw=1.2, color="black", label="Net depot power")
    ax_pw.axhline(0.0, color="black", lw=0.5)
    ax_pw.set_ylabel("Power (kW)")

    # (d) energy band + energy state
    ax_e.fill_between(
        x, dispatch["E_lower_kWh"], dispatch["E_upper_kWh"], color=BAND_GRAY, label="Energy band"
    )
    ax_e.plot(x, dispatch["E_kWh"], lw=1.2, color="black", label="Energy state")
    ax_e.set_ylabel("Energy (kWh)")

    # (e) committed positions: DA/ID stacked bars (buy = solid, sell =
    # translucent) + FCR committed capacity as symmetric band; drawing is
    # shared between the main panel and the activation zoom inset
    x_fcr = dispatch.get("x_fcr_kw", pd.Series(0.0, index=dispatch.index))
    droop = dispatch.get("p_droop_kw", pd.Series(0.0, index=dispatch.index))
    position_series = [(mk, f"p_{mk.lower()}_kw", MARKET_COLORS[mk]) for mk in ("DA", "ID")]
    if "p_imb_pos_kw" in dispatch.columns and "p_imb_neg_kw" in dispatch.columns:
        dispatch["p_rebap_kw"] = dispatch["p_imb_pos_kw"] - dispatch["p_imb_neg_kw"]
        position_series.append(("reBAP", "p_rebap_kw", IMB_COLOR))

    def _draw_positions(ax, handles=None):
        if (x_fcr > 0).any():
            ax.fill_between(
                x,
                -x_fcr,
                x_fcr,
                step="post",
                color=mcolors.to_rgba(MARKET_COLORS["FCR"], 0.18),
                linewidth=0,
                zorder=1,
            )
        bottom_pos = np.zeros(len(dispatch))
        bottom_neg = np.zeros(len(dispatch))
        for mk, col, color in position_series:
            if col not in dispatch.columns:
                continue
            v = dispatch[col].to_numpy(dtype=float)
            pos = np.clip(v, 0, None)
            neg = np.clip(v, None, 0)
            # reBAP is imbalance settlement, not a traded product
            pos_lbl, neg_lbl = ("deficit", "surplus") if mk == "reBAP" else ("buy", "sell")
            if np.any(pos > 0):
                ax.bar(x_num, pos, width=bar_width, bottom=bottom_pos, color=color, linewidth=0)
                bottom_pos = bottom_pos + pos
                if handles is not None:
                    handles.append(Patch(facecolor=color, label=f"{mk} {pos_lbl}"))
            if np.any(neg < 0):
                sell_color = mcolors.to_rgba(color, SELL_ALPHA)
                ax.bar(x_num, neg, width=bar_width, bottom=bottom_neg, color=sell_color, linewidth=0)
                bottom_neg = bottom_neg + neg
                if handles is not None:
                    handles.append(Patch(facecolor=sell_color, label=f"{mk} {neg_lbl}"))
        # FCR activation (droop response): solid green bars stacked on top
        if droop.abs().max() > 0:
            v = droop.to_numpy(dtype=float)
            pos = np.clip(v, 0, None)
            neg = np.clip(v, None, 0)
            fcr_color = MARKET_COLORS["FCR"]
            if np.any(pos > 0):
                ax.bar(x_num, pos, width=bar_width, bottom=bottom_pos, color=fcr_color, linewidth=0)
            if np.any(neg < 0):
                ax.bar(x_num, neg, width=bar_width, bottom=bottom_neg, color=fcr_color, linewidth=0)
            if handles is not None:
                handles.append(Patch(facecolor=fcr_color, label="FCR activation"))
        ax.axhline(0.0, color="black", lw=0.5)

    handles_pos = []
    _draw_positions(ax_pos, handles_pos)

    # Zoom inset: the droop activation is tiny (tens of kW) against the
    # ±x_fcr band and stacked on top of the DA/ID/reBAP bars — magnify the
    # 6 h window where the activation is largest relative to the stacked
    # bar extent (the y-limit the inset needs), so the FCR bars stay visible.
    # Called after tight_layout: the inset sticks out above (e) and would
    # otherwise blow up the inter-panel spacing.
    def _add_zoom_inset():
        stack_pos = np.zeros(len(dispatch))
        stack_neg = np.zeros(len(dispatch))
        for _, col, _ in position_series:
            if col in dispatch.columns:
                v = np.nan_to_num(dispatch[col].to_numpy(dtype=float))
                stack_pos += np.clip(v, 0, None)
                stack_neg += np.clip(v, None, 0)
        v = np.nan_to_num(droop.to_numpy(dtype=float))
        stack_pos += np.clip(v, 0, None)
        stack_neg += np.clip(v, None, 0)

        droop_abs = np.abs(np.nan_to_num(droop.to_numpy(dtype=float)))
        half = pd.Timedelta(hours=3)
        best_score = -1.0
        for t_c in x[droop_abs > 0]:
            w0 = max(t_c - half, x[0])
            w1 = min(w0 + 2 * half, x[-1])
            w0 = w1 - 2 * half
            in_w = (x >= w0) & (x <= w1)
            y_ext = max(float(stack_pos[in_w].max(initial=0.0)),
                        float(-stack_neg[in_w].min(initial=0.0)), 40.0)
            score = float(droop_abs[in_w].sum()) / y_ext
            if score > best_score:
                best_score, t0, t1, y_zoom = score, w0, w1, 1.15 * y_ext

        # placed above the panel (in the gap to (d) and the free lower part
        # of (d)) so it does not cover any bars in (e)
        axins = ax_pos.inset_axes([0.36, 1.08, 0.22, 0.68])
        _draw_positions(axins)
        axins.set_xlim(t0, t1)
        axins.set_ylim(-y_zoom, y_zoom)
        axins.tick_params(labelsize=6.5, width=0.5, length=2)
        axins.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        axins.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        for spine in axins.spines.values():
            spine.set_linewidth(0.6)
        ax_pos.indicate_inset_zoom(axins, edgecolor="black", linewidth=0.6)

    if (x_fcr > 0).any():
        handles_pos.append(
            Patch(facecolor=mcolors.to_rgba(MARKET_COLORS["FCR"], 0.18), label="FCR capacity (±)")
        )
    # reBAP entries go last in the legend (rightmost column), after FCR
    handles_pos.sort(key=lambda h: h.get_label().startswith("reBAP"))
    ax_pos.set_ylabel("Committed positions (kW)")
    ax_pos.margins(y=0.15)

    # shared x-axis formatting
    ax_pos.set_xlabel(f"Time ({LOCAL_TZ})")
    ax_pos.xaxis.set_major_locator(mdates.DayLocator())
    ax_pos.xaxis.set_major_formatter(mdates.DateFormatter("%a %d.%m."))
    ax_pos.set_xlim(x[0], x[-1])

    for ax, label in zip(axes, ("(a)", "(b)", "(c)", "(d)", "(e)")):
        ax.grid(**GRID_KW)
        ax.set_axisbelow(True)
        # y=1.0 pins the title; auto-positioning would push (e) above the
        # zoom inset that sticks out of the panel
        ax.set_title(label, fontsize=BASE_FONT_PT, loc="left", y=1.0, fontweight="bold")

    # collected legend above all panels
    legend_handles = [
        plt.Line2D([], [], color=MARKET_COLORS[code], lw=1.3, label=f"{code} price")
        for code in ("DA", "ID")
        if code in prices
    ]
    if fcr_prices is not None:
        legend_handles.append(
            plt.Line2D([], [], color=MARKET_COLORS["FCR"], lw=1.3, label="FCR capacity price")
        )
    legend_handles += [
        Patch(facecolor=BAND_GRAY, label="Power/energy bounds"),
        plt.Line2D([], [], color="black", lw=1.2, label="Optimized schedule"),
        *handles_pos,
    ]
    fig.legend(
        handles=legend_handles,
        loc="upper center",
        ncol=6,
        frameon=False,
        bbox_to_anchor=(0.5, 1.0),
        columnspacing=1.2,
        handletextpad=0.5,
    )

    fig.align_ylabels(axes)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.94))

    if droop.abs().max() > 0:
        _add_zoom_inset()

    fig_dir = Path("results/illustrative_example/figures")
    fig_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    for ext in ("pdf", "svg", "png"):
        out = fig_dir / f"detail_4day.{ext}"
        try:
            fig.savefig(out)
            print(f"saved -> {out}")
        except PermissionError:
            print(f"ERROR: cannot write {out} (file open in another program?)", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
