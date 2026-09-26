"""Regenerate the bundled DA/ID forecast price series for the example data.

The illustrative example's imperfect-foresight scenario (S4) lets the MPC decide
on *forecast* prices while settling on the *realized* prices. Those forecast
series are shipped as CSVs next to the realized ones:

    data/example/prices/prices_DA_example.csv   ->  prices_DA_forecast_example.csv
    data/example/prices/prices_IDC_example.csv  ->  prices_IDC_forecast_example.csv

This script documents and reproduces exactly how those forecast CSVs were built,
so their provenance lives in this repo rather than only in the upstream
price crawler. Run with no arguments to (re)write the forecast CSVs in place, or
with ``--check`` to verify that the committed CSVs still match this generator
bit-for-bit (used by tests / CI).

Error model
-----------
- Target accuracy is set *relative* to a naive weekly-persistence (D-7)
  benchmark on the DA series: MAE_target = rMAE * MAE_naive, with rMAE = 0.40
  (Lago et al. 2021, "Forecasting day-ahead electricity prices", Tab. 3).
- Gaussian noise with that MAE: for N(0, sigma), E|X| = sigma * sqrt(2/pi),
  hence sigma = MAE_target / sqrt(2/pi) = 1.253 * MAE_target.
- DA: one error draw per delivery hour, held constant across that hour's four
  quarter-hours — the day-ahead forecast is made once at D-1, so intra-hour
  structure carries no fresh information.
- ID: an independent error draw per 15-min step, reflecting continuously
  updated intraday quotes; same target MAE as DA (Chen et al. 2025, Fig. 4a:
  the average intraday path MAE is comparable to the DA MAE).

Reproducibility
---------------
sigma is derived from the DA realized series, and the RNG (numpy PCG64 via
``default_rng(SEED)``) is drawn in a fixed order — DA hourly errors first, then
ID per-step errors — so the output is a deterministic function of the realized
input CSVs. Do not reorder the draws or change SEED without regenerating and
refreshing any regression baselines.

Reproduction is exact to floating-point precision: the random draws are
bit-stable across numpy versions, but ``sigma`` scales with a pandas ``.mean()``
whose summation rounds at machine epsilon differently across library versions,
so ``--check`` allows a tiny numerical tolerance (``CHECK_TOL``) — far below any
economically meaningful level, yet far below any real model/seed change (which
moves values by >=1e-4 EUR/kWh).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
PRICES_DIR = REPO_ROOT / "data" / "example" / "prices"

RMAE_TARGET = 0.40   # rMAE vs. naive D-7 benchmark (Lago et al. 2021)
STEPS_PER_DAY = 96   # 15-min resolution
SEED = 42            # fixed -> reproducible forecasts
CHECK_TOL = 1e-9     # EUR/kWh; tolerates cross-version FP noise in sigma (~1e-16),
                     # far below any real model/seed change (>=1e-4)

# (market, realized CSV, forecast CSV). DA must come first: sigma is computed
# from the DA series and the RNG draw order (DA hourly, then ID per-step) is
# part of the reproducibility contract.
MARKETS = [
    ("DA", "prices_DA_example.csv", "prices_DA_forecast_example.csv"),
    ("ID", "prices_IDC_example.csv", "prices_IDC_forecast_example.csv"),
]


def _load(realized_csv: Path) -> pd.Series:
    return pd.read_csv(realized_csv, parse_dates=["time"], index_col="time")["price"]


def build_forecasts() -> dict[str, pd.Series]:
    """Return {market: forecast price series} reproducing the bundled CSVs."""
    rng = np.random.default_rng(SEED)

    realized = {mk: _load(PRICES_DIR / rel) for mk, rel, _ in MARKETS}
    da = realized["DA"]

    # 1) naive D-7 benchmark on the DA series -> target MAE -> Gaussian sigma
    mae_naive = (da - da.shift(7 * STEPS_PER_DAY)).abs().mean()
    mae_target = RMAE_TARGET * mae_naive
    sigma = 1.253 * mae_target

    forecasts: dict[str, pd.Series] = {}

    # 2) DA: one error per delivery hour, constant over its four quarter-hours
    hours = da.index.floor("h")
    unique_hours = hours.unique()
    err_h = pd.Series(rng.normal(0.0, sigma, size=len(unique_hours)), index=unique_hours)
    forecasts["DA"] = da + err_h.reindex(hours).to_numpy()

    # 3) ID: independent error per 15-min step (drawn AFTER the DA errors)
    idc = realized["ID"]
    forecasts["ID"] = idc + rng.normal(0.0, sigma, size=len(idc))

    print(f"MAE naive D-7: {mae_naive:.5f} | target MAE: {mae_target:.5f} | sigma: {sigma:.5f}")
    return forecasts


def _to_frame(series: pd.Series) -> pd.DataFrame:
    out = series.to_frame("price")
    out.index.name = "time"
    return out


def write(forecasts: dict[str, pd.Series]) -> None:
    for mk, _, out_rel in MARKETS:
        out_path = PRICES_DIR / out_rel
        _to_frame(forecasts[mk]).to_csv(out_path)
        print(f"wrote {out_path.relative_to(REPO_ROOT).as_posix()}")


def check(forecasts: dict[str, pd.Series]) -> bool:
    ok = True
    for mk, _, out_rel in MARKETS:
        out_path = PRICES_DIR / out_rel
        expected = _load(out_path)
        got = forecasts[mk]
        maxdiff = (got.reindex(expected.index) - expected).abs().max()
        same = bool(expected.index.equals(got.index) and maxdiff <= CHECK_TOL)
        ok = ok and same
        status = f"OK (maxdiff={maxdiff:.2e})" if same else f"MISMATCH (maxdiff={maxdiff:.3e})"
        print(f"{out_rel}: {status}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed forecast CSVs match this generator (no writes)",
    )
    args = parser.parse_args()

    forecasts = build_forecasts()
    if args.check:
        return 0 if check(forecasts) else 1
    write(forecasts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
