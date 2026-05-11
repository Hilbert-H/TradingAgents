"""Akshare implementations for market data: stock OHLCV, indicators, insider."""

import logging
from datetime import datetime

import akshare as ak
import pandas as pd

from .akshare_common import (
    NotApplicableError,
    ak_retry,
    format_df_as_md,
    is_a_share,
    to_ak_symbol,
    to_ak_symbol_with_market,
)

logger = logging.getLogger(__name__)


# Akshare returns Chinese column names by default; map to English for
# downstream consistency.
_STOCK_HIST_RENAME = {
    "日期": "Date", "开盘": "Open", "收盘": "Close",
    "最高": "High", "最低": "Low", "成交量": "Volume",
    "成交额": "Turnover", "振幅": "Amplitude",
    "涨跌幅": "ChgPct", "涨跌额": "Chg", "换手率": "TurnoverRate",
}


@ak_retry()
def get_stock_akshare(ticker: str, start_date: str, end_date: str) -> str:
    """Daily OHLCV with forward-adjusted prices for an A-share ticker."""
    symbol = to_ak_symbol(ticker)
    # akshare expects yyyymmdd for these args
    start_compact = start_date.replace("-", "")
    end_compact = end_date.replace("-", "")
    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily",
        start_date=start_compact, end_date=end_compact,
        adjust="qfq",
    )
    if df is not None and not df.empty:
        df = df.rename(columns=_STOCK_HIST_RENAME)
    return format_df_as_md(df, f"{ticker} OHLCV {start_date} → {end_date}", max_rows=60)


def get_indicator_akshare(*_a, **_k):
    raise NotImplementedError("akshare_market.get_indicator_akshare — Task 6")


def get_insider_transactions_akshare(*_a, **_k):
    raise NotImplementedError("akshare_market.get_insider_transactions_akshare — Task 7")
