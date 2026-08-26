"""Scoring engine and valuation maths. No I/O, no UI — easy to test."""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import FACTOR_MAP


def percentile_rank(series: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """Cross-sectional rank, 0-100. Missing values stay missing."""
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() < 2:
        return pd.Series(np.nan, index=series.index, dtype="float64")
    ranked = values.rank(pct=True, ascending=higher_is_better)
    return ranked * 100


def winsorize(series: pd.Series, lower: float = 0.02, upper: float = 0.98) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    if values.notna().sum() < 5:
        return values
    lo, hi = values.quantile(lower), values.quantile(upper)
    return values.clip(lo, hi)


def factor_scores(snapshot: pd.DataFrame) -> pd.DataFrame:
    """One column per factor, each 0-100, plus the underlying metric ranks."""
    metric_ranks = {}
    for factor, metrics in FACTOR_MAP.items():
        for metric, higher_better in metrics.items():
            if metric not in snapshot.columns:
                continue
            cleaned = winsorize(snapshot[metric])
            # A negative EV/EBITDA or P/B is not "cheap", it is broken. Drop it.
            if metric in ("ev_ebitda", "price_to_book", "pe") :
                cleaned = cleaned.where(cleaned > 0)
            metric_ranks[f"{factor}:{metric}"] = percentile_rank(cleaned, higher_better)

    ranks = pd.DataFrame(metric_ranks, index=snapshot.index)
    scores = pd.DataFrame(index=snapshot.index)
    for factor in FACTOR_MAP:
        cols = [c for c in ranks.columns if c.startswith(f"{factor}:")]
        if cols:
            scores[factor] = ranks[cols].mean(axis=1, skipna=True)
        else:
            scores[factor] = np.nan
    return scores, ranks


def composite(scores: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Weighted blend that renormalises when a factor is missing for a name."""
    weight_series = pd.Series(weights, dtype="float64")
    weight_series = weight_series[weight_series.index.isin(scores.columns)]
    if weight_series.sum() == 0:
        return pd.Series(np.nan, index=scores.index)
    available = scores[weight_series.index].notna()
    weighted = (scores[weight_series.index].fillna(0) * weight_series).sum(axis=1)
    denominator = (available * weight_series).sum(axis=1)
    result = weighted / denominator.replace(0, np.nan)
    return result


def coverage(snapshot: pd.DataFrame) -> pd.Series:
    """Share of scoring metrics actually present per ticker — trust indicator."""
    metrics = [m for group in FACTOR_MAP.values() for m in group]
    metrics = [m for m in metrics if m in snapshot.columns]
    if not metrics:
        return pd.Series(np.nan, index=snapshot.index)
    present = snapshot[metrics].apply(pd.to_numeric, errors="coerce").notna()
    return present.mean(axis=1)


def rank_table(snapshot: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    scores, _ = factor_scores(snapshot)
    table = snapshot.join(scores)
    table["Score"] = composite(scores, weights)
    table["Coverage"] = coverage(snapshot)
    return table.sort_values("Score", ascending=False)


# --------------------------------------------------------------------------
# valuation
# --------------------------------------------------------------------------
def dcf(
    base_fcf: float,
    growth: float,
    years: int,
    terminal_growth: float,
    wacc: float,
    net_debt: float,
    shares: float,
) -> dict:
    """Two-stage FCF discount model. All rates as decimals."""
    if wacc <= terminal_growth:
        return {"error": "Discount rate must exceed terminal growth."}
    if shares <= 0:
        return {"error": "Share count must be positive."}

    flows, present_values = [], []
    for year in range(1, years + 1):
        flow = base_fcf * (1 + growth) ** year
        flows.append(flow)
        present_values.append(flow / (1 + wacc) ** year)

    terminal_value = flows[-1] * (1 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / (1 + wacc) ** years
    enterprise = sum(present_values) + pv_terminal
    equity = enterprise - net_debt
    return {
        "enterprise_value": enterprise,
        "equity_value": equity,
        "per_share": equity / shares,
        "pv_explicit": sum(present_values),
        "pv_terminal": pv_terminal,
        "terminal_share": pv_terminal / enterprise if enterprise else np.nan,
        "flows": flows,
    }


def dcf_sensitivity(
    base_fcf: float,
    growth: float,
    years: int,
    shares: float,
    net_debt: float,
    wacc_range: list[float],
    terminal_range: list[float],
) -> pd.DataFrame:
    grid = pd.DataFrame(index=[f"{w:.1%}" for w in wacc_range],
                        columns=[f"{g:.1%}" for g in terminal_range], dtype="float64")
    for w in wacc_range:
        for g in terminal_range:
            result = dcf(base_fcf, growth, years, g, w, net_debt, shares)
            grid.loc[f"{w:.1%}", f"{g:.1%}"] = result.get("per_share", np.nan)
    return grid


# --------------------------------------------------------------------------
# portfolio
# --------------------------------------------------------------------------
def portfolio_view(holdings: pd.DataFrame, snapshot: pd.DataFrame,
                   scores: pd.DataFrame | None = None) -> pd.DataFrame:
    if holdings.empty:
        return holdings
    view = holdings.copy()
    view["price"] = view["ticker"].map(
        pd.to_numeric(snapshot.get("price", pd.Series(dtype="float64")), errors="coerce")
    )
    view["name"] = view["ticker"].map(snapshot.get("name", pd.Series(dtype="object")))
    view["sector"] = view["ticker"].map(snapshot.get("sector", pd.Series(dtype="object")))
    view["value"] = view["price"] * view["shares"]
    view["cost"] = pd.to_numeric(view["buy_price"], errors="coerce") * view["shares"]
    view["pnl"] = view["value"] - view["cost"]
    view["pnl_pct"] = view["pnl"] / view["cost"].replace(0, np.nan)
    total = view["value"].sum(skipna=True)
    view["weight"] = view["value"] / total if total else np.nan
    if scores is not None and "Score" in scores.columns:
        view["score"] = view["ticker"].map(scores["Score"])
    return view


def portfolio_risk(holdings: pd.DataFrame, prices: pd.DataFrame) -> dict:
    """Weighted daily return series stats. Weights are current, not historical."""
    if holdings.empty or prices.empty:
        return {}
    owned = [t for t in holdings["ticker"] if t in prices.columns]
    if not owned:
        return {}
    weights = holdings.set_index("ticker").loc[owned, "value"]
    weights = weights / weights.sum()
    returns = prices[owned].pct_change().dropna(how="all")
    portfolio_returns = (returns * weights).sum(axis=1)
    if portfolio_returns.empty:
        return {}
    curve = (1 + portfolio_returns).cumprod()
    ann_return = curve.iloc[-1] ** (252 / len(curve)) - 1
    ann_vol = portfolio_returns.std() * np.sqrt(252)
    return {
        "annual_return": float(ann_return),
        "volatility": float(ann_vol),
        "sharpe": float(ann_return / ann_vol) if ann_vol else np.nan,
        "max_drawdown": float((curve / curve.cummax() - 1).min()),
        "curve": curve,
        "correlation": returns.corr(),
    }
