from __future__ import annotations

import numpy as np
import pandas as pd


def draw_ar1_shape(index: pd.DatetimeIndex, rho: float, seed: int) -> pd.Series:
    """
    Draw a stationary AR(1) error shape z over `index` with unit marginal variance.

    z[0] ~ N(0, 1);  z[t] = rho * z[t-1] + sqrt(1 - rho^2) * eps[t],  eps ~ N(0, 1)

    The sqrt(1 - rho^2) innovation scaling keeps Var(z[t]) = 1 for every t, so a
    decision-price series settlement + sigma * z has forecast RMSE exactly sigma.
    rho is the lag-1 autocorrelation per index step (e.g. per 15 min).

    Uses a dedicated np.random.Generator: the global `random` module state
    (seeded by the FCR acceptance draw in the MPC workflow) is never touched,
    which keeps runs with this feature disabled bit-identical.
    """
    if not (0.0 <= rho < 1.0):
        raise ValueError(f"rho must be in [0, 1), got {rho}")
    if len(index) == 0:
        return pd.Series(dtype=float, index=index, name="z")

    rng = np.random.default_rng(seed)
    eps = rng.standard_normal(len(index))

    z = np.empty(len(index))
    z[0] = eps[0]
    c = np.sqrt(1.0 - rho * rho)
    for i in range(1, len(index)):
        z[i] = rho * z[i - 1] + c * eps[i]

    return pd.Series(z, index=index, name="z")


def perturb_prices(settlement: pd.Series, z: pd.Series, sigma_eur_per_mwh: float) -> pd.Series:
    """
    Build the decision-price series the optimizer sees:

        decision[t] = settlement[t] + sigma * z[t]

    `settlement` is in EUR/kWh (repo price CSV convention) while sigma is
    configured in EUR/MWh for comparability with published forecast RMSEs,
    hence the /1000. `z` must cover the settlement index.

    With sigma = 0 the result is numerically identical to `settlement`
    (regression case).
    """
    if sigma_eur_per_mwh < 0:
        raise ValueError(f"sigma_eur_per_mwh must be >= 0, got {sigma_eur_per_mwh}")

    missing = settlement.index.difference(z.index)
    if len(missing) > 0:
        raise ValueError(
            f"error shape z does not cover {len(missing)} settlement timestamps "
            f"(first: {missing[0]})"
        )

    decision = settlement + (sigma_eur_per_mwh / 1000.0) * z.reindex(settlement.index)
    decision.name = settlement.name
    return decision
