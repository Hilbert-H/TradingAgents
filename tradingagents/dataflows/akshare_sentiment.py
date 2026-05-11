"""Akshare implementations for sentiment proxies (A-share)."""

import logging
import akshare as ak
import pandas as pd

from .akshare_common import ak_retry, format_df_as_md, to_ak_symbol, to_ak_symbol_with_market

logger = logging.getLogger(__name__)


@ak_retry()
def get_stock_hot_rank_akshare(ticker: str, curr_date: str) -> str:
    """Combined east-money attention rank for an A-share (global snapshot + per-stock history)."""
    symbol = to_ak_symbol(ticker)
    market_symbol = to_ak_symbol_with_market(ticker)  # e.g. "SH600487"
    sections = []

    try:
        em = ak.stock_hot_rank_em()       # full board snapshot
        if em is not None and not em.empty:
            code_col = next((c for c in em.columns if "代码" in c), None)
            if code_col:
                em = em[em[code_col].astype(str).str.zfill(6) == symbol]
        sections.append(format_df_as_md(em, "East-Money Hot Rank (snapshot)", max_rows=10))
    except Exception as e:
        logger.warning("stock_hot_rank_em failed: %s", e)
        sections.append("## East-Money Hot Rank (snapshot)\n\n_Source unavailable._")

    try:
        # stock_hot_rank_wc (同花顺) was removed; use stock_hot_rank_detail_em for per-stock history
        detail = ak.stock_hot_rank_detail_em(symbol=market_symbol)
        sections.append(format_df_as_md(detail, "East-Money Hot Rank (history)", max_rows=20))
    except Exception as e:
        logger.warning("stock_hot_rank_detail_em failed for %s: %s", market_symbol, e)
        sections.append("## East-Money Hot Rank (history)\n\n_Source unavailable._")

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
