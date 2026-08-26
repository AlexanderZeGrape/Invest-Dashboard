"""Data layer: fetch from yfinance, cache in SQLite, or generate offline demo data.

Nothing here knows about the UI. Everything returns plain pandas objects.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import numpy as np
import pandas as pd

from config import CACHE_HOURS, DB_PATH


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------
def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, check_same_thread=False)
    con.execute(
        """CREATE TABLE IF NOT EXISTS holdings (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ticker TEXT NOT NULL,
               shares REAL NOT NULL,
               buy_price REAL,
               buy_date TEXT)"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS journal (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               ticker TEXT NOT NULL,
               entry_date TEXT,
               conviction INTEGER,
               target_price REAL,
               review_date TEXT,
               thesis TEXT,
               risks TEXT,
               outcome TEXT)"""
    )
    con.commit()
    return con


def snapshot_age_hours(con: sqlite3.Connection, universe: str) -> float | None:
    """Hours since this universe was last fetched, or None if never."""
    try:
        row = con.execute(
            "SELECT MAX(fetched_at) FROM snapshot WHERE universe = ?", (universe,)
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or not row[0]:
        return None
    fetched = dt.datetime.fromisoformat(row[0])
    return (dt.datetime.now() - fetched).total_seconds() / 3600


def load_snapshot(con: sqlite3.Connection, universe: str) -> pd.DataFrame:
    try:
        df = pd.read_sql(
            "SELECT * FROM snapshot WHERE universe = ?", con, params=(universe,)
        )
    except Exception:
        return pd.DataFrame()
    if df.empty:
        return df
    return df.set_index("ticker")


def save_snapshot(con: sqlite3.Connection, universe: str, df: pd.DataFrame) -> None:
    out = df.copy()
    out["universe"] = universe
    out["fetched_at"] = dt.datetime.now().isoformat(timespec="seconds")
    out = out.reset_index().rename(columns={"index": "ticker"})
    try:
        con.execute("DELETE FROM snapshot WHERE universe = ?", (universe,))
        con.commit()
    except sqlite3.OperationalError:
        pass  # table does not exist yet
    out.to_sql("snapshot", con, if_exists="append", index=False)
    con.commit()


def save_prices(con: sqlite3.Connection, universe: str, prices: pd.DataFrame) -> None:
    """prices: wide DataFrame, index = date, columns = tickers."""
    if prices is None or prices.empty:
        return
    long = prices.reset_index()
    long = long.rename(columns={long.columns[0]: "date"})
    long = long.melt(id_vars="date", var_name="ticker", value_name="close")
    long = long.dropna(subset=["close"])
    long["date"] = long["date"].astype(str)
    long["universe"] = universe
    try:
        con.execute("DELETE FROM prices WHERE universe = ?", (universe,))
        con.commit()
    except sqlite3.OperationalError:
        pass  # table does not exist yet
    long.to_sql("prices", con, if_exists="append", index=False)
    con.commit()


def load_prices(con: sqlite3.Connection, universe: str) -> pd.DataFrame:
    try:
        long = pd.read_sql(
            "SELECT date, ticker, close FROM prices WHERE universe = ?",
            con, params=(universe,),
        )
    except Exception:
        return pd.DataFrame()
    if long.empty:
        return pd.DataFrame()
    wide = long.pivot(index="date", columns="ticker", values="close")
    wide.index = pd.to_datetime(wide.index)
    return wide.sort_index()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _num(value):
    """Coerce anything yfinance hands back into a float or NaN."""
    try:
        if value is None:
            return np.nan
        out = float(value)
        if np.isinf(out):
            return np.nan
        return out
    except (TypeError, ValueError):
        return np.nan


def _safe_div(a, b):
    a, b = _num(a), _num(b)
    if np.isnan(a) or np.isnan(b) or b == 0:
        return np.nan
    return a / b


def pick_row(frame: pd.DataFrame, candidates: list[str]) -> pd.Series | None:
    """Find a statement row by fuzzy label match. yfinance renames these often."""
    if frame is None or frame.empty:
        return None
    labels = {str(i).lower().replace(" ", ""): i for i in frame.index}
    for cand in candidates:
        key = cand.lower().replace(" ", "")
        for norm, original in labels.items():
            if norm == key:
                return frame.loc[original]
    for cand in candidates:
        key = cand.lower().replace(" ", "")
        for norm, original in labels.items():
            if key in norm:
                return frame.loc[original]
    return None


# --------------------------------------------------------------------------
# live fetch
# --------------------------------------------------------------------------
def fetch_prices(tickers: list[str], source: str = "Stooq", progress=None) -> pd.DataFrame:
    import providers

    return providers.fetch_prices(tickers, source=source, progress=progress)


def price_features(prices: pd.DataFrame) -> pd.DataFrame:
    """Momentum and risk stats from a wide price frame."""
    rows = {}
    for ticker in prices.columns:
        series = prices[ticker].dropna()
        if len(series) < 30:
            rows[ticker] = {}
            continue
        last = series.iloc[-1]
        rec = {"price": last}
        # 12-1 momentum: skip the most recent month (short-term reversal).
        if len(series) > 252:
            rec["mom_12_1"] = _safe_div(series.iloc[-21], series.iloc[-252]) - 1
        if len(series) > 126:
            rec["mom_6_1"] = _safe_div(series.iloc[-21], series.iloc[-126]) - 1
        if len(series) >= 200:
            rec["above_200d"] = _safe_div(last, series.iloc[-200:].mean()) - 1
        returns = series.pct_change().dropna()
        if len(returns) > 60:
            rec["volatility"] = float(returns.std() * np.sqrt(252))
        running_max = series.cummax()
        rec["max_drawdown"] = float((series / running_max - 1).min())
        rows[ticker] = rec
    return pd.DataFrame(rows).T


def fetch_fundamentals(tickers: list[str], source: str = "Yahoo",
                       prices: dict | None = None, fmp_key: str = "",
                       progress=None) -> pd.DataFrame:
    import providers

    return providers.fetch_fundamentals(
        tickers, source=source, prices=prices or {}, fmp_key=fmp_key,
        progress=progress,
    )


def build_snapshot(tickers: list[str], price_source: str = "Stooq",
                   fundamental_source: str = "Yahoo", fmp_key: str = "",
                   asset_class: str = "equity", progress=None):
    """Returns (snapshot, prices). Macro universes skip fundamentals entirely."""
    prices = fetch_prices(tickers, source=price_source, progress=progress)
    features = price_features(prices) if not prices.empty else pd.DataFrame()

    if asset_class == "macro":
        snapshot = features.copy()
        if snapshot.empty:
            return snapshot, prices
        snapshot["name"] = snapshot.index
        snapshot["sector"] = "Macro"
        snapshot["currency"] = ""
        return snapshot, prices

    last_prices = {}
    if not prices.empty:
        for ticker in prices.columns:
            series = prices[ticker].dropna()
            if len(series):
                last_prices[ticker] = float(series.iloc[-1])

    fundamentals = fetch_fundamentals(
        tickers, source=fundamental_source, prices=last_prices,
        fmp_key=fmp_key, progress=progress,
    )
    if fundamentals.empty:
        fundamentals = pd.DataFrame(index=tickers)
    if not features.empty:
        fundamentals = fundamentals.join(features, how="outer")
    return fundamentals, prices


# --------------------------------------------------------------------------
# deep dive: annual statements + Piotroski
# --------------------------------------------------------------------------
def fetch_statements(ticker: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    t = yf.Ticker(ticker)
    out = {}
    for key, attrs in {
        "income": ("income_stmt", "financials"),
        "balance": ("balance_sheet", "balancesheet"),
        "cashflow": ("cashflow", "cash_flow"),
    }.items():
        frame = pd.DataFrame()
        for attr in attrs:
            try:
                candidate = getattr(t, attr)
                if candidate is not None and not candidate.empty:
                    frame = candidate
                    break
            except Exception:
                continue
        out[key] = frame
    return out


def piotroski(statements: dict[str, pd.DataFrame]) -> tuple[int, int, list[tuple[str, bool]]]:
    """Simplified F-Score. Returns (score, criteria_tested, detail)."""
    inc, bal, cfs = statements["income"], statements["balance"], statements["cashflow"]
    net_income = pick_row(inc, ["Net Income", "Net Income Common Stockholders"])
    revenue = pick_row(inc, ["Total Revenue", "Operating Revenue"])
    gross = pick_row(inc, ["Gross Profit"])
    assets = pick_row(bal, ["Total Assets"])
    lt_debt = pick_row(bal, ["Long Term Debt", "Long Term Debt And Capital Lease"])
    cur_assets = pick_row(bal, ["Current Assets", "Total Current Assets"])
    cur_liab = pick_row(bal, ["Current Liabilities", "Total Current Liabilities"])
    shares = pick_row(bal, ["Ordinary Shares Number", "Share Issued"])
    ocf = pick_row(cfs, ["Operating Cash Flow", "Total Cash From Operating Activities"])

    def yoy(series, idx=0):
        """yfinance columns run newest-first."""
        if series is None or len(series) < idx + 2:
            return None, None
        try:
            return _num(series.iloc[idx]), _num(series.iloc[idx + 1])
        except Exception:
            return None, None

    checks: list[tuple[str, bool]] = []

    def add(label, condition):
        if condition is None:
            return
        checks.append((label, bool(condition)))

    ni_now, ni_prev = yoy(net_income)
    a_now, a_prev = yoy(assets)
    ocf_now, ocf_prev = yoy(ocf)

    if ni_now is not None:
        add("Positive net income", ni_now > 0)
    if ocf_now is not None:
        add("Positive operating cash flow", ocf_now > 0)
    if ni_now is not None and ocf_now is not None:
        add("Cash flow exceeds net income", ocf_now > ni_now)
    if None not in (ni_now, ni_prev, a_now, a_prev) and a_now and a_prev:
        add("Return on assets improving", (ni_now / a_now) > (ni_prev / a_prev))
    ld_now, ld_prev = yoy(lt_debt)
    if None not in (ld_now, ld_prev, a_now, a_prev) and a_now and a_prev:
        add("Leverage falling", (ld_now / a_now) < (ld_prev / a_prev))
    ca_now, ca_prev = yoy(cur_assets)
    cl_now, cl_prev = yoy(cur_liab)
    if None not in (ca_now, ca_prev, cl_now, cl_prev) and cl_now and cl_prev:
        add("Current ratio improving", (ca_now / cl_now) > (ca_prev / cl_prev))
    sh_now, sh_prev = yoy(shares)
    if None not in (sh_now, sh_prev):
        add("No share dilution", sh_now <= sh_prev * 1.01)
    g_now, g_prev = yoy(gross)
    r_now, r_prev = yoy(revenue)
    if None not in (g_now, g_prev, r_now, r_prev) and r_now and r_prev:
        add("Gross margin improving", (g_now / r_now) > (g_prev / r_prev))
    if None not in (r_now, r_prev, a_now, a_prev) and a_now and a_prev:
        add("Asset turnover improving", (r_now / a_now) > (r_prev / a_prev))

    score = sum(1 for _, ok in checks if ok)
    return score, len(checks), checks


def statement_history(statements: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Revenue / net income / operating cash flow / capex by fiscal year."""
    inc, cfs = statements["income"], statements["cashflow"]
    series = {
        "Revenue": pick_row(inc, ["Total Revenue", "Operating Revenue"]),
        "Operating income": pick_row(inc, ["Operating Income", "EBIT"]),
        "Net income": pick_row(inc, ["Net Income", "Net Income Common Stockholders"]),
        "Operating cash flow": pick_row(
            cfs, ["Operating Cash Flow", "Total Cash From Operating Activities"]
        ),
        "Capex": pick_row(cfs, ["Capital Expenditure", "Capital Expenditures"]),
    }
    frame = pd.DataFrame({k: v for k, v in series.items() if v is not None})
    if frame.empty:
        return frame
    frame.index = [str(i)[:10] for i in frame.index]
    frame = frame.sort_index()
    if {"Operating cash flow", "Capex"}.issubset(frame.columns):
        frame["Free cash flow"] = frame["Operating cash flow"] + frame["Capex"]
    return frame / 1e6  # millions


# --------------------------------------------------------------------------
# offline demo data
# --------------------------------------------------------------------------
def demo_snapshot(n: int = 40, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthetic but plausible data so the app runs with no internet."""
    rng = np.random.default_rng(seed)
    sectors = [
        "Technology", "Industrials", "Healthcare", "Financials",
        "Consumer", "Energy", "Utilities", "Materials",
    ]
    tickers = [f"DEMO{i:02d}" for i in range(1, n + 1)]

    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=520)
    prices = {}
    for ticker in tickers:
        drift = rng.normal(0.0004, 0.0004)
        vol = rng.uniform(0.010, 0.028)
        steps = rng.normal(drift, vol, len(dates))
        prices[ticker] = 40 * np.exp(np.cumsum(steps))
    price_frame = pd.DataFrame(prices, index=dates)

    rows = {}
    for ticker in tickers:
        pe = float(rng.uniform(7, 45))
        rows[ticker] = {
            "name": f"Demo Industries {ticker[-2:]}",
            "sector": sectors[rng.integers(0, len(sectors))],
            "currency": "EUR",
            "market_cap": float(rng.uniform(2, 400)),
            "pe": pe,
            "earnings_yield": 1 / pe,
            "fcf_yield": float(rng.uniform(-0.01, 0.10)),
            "ev_ebitda": float(rng.uniform(4, 30)),
            "price_to_book": float(rng.uniform(0.6, 12)),
            "roe": float(rng.uniform(-0.05, 0.40)),
            "gross_margin": float(rng.uniform(0.15, 0.75)),
            "operating_margin": float(rng.uniform(0.02, 0.35)),
            "profit_margin": float(rng.uniform(-0.02, 0.28)),
            "revenue_growth": float(rng.uniform(-0.10, 0.35)),
            "earnings_growth": float(rng.uniform(-0.30, 0.60)),
            "net_debt_ebitda": float(rng.uniform(-1.5, 5.0)),
            "debt_to_equity": float(rng.uniform(0.0, 2.5)),
            "current_ratio": float(rng.uniform(0.6, 3.5)),
            "dividend_yield": float(rng.uniform(0, 0.06)),
            "beta": float(rng.uniform(0.4, 1.9)),
        }
    fundamentals = pd.DataFrame(rows).T
    fundamentals = fundamentals.join(price_features(price_frame), how="left")
    return fundamentals, price_frame
