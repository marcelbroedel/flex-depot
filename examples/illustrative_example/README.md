# Illustrative example

Four scenarios of increasing capability, all on the bundled example depot
(identical flexibility bands, fees, penalties, terminal condition, HiGHS
solver) over the same one-month window **2026-02-01 to 2026-03-01** (the first
full calendar month covered by every bundled data series). All scenarios are
evaluated against the uncontrolled-charging reference cost S0, which the
postprocessing computes as `reference_driving_energy_costs` (identical across
scenarios by construction).

| Scenario | Markets      | Price foresight | Config              |
|----------|--------------|-----------------|---------------------|
| S1       | DA           | perfect         | `settings_s1.toml`  |
| S2       | DA + ID      | perfect         | `settings_s2.toml`  |
| S3       | DA + ID + FCR| perfect         | `settings_s3.toml`  |
| S4       | DA + ID + FCR| forecast        | `settings_s4.toml`  |

Gate-closure logic (`trading.mode = "realistic"`) is always enabled; it is not
a scenario variable.

Each scenario also has a **unidirectional companion** (`settings_s1_uni.toml` …
`settings_s4_uni.toml`), identical except that it points at the unidirectional
flexibility band (`vb_bounds_example_uni.csv`, no discharge/export).
The runner computes all eight; `aggregate_results.py` pairs each `*_uni` run
into its bidirectional row so the comparison figure can overlay the
unidirectional result (the gap = value of bidirectionality).

The bundled quick-start config (`src/flex_dep_opt/config/settings_quickstart.toml`,
run via `run_quickstart.sh` / `run_quickstart.bat`) is the **4-day detail window** of
this example: the S3 setup over Fri 2026-02-06 to Tue 2026-02-10 (two weekdays,
two weekend days). It runs in a few minutes, doubles as the golden-KPI
regression test (`tests/test_example_regression.py`), and is the run behind the
paper's time-series detail figure. Note that a standalone 4-day run starts
"cold" (energy state at band midpoint, no day-ahead commitments for day 1, so
day 1 trades intraday only) — this warm-up is inherent to a receding-horizon
start and fades after the first day.

## S4 forecast calibration

S4 models imperfect price foresight synthetically, without any external forecast
CSV. Each scheduled market carries a `forecast_error` block that perturbs only
the **decision** prices the MPC optimizes on, while settlement (cashflows, KPIs)
always uses the realized series:

```
decision[t] = settlement[t] + sigma * z[t],   z ~ AR(1) with unit variance
```

`z` is a stationary AR(1) shape with lag-1 autocorrelation `rho = 0.944` per
15-min step, so the error path is autocorrelated (realistic) rather than white
noise — this avoids spurious high-frequency arbitrage churn against phantom
step-to-step price swings. `sigma` is the forecast RMSE in EUR/MWh, set to the
midpoint of the state-of-the-art literature band (DA ~5–20 → **12.5**, ID ~3–9 →
**6.0** EUR/MWh; k ≈ 0.20 of each market's null-skill ceiling — std(p_DA) for DA,
RMS(p_ID − p_DA) for ID). DA and ID use distinct seeds so they draw independent
shapes. `sigma = 0` (or `enabled = false`) reproduces perfect foresight (S3)
bit-for-bit.

The implementation lives in `src/flex_dep_opt/market/forecast_error.py`
(`draw_ar1_shape`, `perturb_prices`); `tests/test_forecast_error.py` guards the
reproducibility, unit-variance, target-RMSE and autocorrelation properties.

## Modelling note: FCR activation energy and reBAP

FCR activation changes the fleet's grid power flow relative to its scheduled
DA/ID position. This deviation — `p_droop = -droop_signal × x_fcr` in the
model (import-positive convention) — creates a balancing-group (BKV)
imbalance that settles at the reBAP price every quarter-hour:

- **Upward FCR** (f < 50 Hz, export): BKV Überdeckung → earns `IMB_NEG` price
- **Downward FCR** (f > 50 Hz, import): BKV Unterdeckung → pays `IMB_POS` price

The optimizer prices this in PASS-1 via `obj_fcr_activation_cashflow`
(using the same reBAP series as PASS-2 imbalance settlement), so the
dispatch correctly accounts for the settlement cost/revenue on every step
with active FCR. The aggregate is reported as `fcr_activation_cf_eur` in
`kpis.csv` and shown as the "FCR activation" component in panel (b) of the
comparison figure.

Over a full frequency cycle, upward and downward activations roughly balance
in energy (FCR is a symmetric product). The net reBAP cashflow is therefore
small relative to the FCR capacity revenue; its sign depends on the actual
frequency profile of the simulation window. Not modelled (and thus
conservative): pre-scheduling of expected FCR activation energy on the ID
market (which would reduce reBAP exposure), deadband utilization and
permitted over-fulfilment (up to 120 %) for SoC steering during delivery,
and pooling across several depots.

## How to run

From the repository root (activated environment):

```bash
bash examples/illustrative_example/run_all.sh     # Linux/macOS/Git Bash
examples\illustrative_example\run_all.bat         # Windows cmd
```

The runner executes S1 → S4 and their unidirectional companions S1_uni → S4_uni
sequentially via `python -m flex_dep_opt run-sim / run-post` (the same entry
point as `run_quickstart.sh`), aborts on the first failure, and wall-clock-times
each scenario. Expect **~30–45 min per scenario**, i.e. ~4–6 h for all eight,
with HiGHS. All outputs land in `results/illustrative_example/`:

```
results/illustrative_example/
    s1/ s2/ s3/ s4/               # bidirectional run directories
    s1_uni/ s2_uni/ s3_uni/ s4_uni/   # unidirectional companions
    detail_4day/          # quick-start run (see "Paper figures")
    run_index.csv         # scenario -> run dir + runtime
    comparison.csv        # aggregated comparison table (uni paired in)
    figures/              # paper figures (PDF/SVG)
```

`aggregate_results.py` (invoked automatically, re-runnable standalone) writes
`comparison.csv` and prints a markdown table with, per scenario: markets,
foresight, total energy cost, reference cost S0, cost advantage vs. S0 (EUR
and %), DA/ID cashflows, FCR capacity revenue, trading fees, imbalance cost,
PASS2 statistics, forecast MAEs (S4), solver and runtime.

A single scenario can be run standalone, e.g.:

```bash
python -m flex_dep_opt run-sim  --config examples/illustrative_example/settings_s2.toml --run-dir results/illustrative_example/s2
python -m flex_dep_opt run-post --config examples/illustrative_example/settings_s2.toml --run-dir results/illustrative_example/s2
```

## Paper figures

Both figure scripts need the optional plotting extra: `pip install -e .[paper]`

```bash
# Two-panel bar chart S1-S4 (170 mm): (a) gross profit vs. static price charging
# in EUR and %, (b) stacked cashflow composition per market
python examples/illustrative_example/plot_comparison.py

# 4-day time-series detail (positions, energy state in band, cumulative profit)
# from the quick-start run:
python -m flex_dep_opt run-sim  --config src/flex_dep_opt/config/settings_quickstart.toml --run-dir results/illustrative_example/detail_4day
python -m flex_dep_opt run-post --config src/flex_dep_opt/config/settings_quickstart.toml --run-dir results/illustrative_example/detail_4day
python examples/illustrative_example/plot_detail.py
```

## Expected results

Produced with HiGHS on 2026-09-26 (runtimes on a standard desktop machine: 13th Gen Intel(R) Core(TM) i7 1.90 GHz, 32 GB RAM);
regenerate with `aggregate_results.py`, which prints this table ready to paste.
Small deviations across HiGHS versions/platforms are possible (near-degenerate
optima); the qualitative ordering S1 < S2 < S4 < S3 should be robust. The last
two columns carry each scenario's cost advantage for the unidirectional
companion run (`*_uni`); the bidirectional − unidirectional gap is the value of
bidirectionality.

| scenario | markets   | price_foresight | total_energy_cost_eur | ref_cost_s0_eur | cost_advantage_eur | cost_advantage_pct | da_cashflow_eur | id_cashflow_eur | fcr_revenue_eur | fcr_activation_cf_eur | fees_eur | imb_cost_eur | pass2_steps | pass2_fraction_pct | da_forecast_mae_eur_per_kwh | id_forecast_mae_eur_per_kwh | solver | runtime_s | cost_advantage_uni_eur | cost_advantage_uni_pct |
|----------|-----------|-----------------|-----------------------|-----------------|--------------------|--------------------|-----------------|-----------------|-----------------|-----------------------|----------|--------------|-------------|--------------------|-----------------------------|-----------------------------|--------|-----------|------------------------|------------------------|
| S1       | DA        | perfect         | 3100.79               | 3406.87         | 902.73             | 26.50              | -1792.89        | 0.00            | 0.00            | 0.00                  | -3.26    | 0.00         | 0           | 0.00               |                             |                             | highs  | 665.00    | 432.41                 | 12.69                  |
| S2       | DA+ID     | perfect         | 2608.90               | 3406.87         | 1394.62            | 40.94              | 332.07          | -965.97         | 0.00            | 0.00                  | -7.37    | 0.00         | 0           | 0.00               |                             |                             | highs  | 990.00    | 595.01                 | 17.47                  |
| S3       | DA+ID+FCR | perfect         | 1169.31               | 3406.87         | 2834.21            | 83.19              | 53.89           | -1106.14        | 1958.88         | -28.00                | -7.22    | -46.66       | 73          | 2.71               |                             |                             | highs  | 2274.00   | 595.01                 | 17.47                  |
| S4       | DA+ID+FCR | forecast        | 1671.12               | 3406.87         | 2332.40            | 68.46              | 465.88          | -1395.53        | 1913.42         | -14.78                | -9.37    | -27.23       | 56          | 2.08               | 0.0100                      | 0.0047                      | highs  | 2393.00   | 544.33                 | 15.98                  |

Reading aid: `total_energy_cost_eur` is the depot's net operating cost after
market earnings and the battery-cycling (aging) cost; it stays well below the
S0 reference (3406.87 EUR) in every scenario, so the cost advantage is positive
throughout. The imperfect-foresight scenario S4 lands below S3 (the same setup
with perfect foresight): the AR(1) forecast error triggers modestly more
trading throughput and thus higher aging cost — the price of imperfect
foresight. The S3/S4 PASS2 steps arise from FCR activation periods: droop
energy creates a net reBAP imbalance that can make the market-balanced PASS1
constraint infeasible, triggering the PASS2 fallback. The negative
`fcr_activation_cf_eur` in S3/S4 reflects that the actual frequency profile
of February 2026 resulted in a net reBAP cost from FCR activation (symmetric
product, but skewed frequency distribution); the optimizer accounts for this
cost in PASS1 via `obj_fcr_activation_cashflow`. The unidirectional advantage
for S3 equals S2's (FCR adds essentially no value without discharge capability).
