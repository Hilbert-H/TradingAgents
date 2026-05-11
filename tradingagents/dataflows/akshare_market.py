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


# Column rename for ak.stock_zh_a_daily (Sina endpoint, already English lowercase)
_STOCK_DAILY_RENAME = {
    "date": "Date", "open": "Open", "close": "Close",
    "high": "High", "low": "Low", "volume": "Volume",
    "amount": "Turnover", "outstanding_share": "OutstandingShares",
    "turnover": "TurnoverRate",
}


@ak_retry()
def get_stock_akshare(ticker: str, start_date: str, end_date: str) -> str:
    """Daily OHLCV with forward-adjusted prices for an A-share ticker.

    Uses Sina's daily endpoint (via ak.stock_zh_a_daily) rather than the
    EastMoney push2his endpoint, because push2his is unreachable from
    some networks. Sina provides equivalent data with similar columns.
    """
    sina_symbol = to_ak_symbol_with_market(ticker).lower()  # e.g. "sh600487"
    start_compact = start_date.replace("-", "")
    end_compact = end_date.replace("-", "")
    df = ak.stock_zh_a_daily(
        symbol=sina_symbol,
        adjust="qfq",
        start_date=start_compact,
        end_date=end_compact,
    )
    if df is not None and not df.empty:
        df = df.rename(columns=_STOCK_DAILY_RENAME)
    return format_df_as_md(df, f"{ticker} OHLCV {start_date} → {end_date}", max_rows=60)


@ak_retry()
def get_indicator_akshare(
    ticker: str, indicator: str, curr_date: str, look_back_days: int = 30,
) -> str:
    """Compute a technical indicator over a recent window from akshare daily data (via Sina)."""
    sina_symbol = to_ak_symbol_with_market(ticker).lower()  # e.g. "sh600487"
    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    # Pull enough history for the indicator to stabilise (e.g. 50 SMA needs >=50 rows)
    buffer_days = max(look_back_days, 200)
    start_dt = end_dt - timedelta(days=buffer_days + look_back_days)

    df = ak.stock_zh_a_daily(
        symbol=sina_symbol,
        adjust="qfq",
        start_date=start_dt.strftime("%Y%m%d"),
        end_date=end_dt.strftime("%Y%m%d"),
    )
    if df is None or df.empty:
        return f"## {ticker} {indicator}\n\n_No data available._"

    # stockstats requires lowercase column names; Sina already uses lowercase
    df.columns = [c.lower() for c in df.columns]

    sdf = _stockstats_wrap(df)
    sdf[indicator]  # trigger computation
    # stockstats.wrap promotes the date column to the index
    result = sdf[[indicator]].tail(look_back_days).copy()
    result.index = pd.to_datetime(result.index).strftime("%Y-%m-%d")
    result.index.name = "date"
    return format_df_as_md(result.reset_index(), f"{ticker} {indicator} (last {look_back_days} days)", max_rows=look_back_days)


@ak_retry()
def get_insider_transactions_akshare(ticker: str, curr_date: str = None) -> str:
    """Combined executive + 5%+ shareholder transactions for an A-share."""
    symbol = to_ak_symbol(ticker)
    market_symbol = to_ak_symbol_with_market(ticker)
    date_suffix = f" (as of {curr_date})" if curr_date else ""

    sections = []

    # 1) Executive transactions (高管增减持)
    try:
        execs = ak.stock_ggcg_em(symbol=symbol)
        sections.append(format_df_as_md(execs, "Executive (高管) Transactions", max_rows=30))
    except Exception as e:
        logger.warning("stock_ggcg_em failed for %s: %s", symbol, e)
        sections.append("## Executive (高管) Transactions\n\n_Source unavailable._")

    # 2) Major shareholder (5%+) transactions — endpoint depends on exchange
    try:
        if market_symbol.startswith("SH"):
            major = ak.stock_share_hold_change_sse(symbol=symbol)
        else:
            major = ak.stock_share_hold_change_szse(symbol=symbol)
        sections.append(format_df_as_md(major, "Major Shareholder (>=5%) Transactions", max_rows=30))
    except Exception as e:
        logger.warning("stock_share_hold_change failed for %s: %s", market_symbol, e)
        sections.append("## Major Shareholder Transactions\n\n_Source unavailable._")

    return f"# Insider transactions for {ticker}{date_suffix}\n\n" + "\n\n".join(sections)
