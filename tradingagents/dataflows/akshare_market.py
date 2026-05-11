"""Akshare implementations for market data: stock OHLCV, indicators, insider."""

import logging
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd
from stockstats import wrap as _stockstats_wrap

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


@ak_retry()
def get_indicator_akshare(
    ticker: str, indicator: str, curr_date: str, look_back_days: int = 30,
) -> str:
    """Compute a technical indicator over a recent window from akshare daily data."""
    symbol = to_ak_symbol(ticker)
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    # Pull enough history for the indicator to stabilise (e.g. 50 SMA needs >=50 rows)
    buffer_days = max(look_back_days, 200)
    start_dt = end_dt - timedelta(days=buffer_days + look_back_days)

    df = ak.stock_zh_a_hist(
        symbol=symbol, period="daily",
        start_date=start_dt.strftime("%Y%m%d"),
        end_date=end_dt.strftime("%Y%m%d"),
        adjust="qfq",
    )
    if df is None or df.empty:
        return f"## {ticker} {indicator}\n\n_No data available._"

    df = df.rename(columns=_STOCK_HIST_RENAME)
    # stockstats requires lowercase column names
    df.columns = [c.lower() for c in df.columns]

    sdf = _stockstats_wrap(df)
    sdf[indicator]  # trigger computation
    # stockstats.wrap promotes the date column to the index
    result = sdf[[indicator]].tail(look_back_days).copy()
    result.index = pd.to_datetime(result.index).strftime("%Y-%m-%d")
    result.index.name = "date"
    return format_df_as_md(result.reset_index(), f"{ticker} {indicator} (last {look_back_days} days)", max_rows=look_back_days)


def get_insider_transactions_akshare(*_a, **_k):
    raise NotImplementedError("akshare_market.get_insider_transactions_akshare — Task 7")
