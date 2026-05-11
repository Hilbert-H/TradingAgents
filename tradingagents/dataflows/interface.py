from typing import Annotated
import logging

# Import from vendor-specific modules
from .y_finance import (
    get_YFin_data_online,
    get_stock_stats_indicators_window,
    get_fundamentals as get_yfinance_fundamentals,
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
)
from .yfinance_news import get_news_yfinance, get_global_news_yfinance
from .alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
    get_indicator as get_alpha_vantage_indicator,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_income_statement as get_alpha_vantage_income_statement,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_global_news as get_alpha_vantage_global_news,
)
from .alpha_vantage_common import AlphaVantageRateLimitError
from .akshare_common import NotApplicableError

# Configuration and routing logic
from .config import get_config

logger = logging.getLogger(__name__)

A_SHARE_SUFFIXES = (".SS", ".SZ")


def _detect_market(ticker) -> str:
    """Return 'a_share' if ticker has Shanghai/Shenzhen suffix, else 'global'."""
    if not ticker or not isinstance(ticker, str):
        return "global"
    return "a_share" if ticker.upper().endswith(A_SHARE_SUFFIXES) else "global"

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    }
}

VENDOR_LIST = [
    "yfinance",
    "alpha_vantage",
]

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
    },
    # technical_indicators
    "get_indicators": {
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
    },
    # fundamental_data
    "get_fundamentals": {
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
    },
    "get_balance_sheet": {
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
    },
    "get_cashflow": {
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
    },
    "get_income_statement": {
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
    },
    # news_data
    "get_news": {
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
    },
    "get_global_news": {
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
    },
    "get_insider_transactions": {
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str = None) -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")

def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor with fallback support.

    Resolution order:
      1. If the first positional arg / `ticker` / `symbol` kwarg looks like
         an A-share ticker (`.SS` / `.SZ` suffix), force akshare as the
         primary vendor — unless the user has set a method-level
         `tool_vendors` override, which always wins.
      2. Otherwise, use the user-configured vendor for the category.
      3. Build a fallback chain: primary + every other available vendor.
      4. Walk the chain. Skip on AlphaVantageRateLimitError (existing
         behaviour). Skip on NotApplicableError (new). Skip on any other
         Exception with a warning log.
      5. If chain exhausted and every failure was NotApplicableError ->
         return an "N/A: ..." string.
      6. If chain exhausted with at least one real error -> return a
         "Data unavailable: ..." string.
    """
    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    category = get_category_for_method(method)
    config = get_config()

    # Determine ticker (used both for A-share routing and for the N/A message)
    ticker = args[0] if args else (kwargs.get("ticker") or kwargs.get("symbol"))
    market = _detect_market(ticker)

    tool_override = config.get("tool_vendors", {}).get(method)
    if tool_override:
        primary_vendors = [tool_override]
    elif market == "a_share":
        primary_vendors = ["akshare"]
        logger.info("Ticker %s detected as A-share, routing %s to akshare", ticker, method)
    else:
        vendor_config = config.get("data_vendors", {}).get(category, "default")
        primary_vendors = [v.strip() for v in vendor_config.split(",")]

    # Build fallback chain
    all_available = list(VENDOR_METHODS[method].keys())
    fallback_vendors = list(primary_vendors)
    for vendor in all_available:
        if vendor not in fallback_vendors:
            fallback_vendors.append(vendor)

    seen_only_not_applicable = True
    last_error: Exception = None

    for vendor in fallback_vendors:
        if vendor not in VENDOR_METHODS[method]:
            continue
        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl
        try:
            return impl_func(*args, **kwargs)
        except AlphaVantageRateLimitError as e:
            seen_only_not_applicable = False
            last_error = e
            continue
        except NotApplicableError as e:
            last_error = e
            continue
        except Exception as e:
            seen_only_not_applicable = False
            last_error = e
            logger.warning("vendor %s failed for method %s: %s", vendor, method, e)
            continue

    # Chain exhausted
    if seen_only_not_applicable:
        return (
            f"N/A: {method} is not supported for ticker {ticker!r}. "
            f"(All available vendors raised NotApplicableError; "
            f"this method typically requires an A-share ticker.)"
        )
    return f"Data unavailable: {method} failed across all vendors. Last error: {last_error}"