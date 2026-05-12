"""TuShare-backed news fetcher.

What this gives us beyond akshare
---------------------------------
``pro.news`` returns ~1500 rows per single call (sina src), vs the ~200 we
get from akshare's `stock_info_global_em`. That's 7×–10× the recall surface
for the same ticker-filter pass.

Rate limit
~~~~~~~~~~
TuShare caps ``pro.news`` at **2 calls/hour** at the 2000-point tier
(measured empirically — the doc says "1/min" but the real ceiling is hour-
scoped). With 8 parallel batch workers this blows up immediately, so the
aggregated news df is cached **at the filesystem level** (not process-
level) with a 30-minute TTL. The first worker to find the cache stale
fetches and atomically rename-replaces the parquet; all other workers
read the file — at most 1 call/hour shared across the whole batch.
If a fetch fails (e.g. another worker beat us and ate the quota), we
fall back to whatever stale file is on disk rather than empty-feeding
the analyst.

API note
~~~~~~~~
``pro.anns_d`` (per-ticker announcements) requires a higher tier than the
user has, so this module **only** covers news. Announcements stay on
``akshare`` 's ``stock_notice_report`` via the routing layer.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from .akshare_common import format_df_as_md, get_a_share_name
from .tushare_common import (
    get_pro_client, to_ts_code, tushare_retry,
)

logger = logging.getLogger(__name__)


# === 文件级缓存（跨进程共享，绕 2 次/小时 限流） ===
#
# _NEWS_CACHE_DIR  : Path
#     parquet 落盘目录。每个 src 一个文件,文件 mtime 决定是否过期。
#     默认 ~/.cache/tradingagents/tushare_news/,可用 env 覆盖。
#
# _NEWS_TTL_SECS   : int
#     文件级缓存有效期。pro.news 上限 2 次/小时 → TTL 30 min 即可。
#     batch 多 worker 启动后第一个发现 stale 的去拉,其他读文件。
#
# _NEWS_SOURCES    : tuple
#     主源选择: sina 量最大,内容最完整;cls 是财联社快讯但容量小;wallstreetcn 深度好
#     默认只拉 sina,因为 1500 条已经覆盖 24h+。

_NEWS_CACHE_DIR = Path(os.environ.get(
    "TRADINGAGENTS_TUSHARE_NEWS_CACHE",
    str(Path.home() / ".cache" / "tradingagents" / "tushare_news"),
))
_NEWS_TTL_SECS = 1800  # 30 min — half the 1-hour quota window

# 单次取多少条 (tushare pro.news max 1500)
_NEWS_PAGE_SIZE = 1500

_NEWS_SOURCES = ("sina",)

# 进程内并发保护(同进程多线程不重复拉,避免无谓限流触发)
_FETCH_LOCK = threading.Lock()

# 限流冷却:一次拉取失败后,多久之内不再瞬时重试。
# 单位秒。pro.news 的硬限是 2 次/小时,所以失败后等 5 min 重试一次比较合理:
#   - 短到 cache 真过期后还有机会自愈
#   - 长到避免 8 worker 同时撞限流
_FAILURE_COOLDOWN_SECS = 300


def _fetch_news_one_source(src: str, start_dt: str, end_dt: str) -> "pd.DataFrame":
    """Pull a single 24-hour window of news from one src.

    **No retry decorator** — pro.news has a 2 calls/hour ceiling at the
    2000-pt tier and the per-minute "frequency exceeded" errors take 30+
    minutes to clear. Exponential retry within a single call is pure waste
    here; we let the caller cooldown-mark the failure instead.
    """
    pro = get_pro_client()
    return pro.news(src=src, start_date=start_dt, end_date=end_dt)


def _failure_marker_path(src: str) -> Path:
    return _NEWS_CACHE_DIR / f"news_{src}.fail"


def _in_cooldown(src: str) -> bool:
    """True if a recent fetch failure flagged this src as on cooldown."""
    p = _failure_marker_path(src)
    if not p.exists():
        return False
    return time.time() - p.stat().st_mtime < _FAILURE_COOLDOWN_SECS


def _mark_failure(src: str) -> None:
    """Touch the failure marker so subsequent workers/calls skip the fetch."""
    _NEWS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _failure_marker_path(src)
    try:
        p.touch()
    except Exception as exc:
        logger.debug("failed to touch failure marker %s: %s", p, exc)


def _cache_path(src: str) -> Path:
    return _NEWS_CACHE_DIR / f"news_{src}.parquet"


def _read_cache_if_fresh(src: str) -> "pd.DataFrame | None":
    """Return cached df if file exists and mtime ≤ TTL ago; else None.

    A stale-but-existing file is *not* returned here; caller decides whether
    to use it as fallback after a failed refetch.
    """
    p = _cache_path(src)
    if not p.exists():
        return None
    if time.time() - p.stat().st_mtime > _NEWS_TTL_SECS:
        return None
    try:
        return pd.read_parquet(p)
    except Exception as exc:
        logger.warning("tushare news cache read failed (%s): %s", p, exc)
        return None


def _read_cache_stale_ok(src: str) -> "pd.DataFrame | None":
    """Last-resort: return whatever file exists, even if expired."""
    p = _cache_path(src)
    if not p.exists():
        return None
    try:
        return pd.read_parquet(p)
    except Exception:
        return None


def _write_cache_atomic(src: str, df: "pd.DataFrame") -> None:
    """Write parquet via temp + os.replace so concurrent readers never see half-writes."""
    _NEWS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = _cache_path(src)
    tmp = p.with_suffix(p.suffix + f".tmp.{os.getpid()}")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, p)


def _fetch_and_normalize(src: str) -> "pd.DataFrame":
    """Single-source: pull 36h window of tushare news + canonicalize columns."""
    end = datetime.now()
    start = end - timedelta(hours=36)
    start_str = start.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end.strftime("%Y-%m-%d %H:%M:%S")
    raw = _fetch_news_one_source(src, start_str, end_str)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["title", "content", "publish_time", "source"])
    return pd.DataFrame({
        "title": raw.get("title", "").astype(str).fillna(""),
        "content": raw.get("content", "").astype(str).fillna(""),
        "publish_time": pd.to_datetime(raw.get("datetime"), errors="coerce"),
        "source": f"tushare_{src}",
    })


def _aggregate_news_df() -> "pd.DataFrame":
    """Fetch + normalize the news df from configured sources, file-level cache.

    Per-source flow:
      1. Read parquet if mtime within TTL → done.
      2. Acquire in-process lock; re-check (another thread may have just
         written it).
      3. Fetch from tushare (subject to 2 calls/hour).
      4. Atomic-write parquet.
      5. On fetch failure: return stale parquet if present, else empty df.

    Cross-process serialization is best-effort — if two workers race past
    step 1 their two fetches both run, but step 4's atomic rename keeps the
    cache consistent. Worst case: 2 wasted API calls per src per 30 min.
    """
    frames = []
    for src in _NEWS_SOURCES:
        fresh = _read_cache_if_fresh(src)
        if fresh is not None and not fresh.empty:
            frames.append(fresh)
            continue

        # If a recent fetch already failed (limit hit), don't even try —
        # go straight to stale cache. This keeps 8 batch workers from each
        # eating one retry round before giving up.
        if _in_cooldown(src):
            stale = _read_cache_stale_ok(src)
            if stale is not None and not stale.empty:
                frames.append(stale)
            continue

        # Lock keeps same-process threads from double-fetching.
        # Cross-process: a brief race window is tolerable; atomic rename
        # is the consistency guarantee.
        with _FETCH_LOCK:
            # Re-check after acquiring lock (another thread may have written it)
            fresh = _read_cache_if_fresh(src)
            if fresh is not None and not fresh.empty:
                frames.append(fresh)
                continue
            if _in_cooldown(src):  # another thread / process just marked failure
                stale = _read_cache_stale_ok(src)
                if stale is not None and not stale.empty:
                    frames.append(stale)
                continue

            try:
                df = _fetch_and_normalize(src)
                _write_cache_atomic(src, df)
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                logger.warning(
                    "tushare news refetch failed (src=%s): %s — cooldown %ds, falling back to stale cache",
                    src, exc, _FAILURE_COOLDOWN_SECS,
                )
                _mark_failure(src)
                stale = _read_cache_stale_ok(src)
                if stale is not None and not stale.empty:
                    frames.append(stale)
                # else: just skip this src

    if not frames:
        return pd.DataFrame(columns=["title", "content", "publish_time", "source"])
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["title", "publish_time"], keep="first")
    combined = combined.sort_values("publish_time", ascending=False, na_position="last")
    return combined.reset_index(drop=True)


def _filter_news_by_ticker(
    df: "pd.DataFrame",
    ticker: str,
    name: Optional[str],
    start_date: str,
    end_date: str,
) -> "pd.DataFrame":
    """Pick rows whose title/content references this ticker by code or name.

    Same two-channel matcher as the akshare global-news filter: 6-digit
    code OR exact Chinese name. Empty / NaN publish_time is kept (some
    sources don't carry seconds-grain timestamps).
    """
    if df is None or df.empty:
        return df

    # Strip suffix for code matching: 600487.SH → 600487
    code = re.sub(r"\.[A-Z]+$", "", ticker)
    patterns = [re.escape(code)]
    if name and len(name) >= 2:
        patterns.append(re.escape(name))
    pat = re.compile("|".join(patterns))

    text = (df["title"].fillna("") + " " + df["content"].fillna("")).astype(str)
    hit = df[text.str.contains(pat, na=False)].copy()

    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
    in_window = hit["publish_time"].between(start, end, inclusive="left")
    return hit[in_window | hit["publish_time"].isna()]


def get_news_tushare(ticker: str, start_date: str, end_date: str) -> str:
    """Per-stock news for an A-share ticker via TuShare ``pro.news``.

    Aggregates from the configured sources (default: sina, 1500 rows/call),
    then filters by 6-digit code OR Chinese name in title/content.

    The result is markdown for direct LLM consumption. Returns an explanatory
    "no news" string if nothing matches or every source failed.
    """
    ts_code = to_ts_code(ticker)
    name = get_a_share_name(ticker)

    try:
        df = _aggregate_news_df()
    except Exception as exc:
        # _aggregate_news_df is already retry-safe; if we still error, it's
        # because the very first hit raised a fatal (token/permission). Let
        # the routing layer fall back to akshare.
        logger.warning("tushare news aggregation failed for %s: %s", ts_code, exc)
        raise

    if df.empty:
        return (
            f"## News for {ticker} {start_date} → {end_date}\n\n"
            "_No tushare news data (all sources empty)._"
        )

    hit = _filter_news_by_ticker(df, ts_code, name, start_date, end_date)
    if hit.empty:
        return (
            f"## News for {ticker} {start_date} → {end_date}\n\n"
            "_No ticker-matched news in tushare feed for this window._"
        )

    return format_df_as_md(
        hit,
        f"News for {ticker} {start_date} → {end_date} "
        f"(tushare {hit['source'].nunique()} src(s), filtered by code/name)",
        max_rows=25,
    )
