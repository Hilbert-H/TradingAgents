"""Akshare implementations for sentiment proxies (A-share)."""

import logging
import time

import akshare as ak
import pandas as pd

from .akshare_common import ak_retry, format_df_as_md, to_ak_symbol, to_ak_symbol_with_market

logger = logging.getLogger(__name__)


# Process-level cache for the eastmoney hot-rank snapshot. The snapshot is
# a top-100 board-wide list — exact same data for every ticker — so caching
# turns N×500 hits into ~N. The detail endpoint (per-ticker history) is
# stable on its own and doesn't need caching.
_HOT_RANK_SNAPSHOT_CACHE: dict = {"ts": 0.0, "df": None}
_HOT_RANK_SNAPSHOT_TTL_SECS = 900   # 15 minutes


def _get_hot_rank_snapshot() -> "pd.DataFrame | None":
    """Cached fetch of ak.stock_hot_rank_em() with longer in-process TTL.

    This endpoint is the rate-limited bottleneck (it returns the board-wide
    top-100 attention list and east-money tightens limits on it under load).
    Wrapped in our own try/except so a single bad call doesn't poison the
    whole report — the per-stock detail endpoint is independent and works
    even when the snapshot is unavailable.
    """
    now = time.time()
    cached = _HOT_RANK_SNAPSHOT_CACHE["df"]
    if cached is not None and now - _HOT_RANK_SNAPSHOT_CACHE["ts"] < _HOT_RANK_SNAPSHOT_TTL_SECS:
        return cached
    try:
        df = ak.stock_hot_rank_em()
        if df is not None and not df.empty:
            _HOT_RANK_SNAPSHOT_CACHE["df"] = df
            _HOT_RANK_SNAPSHOT_CACHE["ts"] = now
            return df
    except Exception as e:
        logger.warning("stock_hot_rank_em snapshot unavailable (cached miss): %s", e)
    return None


@ak_retry()
def get_stock_hot_rank_akshare(ticker: str, curr_date: str) -> str:
    """Eastmoney attention rank for an A-share — cached snapshot + per-stock history.

    Two complementary signals:
      1. **Snapshot rank** — is this ticker currently in the top-100 attention
         list? Cached per process for 15 min so 500 tickers in a batch don't
         all hit the rate-limited endpoint.
      2. **Per-stock history** — daily attention rank over the last ~year.
         Always tried; this endpoint is more reliable than the snapshot.
    """
    symbol = to_ak_symbol(ticker)
    market_symbol = to_ak_symbol_with_market(ticker)  # e.g. "SH600487"
    sections = []

    snapshot = _get_hot_rank_snapshot()
    if snapshot is not None:
        code_col = next((c for c in snapshot.columns if "代码" in c), None)
        if code_col:
            hit = snapshot[snapshot[code_col].astype(str).str.zfill(6) == symbol]
            if not hit.empty:
                sections.append(format_df_as_md(hit, "East-Money Hot Rank (current snapshot)", max_rows=5))
            else:
                sections.append(
                    "## East-Money Hot Rank (current snapshot)\n\n"
                    f"_{ticker} not in current top-{len(snapshot)} attention list._"
                )
    else:
        sections.append(
            "## East-Money Hot Rank (current snapshot)\n\n"
            "_Snapshot endpoint rate-limited; relying on per-stock history below._"
        )

    try:
        detail = ak.stock_hot_rank_detail_em(symbol=market_symbol)
        if detail is not None and not detail.empty:
            # Keep only the most recent 30 trading days — older history is
            # noise for short-horizon sentiment work.
            detail = detail.tail(30)
        sections.append(format_df_as_md(detail, "East-Money Hot Rank (last 30 trading days)", max_rows=30))
    except Exception as e:
        logger.warning("stock_hot_rank_detail_em failed for %s: %s", market_symbol, e)
        sections.append("## East-Money Hot Rank (last 30 trading days)\n\n_Source unavailable._")

    return f"# Attention rank for {ticker} (as of {curr_date})\n\n" + "\n\n".join(sections)


@ak_retry()
def get_shareholder_count_akshare(ticker: str, curr_date: str) -> str:
    """Quarterly shareholder count history — chip-concentration proxy."""
    symbol = to_ak_symbol(ticker)
    try:
        # stock_zh_a_gdhs_detail_em takes a 6-digit ticker code and returns
        # per-stock quarterly shareholder history (stock_zh_a_gdhs takes a
        # period-date string and returns the full market snapshot instead).
        df = ak.stock_zh_a_gdhs_detail_em(symbol=symbol)
    except Exception as e:
        logger.warning("stock_zh_a_gdhs_detail_em failed for %s: %s", symbol, e)
        return f"## Shareholder count for {ticker}\n\n_Source unavailable: {e}_"
    return format_df_as_md(df, f"Shareholder count history for {ticker} (as of {curr_date})", max_rows=20)


@ak_retry()
def get_research_reports_akshare(ticker: str, start_date: str, end_date: str) -> str:
    """Analyst research reports (target prices, ratings) filtered to a date window."""
    symbol = to_ak_symbol(ticker)
    try:
        df = ak.stock_research_report_em(symbol=symbol)
    except Exception as e:
        logger.warning("stock_research_report_em failed for %s: %s", symbol, e)
        return f"## Research reports for {ticker}\n\n_Source unavailable: {e}_"

    if df is None or df.empty:
        return f"## Research reports for {ticker} {start_date} → {end_date}\n\n_No reports._"

    date_col = next((c for c in df.columns if "日期" in c or "date" in c.lower()), None)
    if date_col:
        df["_dt"] = pd.to_datetime(df[date_col], errors="coerce")
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
        df = df[(df["_dt"] >= start) & (df["_dt"] < end)].drop(columns=["_dt"])

    return format_df_as_md(df, f"Research reports for {ticker} {start_date} → {end_date}", max_rows=20)
