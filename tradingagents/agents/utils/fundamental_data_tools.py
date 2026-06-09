from langchain_core.tools import tool
from typing import Annotated
from tradingagents.dataflows.interface import route_to_vendor


@tool
def get_fundamentals(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"],
) -> str:
    """
    Retrieve comprehensive fundamental data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing comprehensive fundamental data
    """
    return route_to_vendor("get_fundamentals", ticker, curr_date)


@tool
def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve balance sheet data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing balance sheet data
    """
    return route_to_vendor("get_balance_sheet", ticker, freq, curr_date)


@tool
def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve cash flow statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing cash flow statement data
    """
    return route_to_vendor("get_cashflow", ticker, freq, curr_date)


@tool
def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve income statement data for a given ticker symbol.
    Uses the configured fundamental_data vendor.
    Args:
        ticker (str): Ticker symbol of the company
        freq (str): Reporting frequency: annual/quarterly (default quarterly)
        curr_date (str): Current date you are trading at, yyyy-mm-dd
    Returns:
        str: A formatted report containing income statement data
    """
    return route_to_vendor("get_income_statement", ticker, freq, curr_date)


# ─── A-share comprehensive additions (THS-backed) ───────────────────────────

@tool
def get_financial_indicators(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve the full 86-indicator financial-analysis series for an A-share
    ticker (since 2018), covering EPS / ROE / ROA / gross margin / current
    ratio / asset-liability ratio / turnover ratios etc.

    Use this when the four basic statements (balance sheet / income / cashflow
    / fundamentals) don't expose the derived ratio you need.
    Args:
        ticker (str): A-share ticker (e.g. "600031.SH")
        curr_date (str): unused; accepted for tool-uniformity
    Returns:
        str: Markdown table of all reporting periods × 86 indicator columns.
    """
    return route_to_vendor("get_financial_indicators", ticker, curr_date)


@tool
def get_revenue_breakdown(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve revenue-breakdown (主营构成) for an A-share ticker — splits revenue
    by 行业 / 产品 / 地区 with corresponding revenue share, cost share, and
    gross margin per segment, across multiple reporting periods.

    Use when analysing concentration / mix-shift in the business.
    Args:
        ticker (str): A-share ticker
        curr_date (str): unused; accepted for uniformity
    Returns:
        str: Markdown table.
    """
    return route_to_vendor("get_revenue_breakdown", ticker, curr_date)


@tool
def get_dividend_history(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve dividend / bonus-share plan history (Tonghuashun) for an A-share
    ticker, including 董事会日 / 股权登记日 / 除权除息日 / 分红总额 / 实施日.

    Use to assess shareholder-return track record and timing.
    Args:
        ticker (str): A-share ticker
        curr_date (str): unused
    Returns:
        str: Markdown table.
    """
    return route_to_vendor("get_dividend_history", ticker, curr_date)


@tool
def get_profit_forecast(
    ticker: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """
    Retrieve analyst-consensus profit forecast (Tonghuashun) for an A-share
    ticker, showing number of forecasting brokers, min/mean/max projected EPS
    for next 2-3 years, and the industry-average EPS for comparison.

    Use as a quick consensus EPS anchor when building fundamental theses.
    Args:
        ticker (str): A-share ticker
        curr_date (str): unused
    Returns:
        str: Markdown table.
    """
    return route_to_vendor("get_profit_forecast", ticker, curr_date)