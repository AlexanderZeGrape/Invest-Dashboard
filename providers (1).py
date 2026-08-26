"""Data providers. Each adapter returns plain pandas objects with identical
column names, so the rest of the app never knows which source it got.

Prices     : Stooq (keyless) -> yfinance fallback
Fundamentals: SEC EDGAR (US, keyless) | FMP (global, key) | yfinance fallback
News        : Finnhub (key)

SEC requires a real contact address in the User-Agent or it returns 403.
Edit CONTACT below before using the SEC source.
"""

from __future__ import annotations

import io
import time

import numpy as np
import pandas as pd
import requests

CONTACT = "signal-desk-student-project (your.email@example.com)"
HEADERS = {"User-Agent": CONTACT, "Accept-Encoding": "gzip, deflate"}
TIMEOUT = 20


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _num(value):
    try:
        if value is None:
            return np.nan
        out = float(value)
        return np.nan if np.isinf(out) else out
    except (TypeError, ValueError):
        return np.nan


def _div(a, b):
    a, b = _num(a), _num(b)
    if np.isnan(a) or np.isnan(b) or b == 0:
        return np.nan
    return a / b


EMPTY_ROW = {
    "name": None, "sector": "Unknown", "currency": "", "market_cap": np.nan,
    "pe": np.nan, "earnings_yield": np.nan, "fcf_yield": np.nan,
    "ev_ebitda": np.nan, "price_to_book": np.nan, "roe": np.nan,
    "gross_margin": np.nan, "operating_margin": np.nan, "profit_margin": np.nan,
    "revenue_growth": np.nan, "earnings_growth": np.nan,
    "net_debt_ebitda": np.nan, "debt_to_equity": np.nan, "current_ratio": np.nan,
    "dividend_yield": np.nan, "beta": np.nan,
}


# --------------------------------------------------------------------------
# Stooq prices — free, no key, works from cloud servers
# --------------------------------------------------------------------------
STOOQ_SUFFIX = {
    ".DE": ".de", ".F": ".de", ".L": ".uk", ".PA": ".fr", ".AS": ".nl",
    ".SW": ".ch", ".MI": ".it", ".MC": ".es", ".CO": ".dk", ".ST": ".se",
    ".HE": ".fi", ".OL": ".no", ".VI": ".at", ".BR": ".be", ".LS": ".pt",
    ".TO": ".ca", ".T": ".jp", ".HK": ".hk", ".KS": ".kr",
}


def to_stooq_symbol(ticker: str) -> str:
    """AAPL -> aapl.us, SAP.DE -> sap.de, ^DAX -> ^dax."""
    sym = ticker.strip()
    if sym.startswith("^"):
        return sym.lower()
    for suffix, replacement in STOOQ_SUFFIX.items():
        if sym.upper().endswith(suffix):
            return sym[: -len(suffix)].lower().replace("-", ".") + replacement
    return sym.lower().replace("-", ".") + ".us"


def stooq_prices(tickers: list[str], pause: float = 0.15,
                 progress=None) -> pd.DataFrame:
    """Daily closes, wide frame indexed by date. Missing tickers are skipped."""
    series = {}
    for i, ticker in enumerate(tickers):
        if progress:
            progress(i / max(len(tickers), 1), f"prices · {ticker}")
        url = f"https://stooq.com/q/d/l/?s={to_stooq_symbol(ticker)}&i=d"
        try:
            response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            text = response.text.strip()
            if not text or text.lower().startswith("<") or "no data" in text.lower():
                continue
            frame = pd.read_csv(io.StringIO(text))
            if "Close" not in frame.columns or frame.empty:
                continue
            frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
            closes = frame.dropna(subset=["Date"]).set_index("Date")["Close"]
            series[ticker] = pd.to_numeric(closes, errors="coerce")
        except Exception:
            continue
        time.sleep(pause)
    if not series:
        return pd.DataFrame()
    wide = pd.DataFrame(series).sort_index()
    return wide.tail(760)  # ~3 years of trading days


def yahoo_prices(tickers: list[str], period: str = "3y", **_) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(tickers, period=period, auto_adjust=True,
                      progress=False, threads=True)
    if raw is None or len(raw) == 0:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        close = (raw["Close"] if "Close" in raw.columns.get_level_values(0)
                 else raw.xs("Close", axis=1, level=-1))
    else:
        close = raw[["Close"]]
        close.columns = tickers[:1]
    return close.dropna(how="all")


# --------------------------------------------------------------------------
# SEC EDGAR fundamentals — US only, free, official, no key
# --------------------------------------------------------------------------
_CIK_CACHE: dict[str, str] = {}


def sec_cik_map() -> dict[str, str]:
    global _CIK_CACHE
    if _CIK_CACHE:
        return _CIK_CACHE
    try:
        response = requests.get("https://www.sec.gov/files/company_tickers.json",
                                headers=HEADERS, timeout=TIMEOUT)
        payload = response.json()
        _CIK_CACHE = {
            str(entry["ticker"]).upper(): f"{int(entry['cik_str']):010d}"
            for entry in payload.values()
        }
    except Exception:
        _CIK_CACHE = {}
    return _CIK_CACHE


def _annual_values(facts: dict, tags: list[str], namespace: str = "us-gaap",
                   unit: str = "USD") -> tuple[float, float]:
    """Latest and prior fiscal-year value for the first tag that exists."""
    blocks = facts.get("facts", {}).get(namespace, {})
    for tag in tags:
        entry = blocks.get(tag)
        if not entry:
            continue
        for unit_key in (unit, "USD/shares", "shares", "pure"):
            rows = entry.get("units", {}).get(unit_key)
            if not rows:
                continue
            annual = [r for r in rows if r.get("form", "").startswith("10-K")
                      and r.get("fp") == "FY" and r.get("val") is not None]
            if len(annual) < 1:
                continue
            annual.sort(key=lambda r: r.get("end", ""))
            deduped = {}
            for row in annual:
                deduped[row.get("end", "")[:4]] = _num(row["val"])
            years = sorted(deduped)
            latest = deduped[years[-1]]
            prior = deduped[years[-2]] if len(years) > 1 else np.nan
            return latest, prior
    return np.nan, np.nan


def sec_fundamentals(tickers: list[str], prices: dict[str, float] | None = None,
                     progress=None, pause: float = 0.12) -> pd.DataFrame:
    prices = prices or {}
    cik_map = sec_cik_map()
    rows = {}
    for i, ticker in enumerate(tickers):
        if progress:
            progress(i / max(len(tickers), 1), f"SEC · {ticker}")
        record = dict(EMPTY_ROW, name=ticker, currency="USD")
        cik = cik_map.get(ticker.upper().replace("-", "."))
        if not cik:
            cik = cik_map.get(ticker.upper())
        if not cik:
            rows[ticker] = record
            continue
        try:
            response = requests.get(
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                headers=HEADERS, timeout=40,
            )
            facts = response.json()
        except Exception:
            rows[ticker] = record
            continue

        record["name"] = facts.get("entityName", ticker)

        revenue, revenue_prior = _annual_values(
            facts, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "SalesRevenueNet"])
        net_income, net_income_prior = _annual_values(
            facts, ["NetIncomeLoss", "ProfitLoss"])
        operating_income, _ = _annual_values(
            facts, ["OperatingIncomeLoss"])
        gross_profit, _ = _annual_values(facts, ["GrossProfit"])
        equity, _ = _annual_values(
            facts, ["StockholdersEquity",
                    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"])
        assets, _ = _annual_values(facts, ["Assets"])
        liabilities, _ = _annual_values(facts, ["Liabilities"])
        cash, _ = _annual_values(
            facts, ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalents"])
        long_debt, _ = _annual_values(
            facts, ["LongTermDebtNoncurrent", "LongTermDebt"])
        short_debt, _ = _annual_values(facts, ["LongTermDebtCurrent"])
        depreciation, _ = _annual_values(
            facts, ["DepreciationDepletionAndAmortization",
                    "DepreciationAmortizationAndAccretionNet", "Depreciation"])
        operating_cash, _ = _annual_values(
            facts, ["NetCashProvidedByUsedInOperatingActivities"])
        capex, _ = _annual_values(
            facts, ["PaymentsToAcquirePropertyPlantAndEquipment"])
        current_assets, _ = _annual_values(facts, ["AssetsCurrent"])
        current_liabilities, _ = _annual_values(facts, ["LiabilitiesCurrent"])
        shares, _ = _annual_values(
            facts, ["EntityCommonStockSharesOutstanding"], namespace="dei",
            unit="shares")
        if np.isnan(shares):
            shares, _ = _annual_values(
                facts, ["CommonStockSharesOutstanding", "CommonStockSharesIssued"],
                unit="shares")

        price = _num(prices.get(ticker))
        market_cap = price * shares if not (np.isnan(price) or np.isnan(shares)) else np.nan
        debt = np.nansum([long_debt, short_debt]) if not (
            np.isnan(long_debt) and np.isnan(short_debt)) else np.nan
        net_debt = debt - cash if not (np.isnan(debt) or np.isnan(cash)) else np.nan
        free_cash = operating_cash - capex if not (
            np.isnan(operating_cash) or np.isnan(capex)) else np.nan
        ebitda = (operating_income + depreciation) if not (
            np.isnan(operating_income) or np.isnan(depreciation)) else operating_income
        enterprise = market_cap + net_debt if not (
            np.isnan(market_cap) or np.isnan(net_debt)) else np.nan

        record.update({
            "market_cap": market_cap / 1e9 if not np.isnan(market_cap) else np.nan,
            "pe": _div(market_cap, net_income),
            "earnings_yield": _div(net_income, market_cap),
            "fcf_yield": _div(free_cash, market_cap),
            "ev_ebitda": _div(enterprise, ebitda),
            "price_to_book": _div(market_cap, equity),
            "roe": _div(net_income, equity),
            "gross_margin": _div(gross_profit, revenue),
            "operating_margin": _div(operating_income, revenue),
            "profit_margin": _div(net_income, revenue),
            "revenue_growth": _div(revenue, revenue_prior) - 1
            if not np.isnan(revenue_prior) else np.nan,
            "earnings_growth": _div(net_income, net_income_prior) - 1
            if not np.isnan(net_income_prior) else np.nan,
            "net_debt_ebitda": _div(net_debt, ebitda),
            "debt_to_equity": _div(debt, equity),
            "current_ratio": _div(current_assets, current_liabilities),
        })
        if np.isnan(_num(record["pe"])) is False and record["pe"] < 0:
            record["pe"] = np.nan
        rows[ticker] = record
        time.sleep(pause)
    if progress:
        progress(1.0, "done")
    return pd.DataFrame(rows).T


# --------------------------------------------------------------------------
# FMP fundamentals — global coverage, needs a key
# --------------------------------------------------------------------------
FMP_BASE = "https://financialmodelingprep.com/api/v3"


def fmp_fundamentals(tickers: list[str], api_key: str, progress=None,
                     pause: float = 0.2) -> pd.DataFrame:
    rows = {}
    for i, ticker in enumerate(tickers):
        if progress:
            progress(i / max(len(tickers), 1), f"FMP · {ticker}")
        record = dict(EMPTY_ROW, name=ticker)
        try:
            profile = requests.get(f"{FMP_BASE}/profile/{ticker}",
                                   params={"apikey": api_key},
                                   timeout=TIMEOUT).json()
            ratios = requests.get(f"{FMP_BASE}/ratios-ttm/{ticker}",
                                  params={"apikey": api_key},
                                  timeout=TIMEOUT).json()
            metrics = requests.get(f"{FMP_BASE}/key-metrics-ttm/{ticker}",
                                   params={"apikey": api_key},
                                   timeout=TIMEOUT).json()
        except Exception:
            rows[ticker] = record
            continue

        profile = profile[0] if isinstance(profile, list) and profile else {}
        ratios = ratios[0] if isinstance(ratios, list) and ratios else {}
        metrics = metrics[0] if isinstance(metrics, list) and metrics else {}

        market_cap = _num(profile.get("mktCap"))
        pe = _num(ratios.get("peRatioTTM"))
        record.update({
            "name": profile.get("companyName") or ticker,
            "sector": profile.get("sector") or "Unknown",
            "currency": profile.get("currency") or "",
            "market_cap": market_cap / 1e9 if not np.isnan(market_cap) else np.nan,
            "pe": pe if pe and pe > 0 else np.nan,
            "earnings_yield": _div(1, pe) if pe and pe > 0 else np.nan,
            "fcf_yield": _num(metrics.get("freeCashFlowYieldTTM")),
            "ev_ebitda": _num(metrics.get("enterpriseValueOverEBITDATTM"))
            or _num(ratios.get("enterpriseValueMultipleTTM")),
            "price_to_book": _num(ratios.get("priceToBookRatioTTM")),
            "roe": _num(ratios.get("returnOnEquityTTM")),
            "gross_margin": _num(ratios.get("grossProfitMarginTTM")),
            "operating_margin": _num(ratios.get("operatingProfitMarginTTM")),
            "profit_margin": _num(ratios.get("netProfitMarginTTM")),
            "net_debt_ebitda": _num(metrics.get("netDebtToEBITDATTM")),
            "debt_to_equity": _num(ratios.get("debtEquityRatioTTM")),
            "current_ratio": _num(ratios.get("currentRatioTTM")),
            "dividend_yield": _num(ratios.get("dividendYieldTTM")),
            "beta": _num(profile.get("beta")),
        })
        rows[ticker] = record
        time.sleep(pause)
    if progress:
        progress(1.0, "done")
    return pd.DataFrame(rows).T


# --------------------------------------------------------------------------
# yfinance fundamentals — fallback
# --------------------------------------------------------------------------
def yahoo_fundamentals(tickers: list[str], progress=None, **_) -> pd.DataFrame:
    import yfinance as yf

    rows = {}
    for i, ticker in enumerate(tickers):
        if progress:
            progress(i / max(len(tickers), 1), f"Yahoo · {ticker}")
        record = dict(EMPTY_ROW, name=ticker)
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:
            info = {}
        market_cap = _num(info.get("marketCap"))
        ebitda = _num(info.get("ebitda"))
        debt = _num(info.get("totalDebt"))
        cash = _num(info.get("totalCash"))
        pe = _num(info.get("trailingPE"))
        debt_equity = _num(info.get("debtToEquity"))
        record.update({
            "name": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector") or "Unknown",
            "currency": info.get("currency") or "",
            "market_cap": market_cap / 1e9 if not np.isnan(market_cap) else np.nan,
            "pe": pe, "earnings_yield": _div(1, pe),
            "fcf_yield": _div(_num(info.get("freeCashflow")), market_cap),
            "ev_ebitda": _div(_num(info.get("enterpriseValue")), ebitda),
            "price_to_book": _num(info.get("priceToBook")),
            "roe": _num(info.get("returnOnEquity")),
            "gross_margin": _num(info.get("grossMargins")),
            "operating_margin": _num(info.get("operatingMargins")),
            "profit_margin": _num(info.get("profitMargins")),
            "revenue_growth": _num(info.get("revenueGrowth")),
            "earnings_growth": _num(info.get("earningsGrowth")),
            "net_debt_ebitda": _div(debt - cash, ebitda)
            if not (np.isnan(debt) or np.isnan(cash)) else np.nan,
            "debt_to_equity": debt_equity / 100 if not np.isnan(debt_equity) else np.nan,
            "current_ratio": _num(info.get("currentRatio")),
            "dividend_yield": _num(info.get("dividendYield")),
            "beta": _num(info.get("beta")),
        })
        rows[ticker] = record
    if progress:
        progress(1.0, "done")
    return pd.DataFrame(rows).T


# --------------------------------------------------------------------------
# Finnhub news
# --------------------------------------------------------------------------
def finnhub_news(ticker: str, api_key: str, days: int = 14,
                 limit: int = 8) -> list[dict]:
    if not api_key:
        return []
    end = pd.Timestamp.today().date()
    start = end - pd.Timedelta(days=days)
    try:
        response = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": ticker, "from": str(start), "to": str(end),
                    "token": api_key},
            timeout=TIMEOUT,
        )
        items = response.json()
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    out = []
    for item in items[:limit]:
        out.append({
            "headline": item.get("headline", ""),
            "source": item.get("source", ""),
            "url": item.get("url", ""),
            "datetime": pd.to_datetime(item.get("datetime", 0), unit="s",
                                       errors="coerce"),
        })
    return out


def market_news(api_key: str, limit: int = 12) -> list[dict]:
    if not api_key:
        return []
    try:
        items = requests.get("https://finnhub.io/api/v1/news",
                             params={"category": "general", "token": api_key},
                             timeout=TIMEOUT).json()
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    return [{
        "headline": i.get("headline", ""), "source": i.get("source", ""),
        "url": i.get("url", ""),
        "datetime": pd.to_datetime(i.get("datetime", 0), unit="s", errors="coerce"),
    } for i in items[:limit]]


# --------------------------------------------------------------------------
# dispatcher
# --------------------------------------------------------------------------
def fetch_prices(tickers: list[str], source: str = "Stooq", progress=None) -> pd.DataFrame:
    if source == "Yahoo":
        return yahoo_prices(tickers)
    frame = stooq_prices(tickers, progress=progress)
    missing = [t for t in tickers if t not in frame.columns]
    if missing:
        try:
            backup = yahoo_prices(missing)
            if not backup.empty:
                frame = frame.join(backup, how="outer") if not frame.empty else backup
        except Exception:
            pass
    return frame


def fetch_fundamentals(tickers: list[str], source: str, prices: dict,
                       fmp_key: str = "", progress=None) -> pd.DataFrame:
    if source == "SEC EDGAR (US only)":
        return sec_fundamentals(tickers, prices=prices, progress=progress)
    if source == "FMP (global)":
        if not fmp_key:
            return pd.DataFrame()
        return fmp_fundamentals(tickers, fmp_key, progress=progress)
    return yahoo_fundamentals(tickers, progress=progress)
