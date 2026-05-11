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
    """Dragon-Tiger seat detail for a single ticker over a recent window (both buy + sell sides)."""
    symbol = to_ak_symbol(ticker)
    start, end = _date_range(curr_date, look_back_days)
    frames = []
    cursor = start
    while cursor <= end:
        date_str = cursor.strftime("%Y%m%d")
        for flag in ("买入", "卖出"):
            try:
                daily = ak.stock_lhb_stock_detail_em(symbol=symbol, date=date_str, flag=flag)
                if daily is not None and not daily.empty:
                    daily = daily.assign(_dt=cursor.strftime("%Y-%m-%d"), _side=flag)
                    frames.append(daily)
            except Exception as e:
                logger.debug("no LHB %s data for %s on %s: %s", flag, symbol, cursor, e)
        cursor += timedelta(days=1)

    if not frames:
        return f"## Dragon-Tiger seats for {ticker} (last {look_back_days}d)\n\n_No 龙虎榜 hits._"
    combined = pd.concat(frames, ignore_index=True)
    return format_df_as_md(combined, f"Dragon-Tiger seats for {ticker} (last {look_back_days}d)", max_rows=40)


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


@ak_retry()
def get_north_capital_individual_akshare(ticker: str, curr_date: str, look_back_days: int = 10) -> str:
    """Northbound (Stock Connect) holding changes for a ticker."""
    symbol = to_ak_symbol(ticker)  # akshare expects unprefixed 6-digit code, e.g. "600487"
    try:
        df = ak.stock_hsgt_individual_em(symbol=symbol)
    except Exception as e:
        logger.warning("stock_hsgt_individual_em failed for %s: %s", symbol, e)
        return f"## Northbound holdings for {ticker}\n\n_Source unavailable: {e}_"
    if df is not None and not df.empty:
        date_col = next((c for c in df.columns if "日期" in c), None)
        if date_col:
            df["_dt"] = pd.to_datetime(df[date_col], errors="coerce")
            start, end = _date_range(curr_date, look_back_days)
            df = df[(df["_dt"] >= pd.Timestamp(start)) & (df["_dt"] <= pd.Timestamp(end))].drop(columns=["_dt"])
    return format_df_as_md(df, f"Northbound holdings for {ticker} (last {look_back_days}d)", max_rows=20)


@ak_retry()
def get_north_capital_overall_akshare(curr_date: str, look_back_days: int = 10) -> str:
    """Daily net inflow of northbound capital — market-wide mood proxy."""
    try:
        # stock_hsgt_north_net_flow_in_em was removed; use stock_hsgt_hist_em instead
        df = ak.stock_hsgt_hist_em(symbol="北向资金")
    except Exception as e:
        logger.warning("stock_hsgt_hist_em failed: %s", e)
        return f"## Northbound overall flow\n\n_Source unavailable: {e}_"
    if df is not None and not df.empty:
        date_col = next((c for c in df.columns if "日期" in c), None)
        if date_col:
            df["_dt"] = pd.to_datetime(df[date_col], errors="coerce")
            start, end = _date_range(curr_date, look_back_days)
            df = df[(df["_dt"] >= pd.Timestamp(start)) & (df["_dt"] <= pd.Timestamp(end))].drop(columns=["_dt"])
    return format_df_as_md(df, f"Northbound net flow (last {look_back_days}d)", max_rows=look_back_days)


@ak_retry()
def get_margin_trading_akshare(ticker: str, curr_date: str, look_back_days: int = 10) -> str:
    """Per-ticker margin (融资) + securities-lending (融券) balances over a window."""
    symbol = to_ak_symbol(ticker)
    market_symbol = to_ak_symbol_with_market(ticker)
    start, end = _date_range(curr_date, look_back_days)
    # Both SSE and SZSE endpoints accept a single date and return all stocks;
    # walk the window, fetch each trading day, filter by symbol.
    fetch_fn = ak.stock_margin_detail_sse if market_symbol.startswith("SH") else ak.stock_margin_detail_szse
    frames = []
    cursor = start
    while cursor <= end:
        try:
            daily = fetch_fn(date=cursor.strftime("%Y%m%d"))
            if daily is not None and not daily.empty:
                code_col = next((c for c in daily.columns if "代码" in c), None)
                if code_col:
                    daily = daily[daily[code_col].astype(str).str.zfill(6) == symbol]
                if not daily.empty:
                    frames.append(daily)
        except Exception as e:
            logger.debug("margin fetch failed for %s on %s: %s", symbol, cursor, e)
        cursor += timedelta(days=1)
    if not frames:
        return f"## Margin trading for {ticker} (last {look_back_days}d)\n\n_No data available._"
    df = pd.concat(frames, ignore_index=True)
    return format_df_as_md(df, f"Margin trading for {ticker} (last {look_back_days}d)", max_rows=look_back_days)


@ak_retry()
def get_fund_flow_akshare(ticker: str, curr_date: str) -> str:
    """Today's smart-money flow breakdown (super-large / large / medium / small)."""
    symbol = to_ak_symbol(ticker)
    market_symbol = to_ak_symbol_with_market(ticker)
    market = "sh" if market_symbol.startswith("SH") else "sz"
    try:
        df = ak.stock_individual_fund_flow(stock=symbol, market=market)
    except Exception as e:
        logger.warning("stock_individual_fund_flow failed for %s: %s", symbol, e)
        return f"## Smart-money flow for {ticker}\n\n_Source unavailable: {e}_"
    # akshare returns ascending order (oldest first); take tail to get the most-recent rows
    if df is not None and not df.empty:
        df = df.tail(10).iloc[::-1].reset_index(drop=True)
    return format_df_as_md(df, f"Smart-money flow for {ticker} (as of {curr_date})", max_rows=10)
