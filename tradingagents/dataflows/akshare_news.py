"""Akshare implementations for news / announcements."""

import logging
import re
import time
from datetime import datetime

import akshare as ak
import pandas as pd

from .akshare_common import (
    ak_retry, format_df_as_md, get_a_share_name, is_a_share,
    to_ak_symbol, NotApplicableError,
)

logger = logging.getLogger(__name__)


# ---- Global-news multi-source fallback ----
# Macro news is the same for every ticker in a batch run, so we cache the
# aggregated dataframe for a short TTL at process level. With 8 parallel
# workers analysing 500 tickers this turns ~4000 east-money hits into ~8.
_GLOBAL_NEWS_CACHE: dict = {"ts": 0.0, "df": None}
_GLOBAL_NEWS_TTL_SECS = 600  # 10 minutes — macro news changes slowly


def _fetch_one_global_source(name: str, fn, **kwargs) -> "pd.DataFrame | None":
    """Pull from one akshare global-news endpoint, normalise to common cols.

    Each endpoint returns slightly different column names; we coerce to a
    canonical schema (title / content / publish_time / source) and drop the
    rest. Returns None on failure — the caller aggregates whatever succeeded.
    """
    try:
        df = fn(**kwargs)
    except Exception as e:
        logger.warning("global-news source %s failed: %s", name, e)
        return None
    if df is None or df.empty:
        return None

    cols = {c: c for c in df.columns}
    title_col = next((c for c in df.columns if c in ("标题", "title")), None)
    body_col = next((c for c in df.columns if c in ("内容", "摘要", "content")), None)
    time_col = next((c for c in df.columns if "时间" in c or "日期" in c or "date" in c.lower()), None)

    out = pd.DataFrame()
    out["title"] = df[title_col].astype(str) if title_col else ""
    out["content"] = df[body_col].astype(str) if body_col else ""
    if time_col:
        out["publish_time"] = pd.to_datetime(df[time_col], errors="coerce")
    else:
        out["publish_time"] = pd.NaT
    out["source"] = name
    return out


def _aggregate_global_news() -> "pd.DataFrame":
    """Merge multiple akshare global-news endpoints into one deduped frame.

    Order is by signal density and reliability: EM (eastmoney) is biggest,
    CLS (财联社) is news-flash quality, Sina/Futu/THS as backstops.
    Any source that fails is silently skipped — we want as much as we can.
    """
    sources = [
        ("eastmoney",   ak.stock_info_global_em,   {}),
        ("cailianshe",  ak.stock_info_global_cls,  {"symbol": "全部"}),
        ("sina",        ak.stock_info_global_sina, {}),
        ("futu",        ak.stock_info_global_futu, {}),
        ("ths",         ak.stock_info_global_ths,  {}),
    ]
    frames = []
    for name, fn, kwargs in sources:
        df = _fetch_one_global_source(name, fn, **kwargs)
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["title", "content", "publish_time", "source"])
    combined = pd.concat(frames, ignore_index=True)
    # Dedupe by title (same headline often appears across sources)
    combined = combined.drop_duplicates(subset=["title"], keep="first")
    combined = combined.sort_values("publish_time", ascending=False, na_position="last")
    return combined.reset_index(drop=True)


def _get_global_news_df_cached() -> "pd.DataFrame":
    now = time.time()
    if _GLOBAL_NEWS_CACHE["df"] is not None and now - _GLOBAL_NEWS_CACHE["ts"] < _GLOBAL_NEWS_TTL_SECS:
        return _GLOBAL_NEWS_CACHE["df"]
    df = _aggregate_global_news()
    _GLOBAL_NEWS_CACHE["df"] = df
    _GLOBAL_NEWS_CACHE["ts"] = now
    return df


def _filter_global_news_by_ticker(
    df: "pd.DataFrame",
    ticker: str,
    name: str,
    start_date: str,
    end_date: str,
) -> "pd.DataFrame":
    """Pick rows from the cached global-news df whose title/content mention this ticker.

    Two recall channels — either one matches:
    1. 6-digit ticker code anywhere in the headline or body (e.g. "002155.SZ"
       or just "002155"). Most precise, but cailianshe quick-notes often omit
       the code.
    2. Exact Chinese name match (e.g. "湖南黄金"). Higher recall but small
       chance of false positives on short generic names — we accept that;
       the LLM is robust to a few off-target items in a sentiment block.

    ``start_date`` / ``end_date`` are inclusive of both endpoints (YYYY-MM-DD).
    The cached frame already covers the last ~few hours/days depending on
    each source's depth, so a wide window is fine.
    """
    if df is None or df.empty:
        return df

    code = re.sub(r"\.[A-Z]+$", "", ticker)  # 002155.SZ → 002155
    # Build regex: code (word-boundary loose, since Chinese text has no \b)
    # OR exact Chinese name (only if non-empty and not trivially short).
    patterns = [re.escape(code)]
    if name and len(name) >= 2:
        patterns.append(re.escape(name))
    pat = re.compile("|".join(patterns))

    text = (df["title"].fillna("") + " " + df["content"].fillna("")).astype(str)
    hit = df[text.str.contains(pat, na=False)].copy()

    # Date window filter (publish_time is already datetime; ok if NaT)
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
    in_window = hit["publish_time"].between(start, end, inclusive="left")
    return hit[in_window | hit["publish_time"].isna()]


@ak_retry()
def get_news_akshare(ticker: str, start_date: str, end_date: str) -> str:
    """Per-stock news for an A-share ticker, filtered to [start_date, end_date].

    Two recall paths combined:

    1. ``stock_news_em(symbol=...)`` — Eastmoney's per-stock news page.
       Best per-ticker recall for major/mid-cap names.
    2. ``_get_global_news_df_cached()`` filtered to rows that mention the
       6-digit code or the Chinese name. Pulls in 财联社 / 同花顺 / 富途 /
       新浪 quick-notes that Eastmoney's per-stock page often misses (cls
       in particular reacts to intraday events 5-30 min faster).

    Results are merged, deduped by headline, and sorted newest-first.
    """
    symbol = to_ak_symbol(ticker)
    name = get_a_share_name(ticker)

    # --- Path 1: Eastmoney per-stock news ---
    em_rows: "pd.DataFrame" = pd.DataFrame()
    try:
        em = ak.stock_news_em(symbol=symbol)
    except Exception as e:
        logger.warning("stock_news_em failed for %s: %s", symbol, e)
        em = None
    if em is not None and not em.empty:
        # Canonicalise column names so we can merge with global-news frame
        title_col = next((c for c in em.columns if c in ("新闻标题", "标题", "title")), None)
        body_col = next((c for c in em.columns if c in ("新闻内容", "内容", "摘要", "content")), None)
        time_col = next((c for c in em.columns if "时间" in c or "publish" in c.lower()), None)
        em_rows = pd.DataFrame()
        em_rows["title"] = em[title_col].astype(str) if title_col else ""
        em_rows["content"] = em[body_col].astype(str) if body_col else ""
        em_rows["publish_time"] = pd.to_datetime(em[time_col], errors="coerce") if time_col else pd.NaT
        em_rows["source"] = "eastmoney_stock"

    # --- Path 2: filtered slice of the multi-source global feed ---
    try:
        global_df = _get_global_news_df_cached()
        global_rows = _filter_global_news_by_ticker(
            global_df, ticker, name, start_date, end_date
        )
    except Exception as e:
        logger.warning("global-news ticker-filter failed for %s: %s", ticker, e)
        global_rows = pd.DataFrame()

    # --- Merge + date-window filter on the em path ---
    if not em_rows.empty:
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
        em_in = em_rows["publish_time"].between(start, end, inclusive="left")
        em_rows = em_rows[em_in | em_rows["publish_time"].isna()]

    combined = pd.concat(
        [r for r in (em_rows, global_rows) if r is not None and not r.empty],
        ignore_index=True,
    ) if (not em_rows.empty or (global_rows is not None and not global_rows.empty)) else pd.DataFrame()

    if combined.empty:
        return f"## News for {ticker} {start_date} → {end_date}\n\n_No news in window._"

    # Dedupe by headline (cls + em sometimes mirror the same announcement)
    combined = combined.drop_duplicates(subset=["title"], keep="first")
    combined = combined.sort_values("publish_time", ascending=False, na_position="last")

    return format_df_as_md(
        combined,
        f"News for {ticker} {start_date} → {end_date} "
        f"(merged eastmoney/stock + {combined['source'].nunique() - 1} global sources)",
        max_rows=25,
    )


def get_global_news_akshare(curr_date: str, look_back_days=None, limit=None) -> str:
    """Macro/global financial news aggregated from five akshare endpoints.

    Pulls from East-Money + 财联社 + Sina + Futu + 同花顺 in sequence and
    merges. Result is cached for 10 minutes per process — macro news is
    ticker-independent so a batch of 500 tickers only triggers one real
    fetch per worker. If every source fails, returns an explanatory
    placeholder so the LLM doesn't fabricate.

    ``look_back_days`` and ``limit`` accept ``None`` (the tool-wrapper
    default when the caller passes no override) and resolve to sensible
    defaults — keeps us drop-in compatible with the langchain @tool
    signature which marks both as ``Optional[int]``.
    """
    # Resolve optional args (tool wrapper passes None when caller omits them)
    if look_back_days is None:
        look_back_days = 7
    if limit is None:
        limit = 30

    df = _get_global_news_df_cached()
    if df is None or df.empty:
        return (
            f"## Global news as of {curr_date}\n\n"
            "_All 5 akshare global-news endpoints (eastmoney / cailianshe / "
            "sina / futu / ths) returned no data. Macro signal unavailable "
            "for this run._"
        )

    # Filter to recent window
    end = pd.to_datetime(curr_date) + pd.Timedelta(days=1)
    start = end - pd.Timedelta(days=look_back_days + 1)
    in_window = df[(df["publish_time"] >= start) & (df["publish_time"] < end)]
    # If the window is empty (maybe non-trading-day weekend), fall back to
    # the most recent `limit` items rather than returning empty.
    if in_window.empty:
        in_window = df.head(limit)

    in_window = in_window.head(limit)
    return format_df_as_md(
        in_window,
        f"Global news as of {curr_date} (last {look_back_days}d, "
        f"merged from {in_window['source'].nunique()} sources)",
        max_rows=limit,
    )


@ak_retry()
def get_announcements_akshare(ticker: str, start_date: str, end_date: str) -> str:
    """Legal disclosure announcements (法定信披) from CNINFO/EastMoney."""
    symbol = to_ak_symbol(ticker)

    # stock_notice_report returns a daily snapshot; walk the date window
    from datetime import datetime, timedelta
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    frames = []
    cursor = start
    while cursor <= end:
        try:
            daily = ak.stock_notice_report(symbol="全部", date=cursor.strftime("%Y%m%d"))
            if daily is not None and not daily.empty:
                # Filter rows that reference this ticker
                code_col = next((c for c in daily.columns if "代码" in c), None)
                if code_col:
                    hit = daily[daily[code_col].astype(str).str.zfill(6) == symbol]
                    if not hit.empty:
                        frames.append(hit)
        except Exception as e:
            logger.warning("stock_notice_report on %s failed: %s", cursor, e)
        cursor += timedelta(days=1)

    if not frames:
        return f"## Announcements for {ticker} {start_date} → {end_date}\n\n_No filings._"

    combined = pd.concat(frames, ignore_index=True)
    return format_df_as_md(combined, f"Announcements for {ticker} {start_date} → {end_date}", max_rows=40)
