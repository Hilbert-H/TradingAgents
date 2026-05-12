"""Shared TuShare Pro infrastructure: client construction, retry, ticker / date helpers.

This module mirrors the conventions used by the sister project
``quant-research-v2`` (`scripts/data/pull_tushare_data.py`) so the two
codebases share the same error-classification + retry semantics:

- Token resolution: ``TUSHARE_TOKEN`` env var first; if absent, attempt to
  source ``~/.claude/quant-research-v2/secrets.env`` and re-check.
- Fatal vs transient: permission / token / 积分不够 errors are *not* retried
  so we fail fast (and the routing layer can fall back to akshare). Network
  / 5xx / rate-limit jitter is retried with exponential backoff.
- Process-level Pro client + per-call retry decorator. The client is
  constructed once per process (workers in `ProcessPoolExecutor` each get
  their own); the `@tushare_retry()` decorator wraps individual API calls.

Ticker / date format
~~~~~~~~~~~~~~~~~~~~
TuShare uses the same suffix convention as the rest of this codebase
(`600487.SH` / `000001.SZ`) *except* it never accepts `.SS`; we coerce
`.SS` → `.SH`. Dates are `YYYYMMDD` for TuShare vs `YYYY-MM-DD` here.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)


# === DATA SCHEMA / 行为约定 ===
#
# TUSHARE_TOKEN : str
#     来源优先级:
#       1) 进程 env var ``TUSHARE_TOKEN``
#       2) ``~/.claude/quant-research-v2/secrets.env`` 里的 ``TUSHARE_TOKEN=...``
#          —— 用户的 quant-research-v2 项目把 token 放在这里
#     仍未拿到 → :func:`is_tushare_available` 返回 False, 上层路由跳到 akshare。
#
# _PRO_CLIENT : tushare.pro.client.DataApi | None
#     进程级单例。多线程下用 _PRO_LOCK 保护初始化, 避免并发 set_token 竞争。
#
# _FATAL_KEYWORDS : tuple[str, ...]
#     出现在异常消息里就视为不可重试 (token 失效 / 权限不足 / 积分不够)。
#     永久错误立刻 raise, 让 interface.route_to_vendor 跳到下一个 vendor。
#
# _MAX_RETRIES, _BASE_SLEEP : int / float
#     瞬态错误 (网络抖动 / 5xx / 限流) 的指数退避参数。和 quant-research-v2 对齐。


# 永久错误关键词（出现即不重试）
_FATAL_KEYWORDS = (
    "token",
    "permission",
    "403",
    "401",
    "unauth",
    "没有访问该接口的权限",
    "积分不够",
    "积分不足",
)

# 瞬态错误重试参数
_MAX_RETRIES = 3
_BASE_SLEEP = 2.0

# 进程级 Pro client 缓存
_PRO_CLIENT = None
_PRO_LOCK = threading.Lock()
_TOKEN_LOADED_ONCE = False

# 用户 secrets 文件的兜底位置（quant-research-v2 的约定）
_FALLBACK_SECRETS_PATH = Path.home() / ".claude" / "quant-research-v2" / "secrets.env"


def _load_token_from_secrets_file() -> Optional[str]:
    """Try to extract ``TUSHARE_TOKEN`` from the quant-research-v2 secrets.env file.

    Read-only — does not modify os.environ. Returns the token string if
    found, otherwise None. Silent on missing/unreadable file (caller handles).
    """
    if not _FALLBACK_SECRETS_PATH.exists():
        return None
    try:
        for line in _FALLBACK_SECRETS_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Both KEY=value and `export KEY=value` styles work
            m = re.match(r"^(?:export\s+)?TUSHARE_TOKEN\s*=\s*['\"]?([^'\"]+)['\"]?\s*$", line)
            if m:
                return m.group(1).strip()
    except Exception as exc:
        logger.debug("Failed to parse %s: %s", _FALLBACK_SECRETS_PATH, exc)
    return None


def _resolve_token() -> Optional[str]:
    """Look up the TUSHARE_TOKEN from env, falling back to the secrets file.

    The secrets-file lookup runs at most once per process (the result is
    cached into ``os.environ`` so subsequent calls are env-only).
    """
    global _TOKEN_LOADED_ONCE
    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if token:
        return token
    if _TOKEN_LOADED_ONCE:
        return None  # already tried the file once, nothing there
    _TOKEN_LOADED_ONCE = True
    fallback = _load_token_from_secrets_file()
    if fallback:
        os.environ["TUSHARE_TOKEN"] = fallback
        logger.info(
            "Loaded TUSHARE_TOKEN from %s (fallback path)", _FALLBACK_SECRETS_PATH
        )
        return fallback
    return None


def is_tushare_available() -> bool:
    """Return True iff a TuShare token is available somewhere reachable.

    Cheap (env lookup + maybe one file read per process). The routing layer
    calls this on every A-share request to decide whether to put tushare at
    the head of the fallback chain.
    """
    return _resolve_token() is not None


def get_pro_client():
    """Return a process-shared TuShare Pro client. Constructs lazily.

    Raises
    ------
    RuntimeError
        Token not resolvable. Includes a hint about both ways to set it.
    """
    global _PRO_CLIENT
    if _PRO_CLIENT is not None:
        return _PRO_CLIENT
    with _PRO_LOCK:
        if _PRO_CLIENT is not None:  # double-checked locking
            return _PRO_CLIENT
        token = _resolve_token()
        if not token:
            raise RuntimeError(
                "TUSHARE_TOKEN not configured. Either set it in the project .env, "
                f"or place it in {_FALLBACK_SECRETS_PATH} as TUSHARE_TOKEN=..."
            )
        import tushare as ts
        _PRO_CLIENT = ts.pro_api(token)
        return _PRO_CLIENT


def _is_fatal(exc: Exception) -> bool:
    """Return True if the exception message indicates a permanent failure."""
    msg = str(exc).lower()
    return any(kw in msg for kw in _FATAL_KEYWORDS)


T = TypeVar("T")


def tushare_retry(
    max_retries: int = _MAX_RETRIES,
    base_sleep: float = _BASE_SLEEP,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator: retry transient TuShare errors with exponential backoff.

    Wraps a function that talks to ``pro.<method>`` and:
    - lets fatal errors (token / permission / 积分不够) propagate immediately,
      so the routing layer can fall back to akshare;
    - retries other exceptions up to ``max_retries`` times with
      ``base_sleep * 2^attempt`` second waits.

    Identical retry behavior to ``quant-research-v2`` 's
    ``_call_with_retry`` — see that script for the rationale.
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    if _is_fatal(exc):
                        # Don't retry; let route_to_vendor fall through.
                        raise
                    if attempt == max_retries:
                        raise
                    wait = base_sleep * (2 ** attempt)
                    logger.warning(
                        "tushare %s transient err (attempt %d/%d): %s — sleep %.1fs",
                        fn.__name__, attempt + 1, max_retries, exc, wait,
                    )
                    time.sleep(wait)
            return None  # unreachable
        return wrapper
    return decorator


# ---- ticker / date 格式转换 ----


_A_SHARE_SUFFIX_FIX = {".SS": ".SH"}  # yfinance 用 .SS, tushare 只认 .SH


def to_ts_code(ticker: str) -> str:
    """Normalize ticker to TuShare canonical form (``600487.SH`` / ``000001.SZ``).

    Accepts the project's internal forms (``600487.SS`` / ``600487.SH`` /
    bare ``600487``) and emits the tushare-expected form. Bare codes are
    suffixed by the 6→SH / 0|3→SZ heuristic.
    """
    if not ticker:
        raise ValueError("empty ticker")
    t = ticker.strip().upper()
    if "." in t:
        code, suf = t.split(".", 1)
        suf = "." + suf
        suf = _A_SHARE_SUFFIX_FIX.get(suf, suf)
        return f"{code}{suf}"
    # bare 6-digit code: infer suffix
    if len(t) == 6 and t.isdigit():
        if t.startswith("6"):
            return f"{t}.SH"
        if t.startswith(("0", "3")):
            return f"{t}.SZ"
    return t  # leave non-standard tickers alone


def to_ts_date(date_str: str) -> str:
    """``2026-05-12`` → ``20260512``. Idempotent for already-compact dates."""
    if not date_str:
        return ""
    s = str(date_str).strip()
    if "-" in s:
        return s.replace("-", "")
    return s


def from_ts_date(date_str: str) -> str:
    """``20260512`` → ``2026-05-12``. Helper for rendering tushare dates back to ISO."""
    if not date_str:
        return ""
    s = str(date_str).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s
