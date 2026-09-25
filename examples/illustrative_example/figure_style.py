"""
Shared style for the illustrative-example figures.

Sans-serif (Arial/Helvetica) 8 pt base / 7 pt small, thin axes, TUM palette
(DA = blue, ID = orange, FCR = green; market colors are reserved for per-market
quantities).

Targets the Energy Informatics (SpringerOpen) figure format: the final PDF uses
discrete widths of 85 mm (half page) or 170 mm (full page), max height 225 mm,
300 dpi at final size. Figures are designed at the final width (1:1) so 7-8 pt
lettering stays legible, use sans-serif lettering (Helvetica/Arial; Times/serif
is discouraged), keep all lines > 0.25 pt, and embed fonts (pdf.fonttype 42).
"""

from __future__ import annotations

import matplotlib.pyplot as plt

BASE_FONT_PT = 8.0
SMALL_FONT_PT = 7.0
MM_TO_INCH = 1.0 / 25.4
# SpringerOpen/Energy Informatics final-size widths; design figures at these.
FULL_WIDTH_MM = 170.0  # full page width
HALF_WIDTH_MM = 85.0  # half page width

# TUM palette
MARKET_COLORS = {
    "DA": "#0065BD",       # TUMBlue
    "ID": "#E37222",       # Orange
    "FCR": "#A2AD00",      # Olive green — FCR capacity revenue
    "FCR activation": "#69BE28",  # Lime green — reBAP settlement of FCR droop energy
    "Fees & imbalance": "#999999",  # Gray
}
# Neutral gray tones per market setup (scenario-level encodings, light to dark
# with increasing market access); market colors stay reserved for markets.
SETUP_GRAYS = {
    "DA": "#DAD7CB",  # LightGray
    "DA+ID": "#999999",  # Gray
    "DA+ID+FCR": "#6A757E",  # tum-grey-4
}
BAND_GRAY = "#DAD7CB"  # flexibility-band fill (TUM LightGray)
GRID_KW = {"axis": "y", "color": "#bfbfbf", "linewidth": 0.5, "alpha": 0.8}


def apply_paper_style() -> None:
    """Apply the shared serif figure style (print-oriented, editable SVG text)."""
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
            "mathtext.fontset": "stixsans",
            "font.size": BASE_FONT_PT,
            "axes.labelsize": BASE_FONT_PT,
            "xtick.labelsize": BASE_FONT_PT,
            "ytick.labelsize": BASE_FONT_PT,
            "legend.fontsize": SMALL_FONT_PT,
            "axes.linewidth": 0.6,
            "xtick.major.width": 0.6,
            "ytick.major.width": 0.6,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            # keep SVG text as text (not paths) so figures stay editable
            "svg.fonttype": "none",
        }
    )
