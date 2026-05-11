"""Akshare implementations for A-share capital-flow signals."""

import logging
from datetime import datetime, timedelta
import akshare as ak
import pandas as pd

from .akshare_common import (
    ak_retry, format_df_as_md, to_ak_symbol, to_ak_symbol_with_market,
)

logger = logging.getLogger(__name__)


def _date_range(curr_date: str, look_back_days: int):
    end = datetime.strptime(curr_date, "%Y-%m-%d")
    start = end - timedelta(days=look_back_days)
    return start, end


@ak_retry()
def get_lhb_detail_akshare(ticker: str, curr_date: str, look_back_days: int = 5) -> str:
    """Dragon-Tiger seat detail for a single ticker over a recent window."""
    symbol = to_ak_symbol(ticker)
    start, end = _date_range(curr_date, look_back_days)
    frames = []
    cursor = start
    while cursor <= end:
        try:
            daily = ak.stock_lhb_stock_detail_em(symbol=symbol, date=cursor.strftime("%Y%m%d"))
            if daily is not None and not daily.empty:
                daily = daily.assign(_dt=cursor.strftime("%Y-%m-%d"))
                frames.append(daily)
        except Exception as e:
            logger.debug("no LHB data for %s on %s: %s", symbol, cursor, e)
        cursor += timedelta(days=1)

    if not frames:
        return f"## Dragon-Tiger seats for {ticker} (last {look_back_days}d)\n\n_No 龙虎榜 hits._"
    combined = pd.concat(frames, ignore_index=True)
    return format_df_as_md(combined, f"Dragon-Tiger seats for {ticker} (last {look_back_days}d)", max_rows=30)


@ak_retry()
def get_lhb_institutional_akshare(ticker: str, curr_date: str, look_back_days: int = 10) -> str:
    """Institutional-seat-only Dragon-Tiger flow for a ticker over a recent window."""
    symbol = to_ak_symbol(ticker)
    start, end = _date_range(curr_date, look_back_days)
    try:
        df = ak.stock_lhb_jgmmtj_em(start_date=start.strftime("%Y%m%d"),
                                     end_date=end.strftime("%Y%m%d"))
    except Exception as e:
        logger.warning("stock_lhb_jgmmtj_em failed: %s", e)
        return f"## Institutional LHB for {ticker}\n\n_Source unavailable: {e}_"
    if df is not None and not df.empty:
        code_col = next((c for c in df.columns if "代码" in c), None)
        if code_col:
            df = df[df[code_col].astype(str).str.zfill(6) == symbol]
    return format_df_as_md(df, f"Institutional LHB for {ticker} (last {look_back_days}d)", max_rows=20)


def get_north_capital_individual_akshare(*_a, **_k):
    raise NotImplementedError("Task 20")


def get_north_capital_overall_akshare(*_a, **_k):
    raise NotImplementedError("Task 21")


def get_margin_trading_akshare(*_a, **_k):
    raise NotImplementedError("Task 22")


def get_fund_flow_akshare(*_a, **_k):
    raise NotImplementedError("Task 23")
