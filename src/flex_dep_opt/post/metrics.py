from __future__ import annotations

from collections.abc import Mapping

import pandas as pd


# =============================================================================
# Helpers
# =============================================================================
def infer_market_position_columns(dispatch: pd.DataFrame) -> list[str]:
    """
    Return all dispatch columns that represent per-market positions p_<mk>_kw.

    Excludes physical variables (p_ch/p_dis/p_net) and imbalance variables.
    """
    return [
        c
        for c in dispatch.columns
        if c.startswith("p_")
        and c.endswith("_kw")
        and c not in ("p_ch_kw", "p_dis_kw", "p_net_kw", "p_droop_kw", "p_imb_pos_kw", "p_imb_neg_kw")
    ]


def has_imbalance(dispatch: pd.DataFrame) -> bool:
    """True if dispatch contains imbalance columns p_imb_pos_kw and p_imb_neg_kw."""
    return ("p_imb_pos_kw" in dispatch.columns) and ("p_imb_neg_kw" in dispatch.columns)


# =============================================================================
# Core computations
# =============================================================================
def compute_cashflows_per_step(
    dispatch: pd.DataFrame,
    prices_by_market: Mapping[str, pd.Series],
    *,
    timestep_hours: float,
) -> pd.DataFrame:
    """
    Compute per-timestep cashflows for each market and total.

    Convention
    ----------
    cashflow[t] = - price[t] * p_market[t] * dt

    - p_market > 0 => buy/import => negative cashflow (cost)
    - p_market < 0 => sell/export => positive cashflow (revenue)

    Output columns
    --------------
    - "<MK> Cashflow [€/step]" for each market found in dispatch
    - "IMB Cashflow [€/step]" if imbalance columns exist and IMB prices exist
    - "Total Cashflow [€/step]"
    - "Cumulative Profit [€]"
    """
    if not isinstance(dispatch.index, pd.DatetimeIndex):
        raise ValueError("Dispatch index must be a DatetimeIndex")

    dt = float(timestep_hours)
    market_cols = infer_market_position_columns(dispatch)

    cf_df = pd.DataFrame(index=dispatch.index)

    # -------------------------------------------------------------------------
    # Market cashflows (DA, ID, ...)
    # -------------------------------------------------------------------------
    for col in market_cols:
        mk = col[2:-3].upper()
        if mk not in prices_by_market:
            continue

        p = dispatch[col]
        price = prices_by_market[mk]
        p, price = p.align(price, join="inner")
        if p.empty:
            continue

        cf_df[f"{mk} Cashflow [€/step]"] = (-price * p) * dt

    # -------------------------------------------------------------------------
    # Optional: imbalance cashflow (reBAP)
    # Treat as a cost component using IMB_POS and IMB_NEG prices.
    # -------------------------------------------------------------------------
    if has_imbalance(dispatch) and ("IMB_POS" in prices_by_market) and ("IMB_NEG" in prices_by_market):
        p_pos = dispatch["p_imb_pos_kw"].astype(float).copy().clip(lower=0.0)
        p_neg = dispatch["p_imb_neg_kw"].astype(float).copy().clip(lower=0.0)

        pos_price = prices_by_market["IMB_POS"]
        neg_price = prices_by_market["IMB_NEG"]

        idx = dispatch.index.intersection(pos_price.index).intersection(neg_price.index)
        if len(idx) > 0:
            p_pos = p_pos.reindex(idx).fillna(0.0)
            p_neg = p_neg.reindex(idx).fillna(0.0)
            pos_price = pos_price.reindex(idx)
            neg_price = neg_price.reindex(idx)

            cf_df["IMB Cashflow [€/step]"] = -(pos_price * p_pos + neg_price * p_neg) * dt

    # -------------------------------------------------------------------------
    # FCR activation cashflow: reBAP settlement of FCR droop energy.
    # Sign/price mapping (import-positive, consistent with PASS-2 imbalance):
    #   p_droop < 0 (upward FCR, export, BKV Überdeckung)  → earn IMB_NEG price
    #   p_droop > 0 (downward FCR, import, BKV Unterdeckung) → pay  IMB_POS price
    # cashflow = -eff_price * p_droop * dt  (matches -price * p_market * dt convention)
    # -------------------------------------------------------------------------
    if (
        "p_droop_kw" in dispatch.columns
        and "IMB_POS" in prices_by_market
        and "IMB_NEG" in prices_by_market
    ):
        p_droop = dispatch["p_droop_kw"].astype(float)
        pos_price = prices_by_market["IMB_POS"]
        neg_price = prices_by_market["IMB_NEG"]
        idx = dispatch.index.intersection(pos_price.index).intersection(neg_price.index)
        if len(idx) > 0 and p_droop.reindex(idx).abs().sum() > 0:
            p_d = p_droop.reindex(idx)
            pp = pos_price.reindex(idx)
            pn = neg_price.reindex(idx)
            # effective price: IMB_NEG where upward FCR (p_d < 0), IMB_POS elsewhere
            eff_price = pn.where(p_d < 0, pp)
            eff_price = eff_price.where(p_d != 0, 0.0)
            cf_act = (-eff_price * p_d) * dt
            cf_df["FCR Activation Cashflow [€/step]"] = cf_act.reindex(dispatch.index, fill_value=0.0)

    if cf_df.empty:
        raise ValueError("No cashflows could be computed (check market columns and prices_by_market).")

    cf_df["Total Cashflow [€/step]"] = cf_df.sum(axis=1)
    cf_df["Cumulative Profit [€]"] = cf_df["Total Cashflow [€/step]"].cumsum()

    return cf_df


def compute_market_aggregates(
    dispatch: pd.DataFrame,
    prices_by_market: Mapping[str, pd.Series],
    *,
    timestep_hours: float,
) -> tuple[
    dict[str, tuple[float, float]],
    list[tuple[str, str, float]],
    list[tuple[str, str, float]],
]:
    """
    Compute market aggregates for KPI tables and sunburst plots.

    Returns
    -------
    energy_by_mk:
        mk -> (buy_kwh, sell_kwh), both >= 0
    energy_data:
        list (mk, side, value>=0) for sunburst (Buy/Sell)
    cash_data:
        list (mk, side, value>=0) for sunburst (Buy/Sell/Cost)

    Notes
    -----
    - "Buy" corresponds to p > 0, "Sell" corresponds to p < 0.
    - IMB is aggregated from IMB_POS/IMB_NEG into a synthetic market "IMB".
    """
    dt = float(timestep_hours)
    market_cols = infer_market_position_columns(dispatch)

    energy_by_mk: dict[str, tuple[float, float]] = {}

    energy_data: list[tuple[str, str, float]] = []
    cash_data: list[tuple[str, str, float]] = []

    for col in market_cols:
        mk = col[2:-3].upper()
        if mk not in prices_by_market:
            continue

        p = dispatch[col]
        price = prices_by_market[mk]
        p, price = p.align(price, join="inner")
        if p.empty:
            continue

        energy_kwh = p * dt
        cash_eur = (-price * p) * dt

        buy_mask = p > 0
        sell_mask = p < 0

        buy_e = float(energy_kwh[buy_mask].sum()) if buy_mask.any() else 0.0
        sell_e = float((-energy_kwh[sell_mask]).sum()) if sell_mask.any() else 0.0

        buy_c = float((-cash_eur[buy_mask]).sum()) if buy_mask.any() else 0.0
        sell_c = float((cash_eur[sell_mask]).sum()) if sell_mask.any() else 0.0

        energy_by_mk[mk] = (buy_e, sell_e)

        if buy_e > 0:
            energy_data.append((mk, "Buy", buy_e))
        if sell_e > 0:
            energy_data.append((mk, "Sell", sell_e))

        if buy_c > 0:
            cash_data.append((mk, "Buy", buy_c))
        if sell_c > 0:
            cash_data.append((mk, "Sell", sell_c))

    # IMB aggregation
    if has_imbalance(dispatch) and ("IMB_POS" in prices_by_market) and ("IMB_NEG" in prices_by_market):
        p_pos = dispatch["p_imb_pos_kw"].astype(float)
        p_neg = dispatch["p_imb_neg_kw"].astype(float)

        pos_price = prices_by_market["IMB_POS"]
        neg_price = prices_by_market["IMB_NEG"]

        idx = dispatch.index.intersection(pos_price.index).intersection(neg_price.index)
        if len(idx) > 0:
            p_pos = p_pos.reindex(idx).fillna(0.0)
            p_neg = p_neg.reindex(idx).fillna(0.0)
            pos_price = pos_price.reindex(idx)
            neg_price = neg_price.reindex(idx)

            p_net = p_pos - p_neg
            energy_kwh_net = p_net * dt

            buy_mask = energy_kwh_net > 0
            sell_mask = energy_kwh_net < 0

            buy_e = float(energy_kwh_net[buy_mask].sum()) if buy_mask.any() else 0.0
            sell_e = float((-energy_kwh_net[sell_mask]).sum()) if sell_mask.any() else 0.0

            imb_cost_eur = float((pos_price * p_pos + neg_price * p_neg).sum() * dt)

            energy_by_mk["IMB"] = (buy_e, sell_e)

            if buy_e > 0:
                energy_data.append(("IMB", "Buy", buy_e))
            if sell_e > 0:
                energy_data.append(("IMB", "Sell", sell_e))
            if imb_cost_eur > 0:
                cash_data.append(("IMB", "Cost", imb_cost_eur))

    return energy_by_mk, energy_data, cash_data


def compute_fcr_cashflow_per_slot(
    index: pd.DatetimeIndex,
    fcr_commit_df: pd.DataFrame | None,
) -> pd.Series | None:
    """
    Book per-slot FCR revenue (EUR, from fcr_commit.csv) as a single value at the
    first index inside each slot window. The bar chart shows one bar per 4 h slot
    instead of a low-height bar repeated across 16 quarter-hour steps, which is
    far more readable; cumulative profit steps up at slot start and is flat in
    between. Returns None when there is no accepted FCR revenue to report.
    """
    if fcr_commit_df is None or fcr_commit_df.empty:
        return None
    if "slot_start" not in fcr_commit_df.columns or "fcr_revenue_eur" not in fcr_commit_df.columns:
        return None

    cf = pd.Series(0.0, index=index)
    any_revenue = False
    for _, row in fcr_commit_df.iterrows():
        rev = float(row["fcr_revenue_eur"])
        if rev == 0.0:
            continue
        slot_start = row["slot_start"]
        # Place the full slot revenue at the first dispatch step inside the slot.
        in_slot = index[index >= slot_start]
        if in_slot.empty:
            continue
        cf.loc[in_slot[0]] = cf.loc[in_slot[0]] + rev
        any_revenue = True

    return cf if any_revenue else None


def compute_fcr_activation_energy(
    dispatch: pd.DataFrame,
    *,
    timestep_hours: float,
) -> tuple[float, float] | None:
    """
    Total activation energy moved by FCR over the horizon, split into:
      buy_kwh  = energy taken in (downward FCR activations, depot charges)
      sell_kwh = energy delivered out (upward FCR activations, depot discharges)

    Returns None if the dispatch has no FCR column.

    Sign convention in dispatch (`p_droop_kw = -droop * x_fcr`, import-positive):
      p_droop > 0 -> depot imports (Buy, downward FCR)
      p_droop < 0 -> depot exports (Sell, upward FCR)
    """
    if "p_droop_kw" not in dispatch.columns:
        return None
    dt = float(timestep_hours)
    p = dispatch["p_droop_kw"].astype(float)
    buy_kwh = float(p[p > 0].sum()) * dt
    sell_kwh = float(-p[p < 0].sum()) * dt
    if buy_kwh == 0.0 and sell_kwh == 0.0:
        return None
    return buy_kwh, sell_kwh


def compute_kpis(
    cf_df: pd.DataFrame,
    energy_by_mk: Mapping[str, tuple[float, float]],
    fee_eur_per_kwh_by_market: Mapping[str, float],
    *,
    commit: pd.DataFrame | None = None,
    cycling_cost_eur: float = 0.0,
) -> dict[str, float | int]:
    """
    Compute KPIs for reporting.

    KPI definitions
    ---------------
    - gross_profit_eur:
        Total cashflow plus fee term (fees are negative), minus the
        battery-cycling cost. The cycling cost is a degradation proxy and a real
        variable operating cost (it scales with grid-side throughput), so it is
        kept inside gross profit — this makes the reported KPI consistent with
        the optimization objective, which maximizes cashflow net of the same
        cycling term. Gross profit thus remains gross of fixed/capital costs
        (e.g. bidirectional charging hardware), which are not modeled.
    - cycling_cost_eur:
        Battery-cycling (degradation-proxy) cost already subtracted in
        gross_profit_eur; reported separately for transparency.
    - trading_profit_eur:
        Scheduled-market (DA/ID) cashflow only — excludes fees, imbalance
        cost, and FCR revenue.
    - fees_eur:
        Applied to absolute traded energy volume in DA/ID.
    - imb_cost_eur:
        Magnitude derived from "IMB Cashflow [€/step]" if present.
    - trade_steps:
        Count of committed trades based on commit_df.
    - buy/sell/net energy:
        Aggregated traded energy excluding IMB.
    """
    buy_kwh = float(sum(v[0] for mk, v in energy_by_mk.items() if mk != "IMB"))
    sell_kwh = float(sum(v[1] for mk, v in energy_by_mk.items() if mk != "IMB"))
    net_kwh = sell_kwh - buy_kwh

    def _mk_fee(mk: str) -> float:
        if mk not in energy_by_mk:
            return 0.0
        buy_e, sell_e = energy_by_mk[mk]
        return float(fee_eur_per_kwh_by_market.get(mk, 0.0)) * float(buy_e + sell_e)

    fees_eur = -(_mk_fee("DA") + _mk_fee("ID"))

    trade_steps = 0
    if commit is not None and not commit.empty:
        if ("committed_new" in commit.columns) and ("commit_now" in commit.columns):
            trade_steps = int(
                commit[(commit["committed_new"] != 0.0) & commit["commit_now"].astype(bool)].shape[0]
            )

    imb_cost_eur = 0.0
    if "IMB Cashflow [€/step]" in cf_df.columns:
        imb_cost_eur = float(cf_df["IMB Cashflow [€/step]"].sum())

    fcr_cf_eur = 0.0
    if "FCR Cashflow [€/step]" in cf_df.columns:
        fcr_cf_eur = float(cf_df["FCR Cashflow [€/step]"].sum())

    fcr_activation_cf_eur = 0.0
    if "FCR Activation Cashflow [€/step]" in cf_df.columns:
        fcr_activation_cf_eur = float(cf_df["FCR Activation Cashflow [€/step]"].sum())

    total_cf_eur = float(cf_df["Total Cashflow [€/step]"].sum())

    # total CF (DA/ID + IMB + FCR) plus fees (negative), minus battery-cycling
    # cost (degradation proxy) so gross profit matches the optimization objective.
    gross_profit_eur = total_cf_eur + fees_eur - float(cycling_cost_eur)
    trading_profit_eur = (
        total_cf_eur - imb_cost_eur - fcr_cf_eur - fcr_activation_cf_eur
    )  # total CF less IMB, FCR capacity, and FCR activation → pure scheduled-market (DA/ID) CF

    # Per-market cashflow breakdown (scheduled markets only; IMB and FCR are
    # already reported via imb_cost_eur / fcr_revenue_eur).
    per_market_cf: dict[str, float] = {}
    for mk in energy_by_mk:
        if mk == "IMB":
            continue
        col = f"{mk} Cashflow [€/step]"
        per_market_cf[f"{mk.lower()}_cashflow_eur"] = (
            float(cf_df[col].sum()) if col in cf_df.columns else 0.0
        )

    return {
        "gross_profit_eur": gross_profit_eur,
        "cycling_cost_eur": float(cycling_cost_eur),
        "trading_profit_eur": trading_profit_eur,
        **per_market_cf,
        "fees_eur": float(fees_eur),
        "imb_cost_eur": float(imb_cost_eur),
        "fcr_activation_cf_eur": float(fcr_activation_cf_eur),
        "trade_steps": int(trade_steps),
        "net_kwh": float(net_kwh),
        "sell_kwh": float(sell_kwh),
        "buy_kwh": float(buy_kwh),
    }
