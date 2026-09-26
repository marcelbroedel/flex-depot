@echo off
REM ================================
REM Flex-Depot illustrative example: run S1 -> S4 (bidirectional) and their
REM unidirectional companions S1_uni -> S4_uni sequentially, then aggregate.
REM The *_uni runs reuse each scenario's TOML but point at the unidirectional
REM flexibility band; aggregate_results.py pairs them for the bidirectional-vs-
REM unidirectional comparison (#8).
REM Fail fast: abort on the first failing scenario (non-zero exit code).
REM Expect ~30-45 min per scenario with HiGHS (1-month window); 8 runs total.
REM All outputs land in results\illustrative_example\.
REM ================================

setlocal enabledelayedexpansion

REM Go to the repository root (data paths in the TOMLs are repo-relative)
cd /d "%~dp0..\.."

REM Pin the interpreter to THIS repo's venv. A bare `python` would use whatever
REM interpreter is active in the shell, silently producing results from the
REM wrong codebase. Force the flex-depot venv so the run always uses this repo.
set "PY=%CD%\.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo ERROR: flex-depot venv interpreter not found at "%PY%". 1>&2
    echo Create it: run  python -m venv .venv  then  .venv\Scripts\pip install -e .  -- or fix the path. 1>&2
    exit /b 1
)

set "OUT=results\illustrative_example"
if not exist "%OUT%" mkdir "%OUT%"
set "INDEX=%OUT%\run_index.csv"
echo scenario,run_dir,runtime_s> "%INDEX%"

for %%S in (s1 s2 s3 s4 s1_uni s2_uni s3_uni s4_uni) do (
    set "CONFIG=examples\illustrative_example\settings_%%S.toml"
    set "RUN_DIR=%OUT%\%%S"
    echo === Starting scenario %%S [!CONFIG!] -- expect ~30-45 min with HiGHS ===
    "%PY%" -c "import time; print(int(time.time()))" > "%TEMP%\_flexdepot_epoch.txt"
    set /p T0=<"%TEMP%\_flexdepot_epoch.txt"

    "%PY%" -m flex_dep_opt run-sim --config "!CONFIG!" --run-dir "!RUN_DIR!"
    if errorlevel 1 (
        echo ERROR: scenario %%S simulation failed -- aborting. 1>&2
        exit /b 1
    )
    "%PY%" -m flex_dep_opt run-post --config "!CONFIG!" --run-dir "!RUN_DIR!"
    if errorlevel 1 (
        echo ERROR: scenario %%S postprocessing failed -- aborting. 1>&2
        exit /b 1
    )

    "%PY%" -c "import time; print(int(time.time()))" > "%TEMP%\_flexdepot_epoch.txt"
    set /p T1=<"%TEMP%\_flexdepot_epoch.txt"
    set /a RUNTIME=!T1!-!T0!
    echo %%S,!RUN_DIR!,!RUNTIME!>> "%INDEX%"
    echo === Scenario %%S finished in !RUNTIME! s ^-^> !RUN_DIR! ===
)

del "%TEMP%\_flexdepot_epoch.txt" 2>nul

"%PY%" examples\illustrative_example\aggregate_results.py "%INDEX%"
if errorlevel 1 exit /b 1
