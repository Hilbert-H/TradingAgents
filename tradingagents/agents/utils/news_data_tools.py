from langchain_core.tools import tool
from typing import Annotated, Optional
from tradingagents.dataflows.interface import route_to_vendor

@tool
def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """
    Retrieve news data for a given ticker symbol.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol
        start_date (str): Start date in yyyy-mm-dd format
        end_date (str): End date in yyyy-mm-dd format
    Returns:
        str: A formatted string containing news data
    """
    return route_to_vendor("get_news", ticker, start_date, end_date)

@tool
def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[Optional[int], "Days to look back; omit to use the configured default"] = None,
    limit: Annotated[Optional[int], "Max articles to return; omit to use the configured default"] = None,
) -> str:
    """
    Retrieve global news data.
    Uses the configured news_data vendor. Defaults for look_back_days and
    limit come from DEFAULT_CONFIG (global_news_lookback_days,
    global_news_article_limit); pass explicit values to override.

    Args:
        curr_date (str): Current date in yyyy-mm-dd format
        look_back_days (int): Number of days to look back; omit to inherit config
        limit (int): Maximum number of articles to return; omit to inherit config

    Returns:
        str: A formatted string containing global news data
    """
    return route_to_vendor("get_global_news", curr_date, look_back_days, limit)

@tool
def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """
    Retrieve insider transaction information about a company.
    Uses the configured news_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
    Returns:
        str: A report of insider transaction data
    """
    return route_to_vendor("get_insider_transactions", ticker)


@tool
def get_announcements(
    ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
    start_date: Annotated[str, "Start date yyyy-mm-dd"],
    end_date: Annotated[str, "End date yyyy-mm-dd"],
) -> str:
    """A-share legal-disclosure announcements (法定信披) for a ticker.

    Returns a formatted markdown list. Only supports A-share tickers
    (`.SS` / `.SZ`). Calling with a US ticker returns an N/A string.
    """
    return route_to_vendor("get_announcements", ticker, start_date, end_date)


@tool
def get_stock_hot_rank(
    ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"],
) -> str:
    """Attention rank for an A-share ticker (East-Money + Tonghuashun).

    Use this as a market-attention proxy for sentiment analysis on A-shares.
    Non-A-share tickers return an N/A string.
    """
    return route_to_vendor("get_stock_hot_rank", ticker, curr_date)


@tool
def get_shareholder_count(
    ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"],
) -> str:
    """Historical shareholder count for an A-share — chip-concentration proxy.

    A falling count typically indicates institutional accumulation;
    a rising count indicates retail dispersion. Non-A-share returns N/A.
    """
    return route_to_vendor("get_shareholder_count", ticker, curr_date)


@tool
def get_research_reports(
    ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
    start_date: Annotated[str, "Start date yyyy-mm-dd"],
    end_date: Annotated[str, "End date yyyy-mm-dd"],
) -> str:
    """Analyst research reports (target prices, ratings) for an A-share.

    Use as auxiliary sentiment signal. Non-A-share returns N/A.
    """
    return route_to_vendor("get_research_reports", ticker, start_date, end_date)


# ─── A-share comprehensive additions (THS-backed) ───────────────────────────

@tool
def get_concept_tags(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Concept / theme tags with heat scores for an A-share ticker.

    Returns the top concepts the stock is filed under (e.g. 工程机械概念,
    一带一路, 军民融合) sorted by East-Money heat score. Use as a thematic-
    rotation / narrative-driver signal in sentiment analysis.
    """
    return route_to_vendor("get_concept_tags", ticker, curr_date)


@tool
def get_stock_comment(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """East-Money's 千股千评 row for an A-share ticker.

    Includes 综合得分 (composite score), 主力成本 (main-force cost basis),
    机构参与度 (institutional participation %), 关注指数, and 目前排名
    (current sentiment-rank within full A-share universe). Snapshot — one
    row, very high information density.
    """
    return route_to_vendor("get_stock_comment", ticker, curr_date)


@tool
def get_xueqiu_hot(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Xueqiu (snowball) retail-investor heat for an A-share ticker.

    Returns three concatenated tables: 关注 (followers), 讨论 (tweets), and
    成交 (trades) — the stock's current position in each of Xueqiu's three
    "hottest" leaderboards. Strong retail-attention proxy.
    """
    return route_to_vendor("get_xueqiu_hot", ticker, curr_date)


@tool
def get_investor_qa(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Investor Q&A from 上证 e 互动 (SH) or 巨潮互动易 (SZ) for an A-share.

    Each row is a public investor question with the official company answer.
    Reveals what current shareholders actually care about (orders, capex,
    tariffs, etc.) — qualitative complement to financial filings.
    """
    return route_to_vendor("get_investor_qa", ticker, curr_date)


@tool
def get_performance_briefing(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Earnings-conference (业绩说明会) schedule for an A-share.

    Returns first-scheduled date and any reschedules per reporting period.
    Use for event-driven calendar windows.
    """
    return route_to_vendor("get_performance_briefing", ticker, curr_date)


@tool
def get_restricted_release(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Restricted-share unlock (限售解禁) calendar for an A-share.

    Each row: unlock date, # of holders unlocking, share count, value, %
    of total market cap. Use for supply-side overhang forecasting.
    """
    return route_to_vendor("get_restricted_release", ticker, curr_date)


@tool
def get_pledge_ratio(
    ticker: Annotated[str, "Ticker symbol (e.g. 600031.SH)"],
    curr_date: Annotated[str, "Current date yyyy-mm-dd"] = None,
) -> str:
    """Share-pledge ratio (股权质押) snapshot for an A-share.

    Returns latest weekly snapshot: pledged share count, pledged ratio
    (% of total shares pledged). Use as a leverage / control-risk factor.
    """
    return route_to_vendor("get_pledge_ratio", ticker, curr_date)
