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


# ─── A-share comprehensive additions (THS-backed) ───────────────────────────

@tool
def get_top10_holders(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Top-10 holders (十大股东) snapshot at the latest disclosed quarter-end
    for an A-share. Columns: 名次 / 股东名称 / 股份类型 / 持股数 / 占总股本
    比例 / 增减. Use to understand control structure and recent positioning
    by major holders.
    """
    return route_to_vendor("get_top10_holders", ticker, curr_date)


@tool
def get_top10_free_holders(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Top-10 free-circulating holders (十大流通股东) snapshot — same shape
    as get_top10_holders but only counts non-restricted shares, which is
    what drives liquid float and tradable supply. Compare with the total-
    holders list to find lock-up dominance.
    """
    return route_to_vendor("get_top10_free_holders", ticker, curr_date)


@tool
def get_concerted_action(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Concerted-action group (一致行动人) for an A-share — the controller
    and their named acting-in-concert parties, with combined holding %.
    High concentration here = strong control / takeover-defense posture.
    """
    return route_to_vendor("get_concerted_action", ticker, curr_date)


@tool
def get_block_trade(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Block-trade (大宗交易) detail over the last ~90 days for an A-share.
    Columns include 成交价 / 折溢价率 / 买方营业部 / 卖方营业部 / 成交量 /
    成交额. Use to identify institutional repositioning that doesn't show
    in regular price/volume.
    """
    return route_to_vendor("get_block_trade", ticker, curr_date)


@tool
def get_shareholder_change(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Large-shareholder transaction announcements (大股东增减持) for an
    A-share — historical record of which significant holder transacted
    how many shares at what price. Use as a directional signal from people
    with the best informational edge.
    """
    return route_to_vendor("get_shareholder_change", ticker, curr_date)


@tool
def get_management_change(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Management share-transaction history (高管增减持) for an A-share.
    Insider-equivalent transactions for the A-share market.
    """
    return route_to_vendor("get_management_change", ticker, curr_date)
