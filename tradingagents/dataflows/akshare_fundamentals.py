"""Akshare implementations for fundamentals: summary + 3 financial statements."""

import logging
import pandas as pd
import akshare as ak

from .akshare_common import (
    ak_retry, format_df_as_md, to_ak_symbol, to_ak_symbol_with_market,
)

logger = logging.getLogger(__name__)


def _select_periods(df: pd.DataFrame, period_col: str) -> pd.DataFrame:
    """Keep 5 most-recent annual reports + 4 most-recent quarterly reports.

    Annual reports end with '1231'; quarterly with '0331', '0630', '0930'.
    """
    if df is None or df.empty or period_col not in df.columns:
        return df
    periods = pd.to_datetime(df[period_col], errors="coerce")
    df = df.assign(_period=periods).sort_values("_period", ascending=False)
    annual = df[df["_period"].dt.strftime("%m%d") == "1231"].head(5)
    quarterly = df[df["_period"].dt.strftime("%m%d") != "1231"].head(4)
    result = pd.concat([annual, quarterly]).sort_values("_period", ascending=False).drop(columns=["_period"])
    return result


@ak_retry()
def get_fundamentals_akshare(ticker: str, curr_date: str) -> str:
    """High-level fundamentals snapshot from 同花顺."""
    symbol = to_ak_symbol(ticker)
    try:
        df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期")
    except Exception as e:
        logger.warning("stock_financial_abstract_ths failed for %s: %s", symbol, e)
        return f"## Fundamentals for {ticker}\n\n_Source unavailable: {e}_"

    # Pick the period column (column name varies by version)
    period_col = next((c for c in df.columns if "报告" in c or "period" in c.lower()), None)
    if period_col:
        df = _select_periods(df, period_col)
    return format_df_as_md(df, f"Fundamentals summary for {ticker} (as of {curr_date})", max_rows=20)


@ak_retry()
def get_balance_sheet_akshare(ticker: str, curr_date: str) -> str:
    market_symbol = to_ak_symbol_with_market(ticker)
    try:
        df = ak.stock_balance_sheet_by_report_em(symbol=market_symbol)
    except Exception as e:
        logger.warning("stock_balance_sheet_by_report_em failed for %s: %s", market_symbol, e)
        return f"## Balance sheet for {ticker}\n\n_Source unavailable: {e}_"
    period_col = next((c for c in df.columns if "报告" in c or "REPORT" in c.upper()), None)
    if period_col:
        df = _select_periods(df, period_col)
    return format_df_as_md(df, f"Balance sheet for {ticker} (as of {curr_date})", max_rows=20)


@ak_retry()
def get_cashflow_akshare(ticker: str, curr_date: str) -> str:
    market_symbol = to_ak_symbol_with_market(ticker)
    try:
        df = ak.stock_cash_flow_sheet_by_report_em(symbol=market_symbol)
    except Exception as e:
        logger.warning("stock_cash_flow_sheet_by_report_em failed for %s: %s", market_symbol, e)
        return f"## Cash flow for {ticker}\n\n_Source unavailable: {e}_"
    period_col = next((c for c in df.columns if "报告" in c or "REPORT" in c.upper()), None)
    if period_col:
        df = _select_periods(df, period_col)
    return format_df_as_md(df, f"Cash flow for {ticker} (as of {curr_date})", max_rows=20)


@ak_retry()
def get_income_statement_akshare(ticker: str, curr_date: str) -> str:
    market_symbol = to_ak_symbol_with_market(ticker)
    try:
        df = ak.stock_profit_sheet_by_report_em(symbol=market_symbol)
    except Exception as e:
        logger.warning("stock_profit_sheet_by_report_em failed for %s: %s", market_symbol, e)
        return f"## Income statement for {ticker}\n\n_Source unavailable: {e}_"
    period_col = next((c for c in df.columns if "报告" in c or "REPORT" in c.upper()), None)
    if period_col:
        df = _select_periods(df, period_col)
    return format_df_as_md(df, f"Income statement for {ticker} (as of {curr_date})", max_rows=20)
