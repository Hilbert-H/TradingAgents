"""Tool wrappers for the Capital Flow Analyst (A-share-specific)."""

from langchain_core.tools import tool
from typing import Annotated

from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_lhb_detail(
    ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"],
    look_back_days: Annotated[int, "Days to look back"] = 5,
) -> str:
    """Dragon-Tiger List (龙虎榜) seat-level buy/sell detail for an A-share."""
    return route_to_vendor("get_lhb_detail", ticker, curr_date, look_back_days)


@tool
def get_lhb_institutional(
    ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"],
    look_back_days: Annotated[int, "Days to look back"] = 10,
) -> str:
    """Dragon-Tiger institutional-seat net flow over a recent window."""
    return route_to_vendor("get_lhb_institutional", ticker, curr_date, look_back_days)


@tool
def get_north_capital_individual(
    ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"],
    look_back_days: Annotated[int, "Days to look back"] = 10,
) -> str:
    """Northbound (Stock Connect / 北上资金) holding changes for one A-share."""
    return route_to_vendor("get_north_capital_individual", ticker, curr_date, look_back_days)


@tool
def get_north_capital_overall(
    curr_date: Annotated[str, "Current date yyyy-mm-dd"],
    look_back_days: Annotated[int, "Days to look back"] = 10,
) -> str:
    """Daily net inflow of northbound capital — market-wide mood proxy."""
    return route_to_vendor("get_north_capital_overall", curr_date, look_back_days)


@tool
def get_margin_trading(
    ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"],
    look_back_days: Annotated[int, "Days to look back"] = 10,
) -> str:
    """Per-ticker margin balance (融资) and securities lending (融券) over a window."""
    return route_to_vendor("get_margin_trading", ticker, curr_date, look_back_days)


@tool
def get_fund_flow(
    ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"],
) -> str:
    """Smart-money flow breakdown (super-large/large/medium/small orders)."""
    return route_to_vendor("get_fund_flow", ticker, curr_date)
