<p align="center">
  <img src="images/flex-depot-logo2.png" width="600"><br>
  <a href="https://github.com/TUMFTM/flex-depot/actions/workflows/test.yml"><img src="https://github.com/TUMFTM/flex-depot/actions/workflows/test.yml/badge.svg" alt="Tests"></a>&nbsp;<a href="https://www.apache.org/licenses/LICENSE-2.0"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License: Apache 2.0"></a>&nbsp;<a href="https://doi.org/10.5281/zenodo.21676941"><img src="https://img.shields.io/badge/DOI-10.5281%2Fzenodo.21676941-blue.svg" alt="DOI"></a>&nbsp;<a href="https://doi.org/10.24433/CO.5623911.v2"><img src="https://img.shields.io/badge/Open%20in-Code%20Ocean-brightgreen.svg" alt="Open in Code Ocean"></a>
</p>

<p align="center">
  <em>The Code Ocean capsule runs the quick-start configuration as a reproducible example.</em>
</p>

**FLEX-DEPOT** is a Python project for optimizing flexibility at a logistics depot incorporating electric trucks. It combines market
price series, flexibility power and energy bands, and market trading rules into a single rolling-horizon optimization (MPC). 
The core of the project is a MILP/LP (depending on settings) [Pyomo](https://pyomo.readthedocs.io/en/stable/) model that decides market
positions and physical power flow between depot and public grid while respecting aggregated asset power and energy flexibility bounds and gate-closure rules.

Key capabilities:
- Unified optimization for DA/ID spot markets and FCR reserve market.
- Rolling-horizon MPC that commits market positions when gate closure is reached.
- Optional imbalance settlement (reBAP) as a feasibility fallback.
- CSV-based I/O for prices, flexibility bounds, and results.
- Plotly-based visualization of dispatch, prices, and cashflows.

## Created by
Marcel Brödel, M.Sc. <br>
Institute of Automotive Technology <br>
Department of Mobility Systems Engineering <br>
TUM School of Engineering and Design <br>
Technical University of Munich <br>
[marcel.broedel@tum.de](mailto:marcel.broedel@tum.de)

### Contributors
Tien Doan, B.Sc. - Master's student <br>
Adrian Simon Würth, B.Sc. - Master's student <br>
Woan-Ho Park, M.Sc. - Visiting research associate <br>
Philipp Rosner, M.Sc. - Research associate <br>
Roman Schade, M.Sc. - Master's student <br>
Rishabh Rai, B.Sc. - Bachelor's student <br>
Wurilege Wurilege, M.Sc. - Master's student <br>

## Licensing
FLEX-DEPOT is licensed under the [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0) open source license. <br>
The full license text can be found in the LICENSE file in the root directory of the repository.

## Related Publications
Brödel, M., Würth, A.S., Park, W.-H., Rosner, P., Lienkamp, M.: "FLEX-DEPOT: An open-source framework for multi-market flexibility commercialization of a logistics depot incorporating electric trucks." <br>
Manuscript in preperation

Park, W.-H., Brödel, M., Rosner, P., Kim, Y.-S.: "Scalable EV Flexibility Aggregation with Guaranteed Disaggregation via Fast Projection onto Reachable Sets: Application to Real Truck Fleet Data" <br>
Manuscript in revision at eTransportation, Elsevier (2026)

## Citation
If you use FLEX-DEPOT in academic work, please cite **both**:
1. The FLEX-DEPOT software repository
2. The associated publication

**Software:**
Brödel, M. (2026). FLEX-DEPOT (Version 2.0.1). GitHub repository.  
https://github.com/TUMFTM/flex-depot

**Publication:**
Brödel, M., Würth, A.S., Park, W.-H., Rosner, P., Lienkamp, M.  
*FLEX-DEPOT: An open-source framework for multi-market flexibility commercialization of a logistics depot incorporating electric trucks.*  
In preparation.

Citation metadata is provided in `CITATION.cff`.

## Repository Architecture

<img src="images/flex-depot-architecture.svg" width="100%">


## Module Description
`Configuration Layer` specifies all scenario settings, including simulation horizons, enabled markets, optimization options, and asset parameters, serving as the single entry point.

`Input/Output Layer` manages the ingestion, alignment, and validation of external time series data, such as market prices and depot-based flexibility bands, as well as the structured export of results.

`Workflow Layer` coordinates execution across time and market stages, handling repeated optimizations, market gate closures, and progressive commitment of decisions.

`Optimization Layer` contains the mathematical formulation and solver interface that convert aggregated flexibility into market positions and operational schedules under market and physical constraints.

`Domain Layer` includes domain (depot)-specific abstractions.

`Market Layer` defines market and trading rules such as gate-closures.

`Postprocessing Layer` includes result summary generation and a plotting script.

## Control Workflow
<img src="images/flex-depot-workflow.svg" width="100%">

## Installation
### 1) Clone repository
FLEX-DEPOT is available on [GitHub](https://github.com/TUMFTM/flex-depot) and can be cloned from there using
```
git clone https://github.com/TUMFTM/flex-depot
```

### 2) Create and activate a virtual environment
Recommendation: It is recommended to create and activate a clean virtual environment for the installation in the local `.venv/` directory. For Windows (PowerShell) the environment set up can be done by the following commands:
```
cd <root_directory>
py -m venv .venv
.\.venv\Scripts\activate
```
Alternative: A self-defined environment can also be set up (in this case do not forget to add `<name_of_virtual_environment>` to `.gitignore`):
```
py -m venv <name_of_virtual_environment>
.\<name_of_virtual_environment>\Scripts\activate
```
or alternatively using conda: 
```
conda create -n <name_of_conda_environment> python=3.11
conda activate <name_of_conda_environment>
```

### 3) Install the package
Install the package and its dependencies using pip:
```
python -m pip install --upgrade pip
pip install -e .
```

### 4) Solver
FLEX-DEPOT uses [Pyomo](https://pyomo.readthedocs.io/en/stable/) as the modelling interface. The open-source solver [HiGHS](https://highs.dev/) is included as a core dependency and requires no additional installation — `pip install -e .` is sufficient.

To verify that HiGHS is available:
```
python -c "import pyomo.environ as pyo; print(pyo.SolverFactory('appsi_highs').available())"
```

Optionally, the proprietary [Gurobi](https://www.gurobi.com/) solver can be used instead (faster for large problems, free academic license available). Install the Python bindings and set `solver = "gurobi"` in the TOML config:
```
pip install gurobipy==<your_gurobi_version>
```

### 5) Quick start (recommended)
The repository contains a ready-to-run quick-start script for Windows:
```
.\run_quickstart.bat
```
Or the unix shell script for Linux/macOS:
```
bash run_quickstart.sh
```
These commands will run the quick-start configuration and generate result files in the results/ directory.

### Illustrative example (paper scenarios)
Four ready-made scenario configurations of increasing capability (S1: DA only → S4: DA+ID+FCR with
imperfect price foresight) reproduce the illustrative example of the paper over a one-month window;
the quick-start example above is its 4-day detail window. See
[examples/illustrative_example/](examples/illustrative_example/) for the scenario definitions,
`run_all.sh` / `run_all.bat` (sequential execution + aggregated comparison table in
`results/illustrative_example/`), and the paper figure scripts (`pip install -e .[paper]` for
matplotlib).


## How to use
Installing the package provides the console command ```flex-depot``` (equivalently
```python -m flex_dep_opt```) with three subcommands, defined in ```cli.py```:
1. Running the simulation by ```flex-depot run-sim --config <settings.toml_file_path>```
2. Running the postprocessing by ```flex-depot run-post --config <settings.toml_file_path>```
3. Running a scenario batch by ```flex-depot run-batch <configs_dir>```: executes
   simulation + postprocessing for every ```*.toml``` in the directory (in parallel,
   ```--jobs <n>```, default: all CPU cores) and writes a KPI manifest CSV
   (```--manifest <path>```, default: ```results/batches/<batch>__<timestamp>/manifest.csv```).
   If the directory contains a ```default.toml```, each config is deep-merged onto it,
   so per-run TOMLs only need to state the parameters that differ.

```run-sim``` and ```run-post``` accept an optional ```--run-dir <directory>``` to write to / read from a fixed run
directory instead of the default timestamped ```results/<name>__<timestamp>/```. If ```--config``` is
omitted, ```run-sim``` uses the bundled ```settings_quickstart.toml``` and ```run-post``` postprocesses the
latest run (from ```results/LATEST.txt```).

Note: data paths inside the TOML configs are resolved relative to the current working
directory — run the commands from the repository root (as ```run_quickstart.bat``` / ```run_quickstart.sh``` do).

Within the TOML file all parameters for a simulation run can be set: 

Simulation settings:

| Parameter                     | Type   | Description                                                | Valid values / format    |
|------------------------------|--------|------------------------------------------------------------|--------------------------|
| simulation.start             | str    | Simulation start time (inclusive)                          | YYYY-MM-DD HH:MM         |
| simulation.end               | str    | Simulation end time (inclusive)                            | YYYY-MM-DD HH:MM         |
| simulation.timestep_hours    | float  | Simulation timestep in hours                               | 0.25 (others not tested) |
| simulation.name              | str    | Identifier used for naming result files                    | arbitrary string         |
| simulation.solver            | str    | Optimization solver backend                                | {highs, gurobi, cbc}     |
| simulation.solver_threads    | int    | Solver thread count (set 1 for parallel batch runs)        | ≥ 1 or unset (default 8) |
| simulation.solver_mip_gap    | float  | Relative MIP gap termination criterion (optional)          | > 0 or unset             |
| simulation.solver_time_limit_s | int  | Solver time limit per optimization (optional)              | ≥ 1 (seconds) or unset   |

Market configuration: 

| Parameter                                           | Type   | Description                                             | Valid values / format |
|----------------------------------------------------|--------|---------------------------------------------------------|-----------------------|
| optimization.markets.dayahead.enabled              | bool   | Enable day-ahead market participation                   | true / false          |
| optimization.markets.dayahead.source               | str    | CSV file with day-ahead price time series               | path to CSV           |
| optimization.markets.dayahead.forecast_source      | str    | Optional CSV with price forecast used for MPC decisions; if omitted, realized prices are used (perfect foresight, upper-bound potential). Settlement always uses `source` | path to CSV (optional) |
| optimization.markets.dayahead.fee_eur_per_kwh      | float  | Transaction fee applied to day-ahead trades             | ≥ 0 (€/kWh)           |
| optimization.markets.intraday.enabled              | bool   | Enable intraday market participation                    | true / false          |
| optimization.markets.intraday.source               | str    | CSV file with intraday price time series                | path to CSV           |
| optimization.markets.intraday.forecast_source      | str    | Optional CSV with price forecast used for MPC decisions; if omitted, realized prices are used (perfect foresight, upper-bound potential). Settlement always uses `source` | path to CSV (optional) |
| optimization.markets.intraday.fee_eur_per_kwh      | float  | Transaction fee applied to intraday trades              | ≥ 0 (€/kWh)           |

Trading rules: 

| Parameter                                                     | Type   | Description                                              | Valid values / format |
|--------------------------------------------------------------|--------|----------------------------------------------------------|-----------------------|
| optimization.trading.mode                                   | str    | Trading mode controlling market interaction realism      | {none, realistic}    |
| optimization.trading.dayahead.gate_closure_hour             | str    | Day-ahead gate closure time                              | HH:MM                |
| optimization.trading.dayahead.closes_previous_day           | bool   | Whether DA closes on day D-1                              | true / false         |
| optimization.trading.intraday.offset_minutes_before_delivery| int    | Intraday trading offset before delivery                  | ≥ 0 (minutes)        |

FCR reserve market:

| Parameter                                                       | Type   | Description                                              | Valid values / format   |
|----------------------------------------------------------------|--------|----------------------------------------------------------|-------------------------|
| optimization.trading.fcr.enabled                               | bool   | Enable FCR reserve market participation                  | true / false            |
| optimization.trading.fcr.prices_source                         | str    | Excel file with FCR capacity prices (one price per slot) | path to .xlsx           |
| optimization.trading.fcr.frequency_source                      | str    | CSV file with grid frequency time series                 | path to CSV             |
| optimization.trading.fcr.breakeven_analysis                    | bool   | Report the FCR capacity price at which bidding breaks even (extra diagnostic solves) | true / false |
| optimization.trading.fcr.breakeven_include_zero_bid            | bool   | Include the zero-bid alternative in the break-even analysis | true / false          |
| optimization.trading.fcr.gate_closure_hour                     | str    | FCR gate closure time                                    | HH:MM                   |
| optimization.trading.fcr.gate_closure_closes_previous_day      | bool   | Whether FCR gate closes on day D-1                       | true / false            |
| optimization.trading.fcr.gate_closure_timezone                 | str    | Timezone for gate closure evaluation                     | e.g. "Europe/Berlin"    |
| optimization.trading.fcr.product_hours                         | float  | Duration of one FCR product slot                         | 4.0 (hours)             |
| optimization.trading.fcr.bid_block_mw                          | float  | Minimum bid increment                                    | ≥ 0 (MW)                |
| optimization.trading.fcr.energy_reserve_minutes                | float  | Required energy reserve per MW of FCR capacity           | ≥ 0 (minutes)           |
| optimization.trading.fcr.reserve_penalty_eur_per_kwh           | float  | Penalty on violating the FCR energy reserve (soft constraint) | ≥ 0 (€/kWh)        |
| optimization.trading.fcr.balance_penalty_eur_per_kwh           | float  | Soft mid-band pull on the energy state during FCR slots (keeps distance to the reserve edge) | ≥ 0 (€/kWh) |
| optimization.trading.fcr.frequency_nominal_hz                  | float  | Nominal grid frequency                                   | 50.0 (Hz)               |
| optimization.trading.fcr.deadband_hz                           | float  | Frequency deadband around nominal                        | ≥ 0 (Hz)                |
| optimization.trading.fcr.full_activation_hz                    | float  | Frequency deviation for full activation                  | ≥ deadband_hz (Hz)      |

Imbalance settlement:

| Parameter                                                      | Type   | Description                                              | Valid values / format |
|---------------------------------------------------------------|--------|----------------------------------------------------------|-----------------------|
| optimization.imbalance.enabled                                | bool   | Enable imbalance settlement as fallback                  | true / false         |
| optimization.imbalance.source_pos                             | str    | CSV file with positive imbalance prices                  | path to CSV          |
| optimization.imbalance.source_neg                             | str    | CSV file with negative imbalance prices                  | path to CSV          |
| optimization.imbalance.imbalance_volume_penalty_eur_per_kwh   | float  | Penalty on imbalance energy to discourage usage          | ≥ 0 (€/kWh)          |

Optimization logic:

| Parameter                                   | Type   | Description                                             | Valid values / format |
|--------------------------------------------|--------|---------------------------------------------------------|-----------------------|
| optimization.virtual_arbitrage             | bool   | Allow offsetting buy/sell across markets                | true / false         |
| optimization.mpc.da_horizon_hours          | int    | MPC prediction horizon for day-ahead optimization       | ≥ 1 (hours)          |
| optimization.mpc.id_horizon_hours          | int    | MPC prediction horizon for intraday optimization        | ≥ 1 (hours)          |
| optimization.mpc.fcr_price_horizon_hours   | int    | Horizon of known FCR capacity prices ahead of each MPC step | ≥ 1 (hours)      |
| optimization.mpc.fcr_frequency_horizon_minutes | int | Horizon of known frequency/activation signal inside the MPC window (0 = unknown) | ≥ 0 (minutes) |
| optimization.mpc.terminal_condition        | bool   | Enable soft terminal energy condition                   | true / false         |
| optimization.mpc.terminal_weight_eur_per_kwh | float | Weight for terminal energy deviation penalty            | ≥ 0 (€/kWh)          |

Flexibility inputs: 

| Parameter                                                        | Type   | Description                                              | Valid values / format |
|-----------------------------------------------------------------|--------|----------------------------------------------------------|-----------------------|
| optimization.flexibility.bounds_file                            | str    | CSV file with aggregated power and energy flexibility bands | path to CSV       |
| optimization.flexibility.cycle_regularization.enabled           | bool   | Enable cycling regularization                            | true / false         |
| optimization.flexibility.cycle_regularization.cost_eur_per_kwh_throughput | float | Cost per charged/discharged energy throughput | ≥ 0 (€/kWh) |

Depot settings (only if desired, in addition to settings that are implicitly in flexibility bands)

| Parameter                                | Type   | Description                                         | Valid values / format |
|-----------------------------------------|--------|-----------------------------------------------------|-----------------------|
| optimization.depot.eta_grid2depot       | float  | Efficiency for grid-to-depot power flow             | (0, 1]                |
| optimization.depot.eta_depot2grid       | float  | Efficiency for depot-to-grid power flow             | (0, 1]                |
| optimization.depot.grid_connection_limit| float  | Symmetric grid connection limit                     | ≥ 0 (kW)              |

Postprocessing (optional block, defaults shown apply if omitted):

| Parameter                                                          | Type   | Description                                              | Valid values / format |
|--------------------------------------------------------------------|--------|----------------------------------------------------------|-----------------------|
| postprocessing.save_commits                                        | bool   | Keep commit.csv / fcr_commit.csv after postprocessing (false deletes them to save disk space) | true / false (default true) |
| postprocessing.reference_driving_energy_costs.enabled              | bool   | Compute the uncontrolled-charging reference cost KPI     | true / false (default false) |
| postprocessing.reference_driving_energy_costs.static_price_eur_per_kwh | float | Static electricity price for the reference cost     | ≥ 0 (€/kWh)           |
| postprocessing.reference_driving_energy_costs.energy_column        | str    | Column in the flexibility CSV with the reference driving energy | column name (default `Ref_driving_energy_kWh`) |

## Input data formats

All input files must cover the configured simulation horizon (plus the MPC lookahead) at
the simulation timestep. Timestamps must be timezone-aware (e.g. `2026-02-06 00:00:00+01:00`
or UTC); config timestamps (`simulation.start` / `simulation.end`) are interpreted as
`Europe/Berlin` local market time — German market conventions (DA/ID gate closures, FCR 4 h
slots, reBAP) are the modelling baseline.

| Input | Format | Required columns / notes |
|-------|--------|--------------------------|
| DA / ID / imbalance prices | CSV | `time`, `price`. **Prices in €/kWh** (not €/MWh; e.g. 0.06364 = 63.64 €/MWh). One row per simulation timestep; duplicates and gaps raise errors. |
| Price forecasts (optional) | CSV | Same format as the realized price series; used for MPC decisions only, settlement always uses the realized series. |
| Flexibility bounds | CSV | `time`, `Power_lower_kW`, `Power_upper_kW`, `Capacity_lower_kWh`, `Capacity_upper_kWh`; optional `Ref_driving_energy_kWh` (used by the reference-cost postprocessing). |
| FCR capacity prices | XLSX | Column `GERMANY_SETTLEMENTCAPACITY_PRICE_[EUR/MW]` plus either `DATETIME_UTC` or `DATE_FROM` + `PRODUCTNAME` (the format of the German TSOs' tender-result export, regelleistung.net). One price per 4 h product slot, in €/MW. |
| Grid frequency | CSV | `DATETIME` plus the pre-aggregated droop signal `FREQ_DROOP_MEAN` (mean normalized FCR activation in [-1, 1] per timestep); optional `FREQ_DROOP_ABS_MEAN`. |

The bundled files under `data/example/` serve as format references; see
[data/example/README.md](data/example/README.md) for their provenance and terms of use.
