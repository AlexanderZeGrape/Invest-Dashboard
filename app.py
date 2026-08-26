"""Signal Desk — a factor screener, valuation sandbox and portfolio monitor.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

import analytics
import data as dl
from config import CACHE_HOURS, DEFAULT_WEIGHTS, FACTOR_MAP, METRIC_LABELS, UNIVERSES

st.set_page_config(page_title="Signal Desk", page_icon="◧", layout="wide")

FACTORS = list(FACTOR_MAP.keys())

st.markdown(
    """
    <style>
      .stApp { font-feature-settings: "tnum" 1; }
      [data-testid="stMetricValue"] { font-family: ui-monospace, "SFMono-Regular", Menlo, monospace; }
      div[data-testid="stDataFrame"] { font-variant-numeric: tabular-nums; }
      h1, h2, h3 { letter-spacing: -0.015em; }
      .desk-note { color: #5b6672; font-size: 0.85rem; line-height: 1.45; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# small compatibility helpers
# --------------------------------------------------------------------------
def wide(render, *args, **kwargs):
    """st.dataframe / st.plotly_chart changed their full-width argument."""
    try:
        return render(*args, width="stretch", **kwargs)
    except TypeError:
        return render(*args, use_container_width=True, **kwargs)


@st.cache_resource
def get_connection():
    return dl.connect()


con = get_connection()


def load_holdings() -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM holdings", con)


def load_journal() -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM journal ORDER BY entry_date DESC", con)


# --------------------------------------------------------------------------
# sidebar: source, universe, weights, filters
# --------------------------------------------------------------------------
st.sidebar.title("Signal Desk")

source = st.sidebar.radio(
    "Data source",
    ["Demo (offline)", "Live (yfinance)"],
    help="Demo fills the app with synthetic data so you can click around without a connection.",
)
demo_mode = source.startswith("Demo")

universe_name = st.sidebar.selectbox("Universe", list(UNIVERSES.keys()))
custom = st.sidebar.text_area(
    "Extra tickers", placeholder="RHM.DE, ASML.AS, NVDA", height=68
)
tickers = list(UNIVERSES[universe_name])
if custom.strip():
    tickers += [t.strip().upper() for t in custom.replace("\n", ",").split(",") if t.strip()]
    tickers = list(dict.fromkeys(tickers))

age = dl.snapshot_age_hours(con, universe_name)
if not demo_mode:
    label = "never fetched" if age is None else f"cached {age:.1f}h ago"
    st.sidebar.caption(f"{len(tickers)} tickers · {label}")
refresh = st.sidebar.button("Fetch fresh data", disabled=demo_mode)

st.sidebar.divider()
st.sidebar.subheader("Factor weights")
weights = {}
for factor in FACTORS:
    weights[factor] = st.sidebar.slider(factor, 0, 50, DEFAULT_WEIGHTS[factor], step=5)

st.sidebar.divider()
st.sidebar.subheader("Filters")
min_cap = st.sidebar.number_input("Min market cap (bn)", 0.0, 5000.0, 0.0, step=1.0)
profitable_only = st.sidebar.checkbox("Profitable only (P/E > 0)", value=False)
min_coverage = st.sidebar.slider("Min data coverage", 0, 100, 40, step=5) / 100


# --------------------------------------------------------------------------
# data loading
# --------------------------------------------------------------------------
def get_data(force: bool = False):
    if demo_mode:
        return dl.demo_snapshot()

    stale = age is None or age > CACHE_HOURS
    if force or stale:
        bar = st.progress(0.0, text="Fetching…")

        def report(fraction, ticker):
            bar.progress(min(fraction, 1.0), text=f"Fetching {ticker}")

        try:
            snapshot, prices = dl.build_snapshot(tickers, progress=report)
        except ModuleNotFoundError:
            bar.empty()
            st.error("yfinance is not installed. Run: pip install -r requirements.txt")
            st.stop()
        except Exception as exc:  # network hiccup, rate limit, bad ticker
            bar.empty()
            st.error(f"Fetch failed: {exc}. Falling back to the last cached snapshot.")
            snapshot, prices = dl.load_snapshot(con, universe_name), dl.load_prices(con, universe_name)
        else:
            bar.empty()
            if not snapshot.empty:
                dl.save_snapshot(con, universe_name, snapshot)
                dl.save_prices(con, universe_name, prices)
        return snapshot, prices

    return dl.load_snapshot(con, universe_name), dl.load_prices(con, universe_name)


snapshot, prices = get_data(force=refresh)

if snapshot is None or snapshot.empty:
    st.warning("No data yet. Press **Fetch fresh data**, or switch to Demo to explore the app.")
    st.stop()

numeric_cols = [c for c in snapshot.columns if c not in ("name", "sector", "currency",
                                                         "universe", "fetched_at")]
for col in numeric_cols:
    snapshot[col] = pd.to_numeric(snapshot[col], errors="coerce")

table = analytics.rank_table(snapshot, weights)

mask = pd.Series(True, index=table.index)
if min_cap > 0 and "market_cap" in table:
    mask &= table["market_cap"].fillna(0) >= min_cap
if profitable_only and "pe" in table:
    mask &= table["pe"] > 0
mask &= table["Coverage"].fillna(0) >= min_coverage

sectors = sorted(x for x in table["sector"].dropna().unique())
chosen_sectors = st.sidebar.multiselect("Sectors", sectors, default=sectors)
if chosen_sectors:
    mask &= table["sector"].isin(chosen_sectors)

filtered = table[mask]


# --------------------------------------------------------------------------
# tabs
# --------------------------------------------------------------------------
screen_tab, company_tab, value_tab, portfolio_tab, journal_tab, method_tab = st.tabs(
    ["Screen", "Company", "Valuation", "Portfolio", "Journal", "Method"]
)

# ---------------------------------------------------------------- Screen ---
with screen_tab:
    left, right = st.columns([3, 1])
    with left:
        st.subheader(f"{universe_name} — ranked")
        st.markdown(
            f"<div class='desk-note'>{len(filtered)} of {len(table)} names pass your filters. "
            "Score is a weighted blend of cross-sectional percentile ranks, so it says how a "
            "company stacks up against this universe today — not whether it is a good "
            "investment.</div>",
            unsafe_allow_html=True,
        )
    with right:
        if demo_mode:
            st.info("Demo data — figures are synthetic.")

    display_cols = ["name", "sector", "Score"] + FACTORS + [
        "price", "market_cap", "pe", "fcf_yield", "ev_ebitda", "roe",
        "revenue_growth", "net_debt_ebitda", "mom_12_1", "Coverage",
    ]
    display_cols = [c for c in display_cols if c in filtered.columns]
    view = filtered[display_cols].copy()

    column_config = {
        "name": st.column_config.TextColumn("Company", width="medium"),
        "sector": st.column_config.TextColumn("Sector", width="small"),
        "Score": st.column_config.ProgressColumn(
            "Score", min_value=0, max_value=100, format="%.0f"
        ),
        "Coverage": st.column_config.NumberColumn("Data", format="%.0f%%"),
    }
    for factor in FACTORS:
        if factor in view:
            column_config[factor] = st.column_config.NumberColumn(factor, format="%.0f")
    for key, (label, fmt) in METRIC_LABELS.items():
        if key in view:
            spec = "%.1f%%" if fmt.endswith("%}") else "%.2f"
            column_config[key] = st.column_config.NumberColumn(label, format=spec)

    if "Coverage" in view:
        view["Coverage"] = view["Coverage"] * 100
    for pct_col in ("fcf_yield", "roe", "revenue_growth", "mom_12_1"):
        if pct_col in view:
            view[pct_col] = view[pct_col] * 100

    wide(st.dataframe, view, column_config=column_config, height=520)

    st.download_button(
        "Download ranking as CSV",
        filtered.to_csv().encode("utf-8"),
        file_name=f"ranking_{universe_name.replace(' ', '_')}_{dt.date.today()}.csv",
        mime="text/csv",
    )

    st.divider()
    chart_left, chart_right = st.columns(2)
    with chart_left:
        st.caption("Value vs Quality — top right is cheap and profitable")
        scatter_source = filtered.reset_index().rename(columns={"index": "ticker"})
        fig = px.scatter(
            scatter_source,
            x="Value", y="Quality", color="Score", size="market_cap",
            hover_name="name", text="ticker",
            color_continuous_scale="Teal", size_max=38,
        )
        fig.update_traces(textposition="top center", textfont_size=9)
        fig.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=420)
        wide(st.plotly_chart, fig)
    with chart_right:
        st.caption("Median factor score by sector")
        by_sector = filtered.groupby("sector")[FACTORS].median(numeric_only=True)
        heat = go.Figure(
            go.Heatmap(
                z=by_sector.values, x=by_sector.columns, y=by_sector.index,
                colorscale="Teal", zmin=0, zmax=100,
            )
        )
        heat.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=420)
        wide(st.plotly_chart, heat)

# --------------------------------------------------------------- Company ---
with company_tab:
    options = list(filtered.index)
    if not options:
        st.info("No names pass the current filters.")
    else:
        ticker = st.selectbox(
            "Company", options,
            format_func=lambda t: f"{t} — {filtered.loc[t, 'name']}",
        )
        row = filtered.loc[ticker]
        st.subheader(str(row.get("name", ticker)))
        st.caption(f"{row.get('sector', '—')} · {ticker}")

        cards = st.columns(5)
        cards[0].metric("Score", f"{row['Score']:.0f}" if pd.notna(row["Score"]) else "—")
        cards[1].metric("Price", f"{row.get('price', float('nan')):,.2f}"
                        if pd.notna(row.get("price")) else "—")
        cards[2].metric("P/E", f"{row.get('pe', float('nan')):,.1f}"
                        if pd.notna(row.get("pe")) else "—")
        cards[3].metric("FCF yield", f"{row.get('fcf_yield', float('nan')):.1%}"
                        if pd.notna(row.get("fcf_yield")) else "—")
        cards[4].metric("Net debt/EBITDA", f"{row.get('net_debt_ebitda', float('nan')):,.2f}"
                        if pd.notna(row.get("net_debt_ebitda")) else "—")

        chart_col, factor_col = st.columns([2, 1])
        with chart_col:
            if not prices.empty and ticker in prices.columns:
                series = prices[ticker].dropna()
                line = go.Figure()
                line.add_trace(go.Scatter(x=series.index, y=series.values, name="Price",
                                          line=dict(width=1.6)))
                if len(series) >= 200:
                    line.add_trace(go.Scatter(
                        x=series.index, y=series.rolling(200).mean(),
                        name="200d average", line=dict(width=1, dash="dot")))
                line.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=360,
                                   legend=dict(orientation="h", y=1.1))
                wide(st.plotly_chart, line)
            else:
                st.info("No price history cached for this ticker.")
        with factor_col:
            factor_values = [row.get(f, np.nan) for f in FACTORS]
            bar = go.Figure(go.Bar(x=factor_values, y=FACTORS, orientation="h"))
            bar.update_layout(xaxis_range=[0, 100], height=360,
                              margin=dict(l=10, r=10, t=10, b=10))
            wide(st.plotly_chart, bar)

        metric_rows = []
        for key, (label, fmt) in METRIC_LABELS.items():
            if key in row.index and pd.notna(row[key]):
                try:
                    metric_rows.append({"Metric": label, "Value": fmt.format(row[key])})
                except (ValueError, TypeError):
                    pass
        if metric_rows:
            wide(st.dataframe, pd.DataFrame(metric_rows), hide_index=True, height=280)

        st.divider()
        if demo_mode:
            st.caption("Annual statements and the F-Score need live data.")
        else:
            if st.button("Pull annual statements", key="statements"):
                with st.spinner("Reading filings…"):
                    statements = dl.fetch_statements(ticker)
                    history = dl.statement_history(statements)
                    score, tested, checks = dl.piotroski(statements)
                if history.empty:
                    st.warning("No statement data returned for this ticker.")
                else:
                    st.markdown(f"**Piotroski F-Score: {score} / {tested} criteria tested**")
                    checks_frame = pd.DataFrame(
                        [{"Criterion": c, "Pass": ok} for c, ok in checks]
                    )
                    detail_left, detail_right = st.columns([1, 2])
                    with detail_left:
                        wide(st.dataframe, checks_frame, hide_index=True, height=320)
                    with detail_right:
                        st.caption("Annual figures, millions")
                        wide(st.dataframe, history.round(0), height=320)

# ------------------------------------------------------------- Valuation ---
with value_tab:
    st.subheader("Two-stage discounted cash flow")
    st.markdown(
        "<div class='desk-note'>A DCF tells you what you must believe for today's price to "
        "make sense. Run it backwards: change growth until the output matches the market, then "
        "ask whether that assumption is defensible.</div>",
        unsafe_allow_html=True,
    )

    inputs, results = st.columns([1, 2])
    with inputs:
        base_fcf = st.number_input("Base free cash flow (m)", value=1000.0, step=50.0)
        growth = st.slider("Growth, stage 1 (% p.a.)", -10.0, 30.0, 6.0, 0.5) / 100
        years = st.slider("Stage 1 length (years)", 3, 15, 10)
        terminal_growth = st.slider("Terminal growth (%)", 0.0, 4.0, 2.0, 0.1) / 100
        wacc = st.slider("Discount rate / WACC (%)", 3.0, 20.0, 8.5, 0.1) / 100
        net_debt = st.number_input("Net debt (m)", value=0.0, step=50.0)
        shares = st.number_input("Shares outstanding (m)", value=100.0, step=1.0)

    result = analytics.dcf(base_fcf, growth, years, terminal_growth, wacc, net_debt, shares)
    with results:
        if "error" in result:
            st.error(result["error"])
        else:
            top = st.columns(3)
            top[0].metric("Value per share", f"{result['per_share']:,.2f}")
            top[1].metric("Equity value (m)", f"{result['equity_value']:,.0f}")
            top[2].metric("From terminal value", f"{result['terminal_share']:.0%}")
            if result["terminal_share"] > 0.75:
                st.warning(
                    "Over three quarters of the value sits in the terminal assumption. "
                    "That is a forecast about the year 2040, not an analysis of the business."
                )
            grid = analytics.dcf_sensitivity(
                base_fcf, growth, years, shares, net_debt,
                [wacc + d for d in (-0.02, -0.01, 0, 0.01, 0.02)],
                [terminal_growth + d for d in (-0.01, -0.005, 0, 0.005, 0.01)],
            )
            st.caption("Value per share — discount rate (rows) vs terminal growth (columns)")
            wide(st.dataframe, grid.round(2))

# ------------------------------------------------------------- Portfolio ---
with portfolio_tab:
    st.subheader("Holdings")
    with st.form("add_holding", clear_on_submit=True):
        cols = st.columns([2, 1, 1, 1, 1])
        new_ticker = cols[0].text_input("Ticker")
        new_shares = cols[1].number_input("Shares", min_value=0.0, step=1.0)
        new_price = cols[2].number_input("Buy price", min_value=0.0, step=1.0)
        new_date = cols[3].date_input("Buy date", value=dt.date.today())
        cols[4].markdown("<br>", unsafe_allow_html=True)
        submitted = cols[4].form_submit_button("Add")
    if submitted and new_ticker.strip():
        con.execute(
            "INSERT INTO holdings (ticker, shares, buy_price, buy_date) VALUES (?,?,?,?)",
            (new_ticker.strip().upper(), new_shares, new_price, str(new_date)),
        )
        con.commit()
        st.rerun()

    holdings = load_holdings()
    if holdings.empty:
        st.info("No positions yet. Add one above — paper positions count.")
    else:
        view = analytics.portfolio_view(holdings, snapshot, table)
        totals = st.columns(4)
        totals[0].metric("Market value", f"{view['value'].sum(skipna=True):,.0f}")
        totals[1].metric("Cost", f"{view['cost'].sum(skipna=True):,.0f}")
        pnl = view["pnl"].sum(skipna=True)
        cost = view["cost"].sum(skipna=True)
        totals[2].metric("Open P&L", f"{pnl:,.0f}",
                         f"{pnl / cost:.1%}" if cost else None)
        totals[3].metric("Positions", f"{len(view)}")

        show = view[["ticker", "name", "sector", "shares", "buy_price", "price",
                     "value", "weight", "pnl", "pnl_pct", "score"]]
        wide(
            st.dataframe, show, hide_index=True,
            column_config={
                "weight": st.column_config.ProgressColumn(
                    "Weight", min_value=0.0, max_value=1.0, format="%.1f%%"),
                "pnl_pct": st.column_config.NumberColumn("P&L %", format="%.1f%%"),
                "score": st.column_config.NumberColumn("Score", format="%.0f"),
            },
        )

        left, right = st.columns(2)
        with left:
            allocation = view.groupby("sector")["value"].sum().reset_index()
            pie = px.pie(allocation, names="sector", values="value", hole=0.55)
            pie.update_layout(margin=dict(l=10, r=10, t=30, b=10), height=360,
                              title="Sector allocation")
            wide(st.plotly_chart, pie)
        with right:
            risk = analytics.portfolio_risk(view, prices)
            if risk:
                stats = st.columns(4)
                stats[0].metric("Return (ann.)", f"{risk['annual_return']:.1%}")
                stats[1].metric("Volatility", f"{risk['volatility']:.1%}")
                stats[2].metric("Sharpe", f"{risk['sharpe']:.2f}")
                stats[3].metric("Max drawdown", f"{risk['max_drawdown']:.1%}")
                curve = risk["curve"]
                fig = go.Figure(go.Scatter(x=curve.index, y=curve.values))
                fig.update_layout(margin=dict(l=10, r=10, t=30, b=10), height=280,
                                  title="Growth of 1 unit, current weights")
                wide(st.plotly_chart, fig)
                st.caption(
                    "Backward-looking with today's weights — it is not your realised return."
                )
            else:
                st.info("No overlapping price history for these positions.")

        drop = st.selectbox("Remove position", ["—"] + [
            f"{r.id}: {r.ticker} ({r.shares:g})" for r in holdings.itertuples()
        ])
        if drop != "—" and st.button("Remove"):
            con.execute("DELETE FROM holdings WHERE id = ?", (int(drop.split(":")[0]),))
            con.commit()
            st.rerun()

# --------------------------------------------------------------- Journal ---
with journal_tab:
    st.subheader("Thesis journal")
    st.markdown(
        "<div class='desk-note'>Write the thesis before you buy and set a review date. "
        "Six months later this is the only honest record of whether you were right for the "
        "reason you thought.</div>",
        unsafe_allow_html=True,
    )
    with st.form("add_entry", clear_on_submit=True):
        row1 = st.columns([1, 1, 1, 1])
        j_ticker = row1[0].text_input("Ticker")
        j_conviction = row1[1].slider("Conviction", 1, 5, 3)
        j_target = row1[2].number_input("Target price", min_value=0.0, step=1.0)
        j_review = row1[3].date_input("Review on", value=dt.date.today() + dt.timedelta(days=180))
        j_thesis = st.text_area("Thesis — why does this make money?", height=90)
        j_risks = st.text_area("What would prove me wrong?", height=68)
        saved = st.form_submit_button("Save entry")
    if saved and j_ticker.strip():
        con.execute(
            """INSERT INTO journal
               (ticker, entry_date, conviction, target_price, review_date, thesis, risks, outcome)
               VALUES (?,?,?,?,?,?,?,?)""",
            (j_ticker.strip().upper(), str(dt.date.today()), j_conviction, j_target,
             str(j_review), j_thesis, j_risks, ""),
        )
        con.commit()
        st.rerun()

    entries = load_journal()
    if entries.empty:
        st.info("Nothing recorded yet.")
    else:
        due = entries[pd.to_datetime(entries["review_date"], errors="coerce")
                      <= pd.Timestamp.today()]
        if not due.empty:
            st.warning(f"{len(due)} thesis review(s) due: {', '.join(due['ticker'])}")
        for entry in entries.itertuples():
            with st.expander(
                f"{entry.ticker} · {entry.entry_date} · conviction {entry.conviction}/5"
            ):
                st.write(entry.thesis or "_no thesis recorded_")
                if entry.risks:
                    st.caption(f"Disproved by: {entry.risks}")
                st.caption(f"Target {entry.target_price} · review {entry.review_date}")
                if st.button("Delete", key=f"del_{entry.id}"):
                    con.execute("DELETE FROM journal WHERE id = ?", (entry.id,))
                    con.commit()
                    st.rerun()

# ---------------------------------------------------------------- Method ---
with method_tab:
    st.subheader("How the score is built")
    st.markdown(
        """
Every metric is converted into a **percentile rank inside the current universe**, then averaged
within its factor, then blended using your sidebar weights. A score of 80 means: better than 80%
of these companies on this blend of measures, right now.

| Factor | Metrics |
|---|---|
| Value | Earnings yield, FCF yield, EV/EBITDA, P/B |
| Quality | ROE, gross margin, operating margin, net margin |
| Growth | Revenue growth, EPS growth |
| Health | Net debt/EBITDA, debt/equity, current ratio |
| Momentum | 12-1 and 6-1 price momentum, distance from the 200-day average |

Extreme values are winsorised at the 2nd and 98th percentile so one broken data point cannot
dominate a rank. Negative P/E, P/B and EV/EBITDA are treated as missing rather than as cheap.
Where a metric is unavailable the factor is computed from what remains, and the **Data** column
shows how much of the input was actually present — treat anything under 60% with suspicion.

### What this cannot do

- **Ranks are relative.** In an expensive universe the cheapest name still scores 100.
- **No forward view.** Everything here is trailing data; markets price the future.
- **Free data has errors.** Cross-check anything surprising against the annual report before acting.
- **Momentum and value fight each other.** That is intended — it stops one factor running the book.
- **Sector effects are real.** Banks and software companies are not comparable on EV/EBITDA;
  filter by sector before you compare.

### Sensible next steps

1. Add a backtest: store snapshots monthly, then measure forward returns by score decile.
2. Add sector-neutral ranking (rank inside sector, not across the universe).
3. Add an earnings calendar and alerts for review dates.
        """
    )
