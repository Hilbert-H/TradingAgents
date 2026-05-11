"""Akshare implementations for news / announcements."""

import logging
from datetime import datetime

import akshare as ak
import pandas as pd

from .akshare_common import (
    ak_retry, format_df_as_md, is_a_share,
    to_ak_symbol, NotApplicableError,
)

logger = logging.getLogger(__name__)


@ak_retry()
def get_news_akshare(ticker: str, start_date: str, end_date: str) -> str:
    """Per-stock news for an A-share ticker, filtered to [start_date, end_date]."""
    symbol = to_ak_symbol(ticker)
    df = ak.stock_news_em(symbol=symbol)
    if df is None or df.empty:
        return f"## News for {ticker} {start_date} → {end_date}\n\n_No news found._"

    # Akshare returns publish times as strings like '2026-05-07 09:15:00'
    time_col = next((c for c in df.columns if "时间" in c or "publish" in c.lower()), None)
    if time_col:
        df["_dt"] = pd.to_datetime(df[time_col], errors="coerce")
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date) + pd.Timedelta(days=1)  # inclusive of end_date
        df = df[(df["_dt"] >= start) & (df["_dt"] < end)].drop(columns=["_dt"])

    if df.empty:
        return f"## News for {ticker} {start_date} → {end_date}\n\n_No news in window._"

    return format_df_as_md(df, f"News for {ticker} {start_date} → {end_date}", max_rows=20)


@ak_retry()
def get_global_news_akshare(curr_date: str, look_back_days: int = 7, limit: int = 30) -> str:
    """Macro/global financial news from akshare's east-money aggregator."""
    df = ak.stock_info_global_em()
    if df is None or df.empty:
        return f"## Global news as of {curr_date}\n\n_No data._"

    # Filter to recent window if a time column exists
    time_col = next((c for c in df.columns if "时间" in c), None)
    if time_col:
        df["_dt"] = pd.to_datetime(df[time_col], errors="coerce")
        end = pd.to_datetime(curr_date) + pd.Timedelta(days=1)
        start = end - pd.Timedelta(days=look_back_days + 1)
        df = df[(df["_dt"] >= start) & (df["_dt"] < end)].drop(columns=["_dt"])

    df = df.head(limit)
    return format_df_as_md(df, f"Global news as of {curr_date} (last {look_back_days}d)", max_rows=limit)


@ak_retry()
def get_announcements_akshare(ticker: str, start_date: str, end_date: str) -> str:
    """Legal disclosure announcements (法定信披) from CNINFO/EastMoney."""
    symbol = to_ak_symbol(ticker)

    # stock_notice_report returns a daily snapshot; walk the date window
    from datetime import datetime, timedelta
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    frames = []
    cursor = start
    while cursor <= end:
        try:
            daily = ak.stock_notice_report(symbol="全部", date=cursor.strftime("%Y%m%d"))
            if daily is not None and not daily.empty:
                # Filter rows that reference this ticker
                code_col = next((c for c in daily.columns if "代码" in c), None)
                if code_col:
                    hit = daily[daily[code_col].astype(str).str.zfill(6) == symbol]
                    if not hit.empty:
                        frames.append(hit)
        except Exception as e:
            logger.warning("stock_notice_report on %s failed: %s", cursor, e)
        cursor += timedelta(days=1)

    if not frames:
        return f"## Announcements for {ticker} {start_date} → {end_date}\n\n_No filings._"

    combined = pd.concat(frames, ignore_index=True)
    return format_df_as_md(combined, f"Announcements for {ticker} {start_date} → {end_date}", max_rows=40)
