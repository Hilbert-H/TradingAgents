"""Shared helpers for the akshare vendor implementations."""

import logging
import os
import time
from contextlib import contextmanager
from functools import wraps
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


@contextmanager
def _no_proxy_env():
    """Temporarily clear HTTP(S) proxy env vars.

    Akshare's data sources are Chinese-domain endpoints (eastmoney.com,
    sina.com.cn, ...) that are reachable directly from anywhere. Cross-border
    proxies (Clash etc.) tend to fail intermittently for these hosts, so we
    bypass them for the duration of akshare calls.
    """
    saved = {k: os.environ.pop(k, None) for k in _PROXY_ENV_KEYS}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


class NotApplicableError(Exception):
    """Raised when a vendor cannot serve a ticker (e.g. akshare for US stocks).

    Distinct from regular errors so the dispatch layer can route the call
    to the next vendor (or, when no other vendor implements the method,
    surface a clean "N/A" string to the agent).
    """


A_SHARE_SUFFIXES = (".SS", ".SZ")


def is_a_share(ticker: Optional[str]) -> bool:
    if not ticker:
        return False
    return ticker.upper().endswith(A_SHARE_SUFFIXES)


def to_ak_symbol(ticker: str) -> str:
    """600487.SS -> '600487'. Most akshare endpoints take the bare 6-digit code."""
    if not is_a_share(ticker):
        raise NotApplicableError(f"{ticker!r} is not an A-share ticker")
    return ticker.split(".", 1)[0]


def to_ak_symbol_with_market(ticker: str) -> str:
    """600487.SS -> 'SH600487'. Some akshare endpoints want the exchange prefix.

    Prefix rules: 6 -> SH (Shanghai main + STAR);
                  0 / 3 -> SZ (Shenzhen main + ChiNext);
                  4 / 8 -> Beijing Stock Exchange (out of scope, raises).
    """
    code = to_ak_symbol(ticker)
    if not code or not code[0].isdigit():
        raise NotApplicableError(f"{ticker!r} has no recognisable market prefix")
    first = code[0]
    if first == "6":
        return f"SH{code}"
    if first in ("0", "3"):
        return f"SZ{code}"
    if first in ("4", "8"):
        raise NotApplicableError(
            f"{ticker!r} appears to be a Beijing Stock Exchange ticker, "
            "which is out of scope for this vendor."
        )
    raise NotApplicableError(f"{ticker!r} has an unrecognised market prefix '{first}'")


def ak_retry(max_attempts: int = 3, base_delay: float = 1.0):
    """Decorator: retry on transient errors with exponential backoff.

    `NotApplicableError` is never retried — that's a permanent classification
    error, not a transient failure.
    """
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    with _no_proxy_env():
                        return fn(*args, **kwargs)
                except NotApplicableError:
                    raise
                except Exception as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        delay = base_delay * (2 ** attempt)
                        logger.warning(
                            "akshare call %s failed (attempt %d/%d): %s; retrying in %.1fs",
                            fn.__name__, attempt + 1, max_attempts, e, delay,
                        )
                        time.sleep(delay)
            logger.error("akshare call %s exhausted retries: %s", fn.__name__, last_exc)
            raise last_exc
        return wrapper
    return deco


def format_df_as_md(df: Optional[pd.DataFrame], title: str, max_rows: int = 30) -> str:
    """Render a DataFrame as a markdown section for LLM consumption.

    Returns a "No data" message if df is None or empty. Truncates rows past max_rows.
    """
    if df is None or df.empty:
        return f"## {title}\n\n_No data available._"
    truncated = df.head(max_rows)
    try:
        body = truncated.to_markdown(index=False)
    except ImportError:
        # to_markdown needs `tabulate`; fall back to to_string
        body = truncated.to_string(index=False)
    return f"## {title}\n\n{body}"
