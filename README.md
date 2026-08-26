[README.md](https://github.com/user-attachments/files/31451881/README.md)
# Signal Desk

A factor screener, valuation sandbox and portfolio monitor. Runs locally, uses free data,
needs no API keys.

## Run it

```bash
cd invest-dashboard
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

It opens at `http://localhost:8501`. The sidebar starts in **Demo (offline)** mode with synthetic
data so you can see how everything works immediately. Switch to **Live (yfinance)** and press
**Fetch fresh data** to pull real numbers. The first fetch of ~45 tickers takes 1–3 minutes;
after that it is cached in `signaldesk.db` for 12 hours.

## What's in it

| Tab | What it does |
|---|---|
| **Screen** | Every company ranked 0–100 on a weighted blend of Value, Quality, Growth, Health and Momentum. Adjust the weights in the sidebar and the ranking rebuilds live. CSV export included. |
| **Company** | Price history against the 200-day average, factor breakdown, full metric sheet, and — in live mode — annual statements plus a Piotroski F-Score. |
| **Valuation** | Two-stage DCF with a sensitivity grid across discount rate and terminal growth. Warns when the terminal value carries more than 75% of the answer. |
| **Portfolio** | Positions, weights, P&L, sector allocation, annualised return, volatility, Sharpe and max drawdown. |
| **Journal** | Write the thesis before you buy, set a review date, get reminded when it's due. |
| **Method** | What the score means and, more importantly, what it cannot tell you. |

## Files

```
config.py      universes, factor definitions, default weights   ← edit this first
data.py        yfinance fetch, SQLite cache, demo generator
analytics.py   ranking, composite score, DCF, portfolio maths   ← pure functions, easy to test
app.py         the Streamlit interface
signaldesk.db  created on first run (cache + your holdings + journal)
```

To screen different companies, edit `UNIVERSES` in `config.py`, or paste tickers into
**Extra tickers** in the sidebar. Yahoo suffixes: `.DE` Xetra, `.PA` Paris, `.AS` Amsterdam,
`.L` London, `.SW` Zurich, `.MI` Milan. US tickers have no suffix.

To change what "good" means, edit `FACTOR_MAP` in `config.py` — add a metric, set `True` if a
higher value is better, and it flows into the ranking automatically.

## Deploy it for free

Push the folder to a GitHub repo, then connect it at share.streamlit.io. Note that on a public
deployment the SQLite file resets when the app sleeps, so treat a hosted version as read-only
research and keep your portfolio and journal on the local copy.

## If something breaks

- **`Fetch failed` / empty rows** — Yahoo rate-limits bursts. Wait a minute, or run
  `pip install -U yfinance`; the library needs updating whenever Yahoo changes its endpoints.
- **A ticker returns nothing** — check the exact symbol on finance.yahoo.com first.
- **Statements look wrong** — yfinance renames statement rows regularly. `data.pick_row()` does a
  fuzzy match; add the new label to the candidate list if a row goes missing.
- **Metrics are missing for a company** — the **Data** column shows coverage. Under 60% means the
  score rests on a handful of inputs; don't trust the rank.

## Known limits

Ranks are relative to the universe you loaded, not to fair value. Everything is trailing data.
Free fundamentals contain errors — verify anything surprising against the annual report. Banks
and insurers are not comparable to industrials on EV/EBITDA or debt ratios, so filter by sector
before drawing conclusions.

## Where to take it next

1. **Backtest.** Save a dated snapshot every month, then compare forward returns by score decile.
   Until you do this, the weights are opinions.
2. **Sector-neutral ranking.** Rank inside each sector, then blend, so the score stops being a
   bet on whichever sector is cheap.
3. **Alerts.** A GitHub Action running daily that emails you when a watchlist name crosses a
   score threshold or a review date comes due.
