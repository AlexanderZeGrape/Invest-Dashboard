"""Universes, factor definitions and defaults. Edit this file first.

Each universe has a class:
  "equity" — gets the full factor score
  "macro"  — commodities, indices, ETFs. No fundamentals, so price behaviour only.
"""

DB_PATH = "signaldesk.db"
CACHE_HOURS = 12

UNIVERSES = {
    "AI & compute": {"class": "equity", "tickers": [
        "NVDA", "AVGO", "AMD", "MU", "TSM", "ASML.AS", "MSFT", "GOOGL", "META",
        "AMZN", "ORCL", "SMCI", "DELL", "ANET", "MRVL", "ARM", "PLTR", "SNOW",
        "CRWD", "NOW", "VRT", "CRM",
    ]},
    "Memory & semis": {"class": "equity", "tickers": [
        "MU", "WDC", "STX", "INTC", "TXN", "ADI", "NXPI", "ON", "SWKS",
        "QCOM", "LRCX", "AMAT", "KLAC", "TER", "ASML.AS", "IFX.DE", "STM.MI",
        "005930.KS", "000660.KS",
    ]},
    "Precious metals": {"class": "equity", "tickers": [
        "NEM", "GOLD", "AEM", "WPM", "FNV", "KGC", "PAAS", "HL", "AU", "GFI",
        "HMY", "SSRM", "EGO", "IAG",
    ]},
    "Energy": {"class": "equity", "tickers": [
        "XOM", "CVX", "COP", "EOG", "SLB", "HAL", "OXY", "PSX", "VLO", "MPC",
        "SHEL.L", "BP.L", "TTE.PA", "EQNR", "RWE.DE",
    ]},
    "DAX 40": {"class": "equity", "tickers": [
        "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "AIR.DE", "MRK.DE", "MBG.DE",
        "BMW.DE", "VOW3.DE", "BAS.DE", "BAYN.DE", "ADS.DE", "IFX.DE", "DB1.DE",
        "MUV2.DE", "DBK.DE", "RWE.DE", "EOAN.DE", "HEN3.DE", "BEI.DE", "FRE.DE",
        "HEI.DE", "SHL.DE", "SY1.DE", "VNA.DE", "ZAL.DE", "CON.DE", "P911.DE",
        "PAH3.DE", "1COV.DE", "MTX.DE", "RHM.DE", "SRT3.DE", "CBK.DE", "ENR.DE",
        "BNR.DE", "HNR1.DE", "DHL.DE", "QIA.DE", "FME.DE",
    ]},
    "US large caps": {"class": "equity", "tickers": [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "BRK-B", "LLY", "AVGO",
        "JPM", "V", "MA", "UNH", "XOM", "JNJ", "PG", "HD", "COST", "ABBV", "KO",
        "PEP", "CVX", "ADBE", "CRM", "MCD", "CSCO", "ACN", "TMO", "ORCL", "NKE",
        "TXN", "QCOM", "AMD", "PFE", "DIS", "CAT", "DE", "GS", "BLK", "SPGI",
    ]},
    "Commodities & macro": {"class": "macro", "tickers": [
        "GLD", "SLV", "PPLT", "PALL",
        "USO", "BNO", "UNG",
        "CORN", "WEAT", "SOYB", "DBA",
        "DBC", "COPX", "URA",
        "^SPX", "^NDX", "^DAX",
        "UUP", "TLT", "IEF", "HYG",
    ]},
    "ETF allocation": {"class": "macro", "tickers": [
        "VOO", "VTI", "QQQ", "IWM", "EFA", "EEM", "VGK", "EWJ",
        "AGG", "TLT", "IEF", "LQD", "HYG", "TIP", "GLD", "DBC", "VNQ",
    ]},
}

FACTOR_MAP = {
    "Value": {
        "earnings_yield": True, "fcf_yield": True,
        "ev_ebitda": False, "price_to_book": False,
    },
    "Quality": {
        "roe": True, "gross_margin": True,
        "operating_margin": True, "profit_margin": True,
    },
    "Growth": {"revenue_growth": True, "earnings_growth": True},
    "Health": {
        "net_debt_ebitda": False, "debt_to_equity": False, "current_ratio": True,
    },
    "Momentum": {"mom_12_1": True, "mom_6_1": True, "above_200d": True},
}

DEFAULT_WEIGHTS = {
    "Value": 25, "Quality": 25, "Growth": 15, "Health": 15, "Momentum": 20,
}

METRIC_LABELS = {
    "price": ("Price", "{:,.2f}"),
    "market_cap": ("Mkt cap (bn)", "{:,.1f}"),
    "pe": ("P/E", "{:,.1f}"),
    "earnings_yield": ("Earnings yield", "{:.1%}"),
    "fcf_yield": ("FCF yield", "{:.1%}"),
    "ev_ebitda": ("EV/EBITDA", "{:,.1f}"),
    "price_to_book": ("P/B", "{:,.2f}"),
    "roe": ("ROE", "{:.1%}"),
    "gross_margin": ("Gross margin", "{:.1%}"),
    "operating_margin": ("Op. margin", "{:.1%}"),
    "profit_margin": ("Net margin", "{:.1%}"),
    "revenue_growth": ("Rev. growth", "{:.1%}"),
    "earnings_growth": ("EPS growth", "{:.1%}"),
    "net_debt_ebitda": ("Net debt/EBITDA", "{:,.2f}"),
    "debt_to_equity": ("Debt/Equity", "{:,.1f}"),
    "current_ratio": ("Current ratio", "{:,.2f}"),
    "mom_12_1": ("Mom 12-1", "{:.1%}"),
    "mom_6_1": ("Mom 6-1", "{:.1%}"),
    "above_200d": ("vs 200d MA", "{:.1%}"),
    "volatility": ("Volatility (ann.)", "{:.1%}"),
    "max_drawdown": ("Max drawdown", "{:.1%}"),
    "dividend_yield": ("Dividend yield", "{:.2%}"),
    "beta": ("Beta", "{:,.2f}"),
}


def universe_tickers(name: str) -> list[str]:
    return list(UNIVERSES[name]["tickers"])


def universe_class(name: str) -> str:
    return UNIVERSES[name].get("class", "equity")
