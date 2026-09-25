"""
Two-panel comparison figure of the illustrative-example scenarios S1-S4
(190 mm double column).

Panel (a): gross profit relative to uncontrolled charging at the static
reference price (S0), i.e. the `total_potential_gross_profit_delta_eur` KPI,
with "+X € / +Y %" annotations on the bars. Bars use neutral gray tones per
market setup — the TUM market colors are reserved for per-market quantities
(panel (b) and the detail figure); S4 is hatched to mark imperfect price
foresight (redundant encoding for grayscale print). Panel (b): stacked
cashflow composition per scenario (DA, ID, FCR capacity revenue, fees &
imbalance) with the net cashflow marker and the S0 reference line, y-axis
symmetric around zero.

Style and palette are shared via figure_style.py.

Requires the optional plotting dependency:  pip install -e .[paper]

Usage:
    python examples/illustrative_example/plot_comparison.py [comparison.csv]
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import matplotlib.colors as mcolors
    import matplotlib.patheffects as mpe
    import matplotlib.pyplot as plt
    from figure_style import (
        BASE_FONT_PT,
        GRID_KW,
        MARKET_COLORS,
        MM_TO_INCH,
        SETUP_GRAYS,
        SMALL_FONT_PT,
        apply_paper_style,
    )
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
except ImportError:
    sys.exit("matplotlib is required for the figure scripts: pip install -e .[paper]")

DEFAULT_CSV = Path("results/illustrative_example/comparison.csv")

FORECAST_HATCH = "///"
HATCH_COLOR = "#F0F0F0"  # near-white hatch, visible on the dark setup gray


def _panel_advantage(ax, df: pd.DataFrame) -> None:
    """(a) Cost advantage vs. S0 in EUR, percent annotated on the bars."""
    advantage = df["cost_advantage_eur"]

    x = range(len(df))
    bars = ax.bar(
        x,
        advantage,
        width=0.6,
        color=[SETUP_GRAYS[mk] for mk in df["markets"]],
        edgecolor="black",
        linewidth=0.6,
    )
    for bar, fc in zip(bars, df["price_foresight"]):
        if fc == "forecast":
            bar.set_hatch(FORECAST_HATCH)
            # Matplotlib couples the hatch color to the edgecolor; override the
            # private attribute so the black edge keeps a light hatch.
            bar._hatch_color = mcolors.to_rgba(HATCH_COLOR)

    for xi, (adv, pct) in enumerate(zip(advantage, df["cost_advantage_pct"])):
        ax.annotate(
            f"{adv:+.0f} €\n{pct:+.0f} %",
            (xi, adv),
            textcoords="offset points",
            xytext=(0, 2),
            ha="center",
            va="bottom",
            fontsize=SMALL_FONT_PT,
        )

    # Unidirectional-fleet companion runs (#8): overlay each scenario's
    # unidirectional advantage as a horizontal level marker inside its
    # (bidirectional) bar. The gap between marker and bar top is the value of
    # bidirectionality. A white outline keeps the black marker legible on both
    # the dark setup gray and the hatched S4 bar. Skipped when the column is
    # absent or empty, so the figure still renders from a bidirectional-only run.
    uni = df["cost_advantage_uni_eur"] if "cost_advantage_uni_eur" in df.columns else None
    drew_uni = False
    if uni is not None:
        half_w = 0.30  # matches the 0.6 bar width
        for xi, yv in enumerate(uni):
            if pd.isna(yv):
                continue
            ax.hlines(
                yv,
                xi - half_w,
                xi + half_w,
                color="black",
                lw=1.6,
                zorder=6,
                path_effects=[mpe.withStroke(linewidth=3.2, foreground="white")],
            )
            drew_uni = True

    ax.axhline(0.0, color="black", lw=0.5)
    ax.set_xticks(list(x))
    ax.set_xticklabels(
        [f"{sc}\n{mk}" for sc, mk in zip(df["scenario"], df["markets"])], fontsize=SMALL_FONT_PT
    )
    ax.set_ylabel("Cost reduction vs. static price charging\nfor one-month period (€)")
    ax.margins(y=0.18)
    ax.grid(**GRID_KW)
    ax.set_axisbelow(True)

    forecast_patch = Patch(
        facecolor=SETUP_GRAYS["DA+ID+FCR"],
        edgecolor="black",
        hatch=FORECAST_HATCH,
        label="Imperfect foresight",
    )
    forecast_patch._hatch_color = mcolors.to_rgba(HATCH_COLOR)
    handles = [forecast_patch]
    if drew_uni:
        handles.append(
            Line2D(
                [0],
                [0],
                color="black",
                lw=1.6,
                path_effects=[mpe.withStroke(linewidth=3.2, foreground="white")],
                label="Unidirectional fleet",
            )
        )
    ax.legend(handles=handles, frameon=False, loc="upper left")


def _panel_composition(ax, df: pd.DataFrame) -> None:
    """(b) Stacked cashflow composition per scenario + net cashflow + S0 line."""
    components = {
        "DA": df["da_cashflow_eur"],
        "ID": df["id_cashflow_eur"],
        "FCR": df["fcr_revenue_eur"] + df.get("fcr_activation_cf_eur", pd.Series(0.0, index=df.index)),
        "Fees & imbalance": df["fees_eur"] + df["imb_cost_eur"],
    }
    net = -df["total_energy_cost_eur"]  # gross profit (negative = net cost)
    ref = -float(df["ref_cost_s0_eur"].iloc[0])  # S0 gross profit

    x = range(len(df))
    pos_base = [0.0] * len(df)
    neg_base = [0.0] * len(df)
    for label, values in components.items():
        bottoms = [pb if v >= 0 else nb for v, pb, nb in zip(values, pos_base, neg_base)]
        ax.bar(
            x,
            values,
            bottom=bottoms,
            width=0.6,
            label=label,
            color=MARKET_COLORS[label],
            edgecolor="black",
            linewidth=0.4,
        )
        pos_base = [pb + max(v, 0.0) for v, pb in zip(values, pos_base)]
        neg_base = [nb + min(v, 0.0) for v, nb in zip(values, neg_base)]

    ax.scatter(x, net, marker="D", s=18, color="black", zorder=5, label="Net cashflow")
    ax.axhline(ref, color="black", lw=1.4, ls="--", label="S0 (static price)")
    ax.axhline(0.0, color="black", lw=0.5)

    ax.set_xticks(list(x))
    ax.set_xticklabels(
        [f"{sc}\n{mk}" for sc, mk in zip(df["scenario"], df["markets"])], fontsize=SMALL_FONT_PT
    )
    ax.set_ylabel("Cashflow for one-month period (€)")
    # Symmetric y-axis: zero line in the middle (S0 line and stack tops included)
    extremes = [float(min(neg_base)), float(max(pos_base)), ref, float(net.min()), float(net.max())]
    limit = 1.25 * max(abs(v) for v in extremes)
    ax.set_ylim(-limit, limit)
    ax.grid(**GRID_KW)
    ax.set_axisbelow(True)
    # Column-wise legend order: [Net, S0, Fees | DA, ID, FCR] so the market
    # patches share one column.
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    order = ["Net cashflow", "S0 (static price)", "Fees & imbalance", "DA", "ID", "FCR"]
    ax.legend(
        [by_label[lb] for lb in order],
        order,
        frameon=False,
        ncol=2,
        loc="upper left",
        columnspacing=1.2,
        handletextpad=0.5,
    )


def main() -> int:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not csv_path.exists():
        print(f"ERROR: comparison table not found: {csv_path} (run run_all first)", file=sys.stderr)
        return 1

    df = pd.read_csv(csv_path)

    apply_paper_style()
    fig, (ax_a, ax_b) = plt.subplots(1, 2, figsize=(190 * MM_TO_INCH, 75 * MM_TO_INCH))

    _panel_advantage(ax_a, df)
    _panel_composition(ax_b, df)
    for ax, label in ((ax_a, "(a)"), (ax_b, "(b)")):
        ax.set_title(label, fontsize=BASE_FONT_PT, loc="left")

    fig.tight_layout(w_pad=2.0)

    fig_dir = csv_path.parent / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    failed = False
    for ext in ("pdf", "svg", "png"):
        out = fig_dir / f"comparison.{ext}"
        try:
            fig.savefig(out)
            print(f"saved -> {out}")
        except PermissionError:
            print(f"ERROR: cannot write {out} (file open in another program?)", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
