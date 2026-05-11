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


def get_global_news_akshare(*_a, **_k):
    raise NotImplementedError("akshare_news.get_global_news_akshare — Task 9")


def get_announcements_akshare(*_a, **_k):
    raise NotImplementedError("akshare_news.get_announcements_akshare — Task 10")
