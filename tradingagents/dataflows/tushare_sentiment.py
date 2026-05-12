"""TuShare-backed sentiment / chip-concentration data.

Replaces:
  - ``akshare.stock_zh_a_gdhs_detail_em`` (which returns 2016-era data, broken).

Adds (free side-channels of the same ticker query):
  - top 10 holders / top 10 float holders (per quarter).

Out of scope:
  - 东财热度排名 / 研报评级 — TuShare doesn't expose those at our point tier.
    Those keep using akshare.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd

from .akshare_common import format_df_as_md
from .tushare_common import (
    get_pro_client, to_ts_code, to_ts_date, from_ts_date, tushare_retry,
)

logger = logging.getLogger(__name__)


# === DATA SCHEMA ===
#
# stk_holdernumber : pd.DataFrame  (返回示例 shape=(10, 4))
#   ts_code     : str, e.g. '600487.SH'
#   ann_date    : str, 公告日期 YYYYMMDD
#   end_date    : str, 报告期截止日 YYYYMMDD  ← 季度末
#   holder_num  : int, 股东户数
#
# 用法约定:start_date / end_date 是**公告日期窗口**,所以要够长才能拿到
# 多个季度。我们默认回看 2 年(8 个季度),足够 LLM 看清户数变化趋势。


_DEFAULT_LOOKBACK_YEARS = 2


def _date_window(curr_date: str, years_back: int = _DEFAULT_LOOKBACK_YEARS) -> tuple[str, str]:
    """``curr_date`` (YYYY-MM-DD) → ``(YYYYMMDD, YYYYMMDD)`` for an N-year window."""
    end = datetime.strptime(curr_date, "%Y-%m-%d") if "-" in curr_date else datetime.strptime(curr_date, "%Y%m%d")
    start = end - timedelta(days=years_back * 365 + 30)  # small buffer
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


@tushare_retry()
def _fetch_holdernumber(ts_code: str, start: str, end: str) -> "pd.DataFrame":
    pro = get_pro_client()
    return pro.stk_holdernumber(ts_code=ts_code, start_date=start, end_date=end)


def _derive_chip_concentration_signals(df: "pd.DataFrame") -> "pd.DataFrame":
    """Compute QoQ change + multi-period delta to surface 集中度 trend.

    Adds:
      qoq_change_pct : 户数环比变动 (%)
      vs_2q_ago_pct  : 户数较两季前变动 (%, 主升浪/吸筹的关键周期)

    上行 → 户数增加 → 散户化(筹码分散),下行 → 户数减少 → 机构化(筹码集中)。
    """
    if df is None or df.empty:
        return df
    out = df.sort_values("end_date", ascending=True).reset_index(drop=True).copy()
    out["holder_num"] = pd.to_numeric(out["holder_num"], errors="coerce")
    out["qoq_change_pct"] = (out["holder_num"].pct_change() * 100).round(2)
    out["vs_2q_ago_pct"] = (out["holder_num"].pct_change(periods=2) * 100).round(2)
    # 按报告期倒序显示(最新的在最上方便 LLM 读)
    return out.sort_values("end_date", ascending=False).reset_index(drop=True)


def get_shareholder_count_tushare(ticker: str, curr_date: str) -> str:
    """Quarterly shareholder-count history for an A-share ticker.

    Pulls ~2 years (8 quarters) from TuShare ``pro.stk_holdernumber``,
    then attaches QoQ and 2-quarter delta columns so the LLM can spot
    chip concentration / dispersion trends without doing arithmetic.

    The returned markdown table is what the sentiment analyst sees.
    """
    ts_code = to_ts_code(ticker)
    start, end = _date_window(curr_date)
    df = _fetch_holdernumber(ts_code, start, end)
    if df is None or df.empty:
        return (
            f"## Shareholder count for {ticker} (as of {curr_date})\n\n"
            "_TuShare returned no rows; check ticker validity or window._"
        )

    enriched = _derive_chip_concentration_signals(df)
    # Reorder columns for clarity
    cols = ["end_date", "ann_date", "holder_num", "qoq_change_pct", "vs_2q_ago_pct"]
    enriched = enriched[[c for c in cols if c in enriched.columns]]
    enriched["end_date"] = enriched["end_date"].apply(from_ts_date)
    enriched["ann_date"] = enriched["ann_date"].apply(from_ts_date)

    return format_df_as_md(
        enriched,
        f"Shareholder count for {ticker} (last {len(enriched)} quarters, tushare)",
        max_rows=12,
    )


@tushare_retry()
def _fetch_top10_holders(ts_code: str, start: str, end: str, float_only: bool) -> "pd.DataFrame":
    pro = get_pro_client()
    fn = pro.top10_floatholders if float_only else pro.top10_holders
    return fn(ts_code=ts_code, start_date=start, end_date=end)


def get_top10_holders_tushare(ticker: str, curr_date: str, float_only: bool = False) -> str:
    """Latest available 'top-10 (float) holders' table.

    Tushare returns one row per holder per period (10 rows/period). We pick
    the most recent period in the lookback window and render those 10 rows.
    ``float_only=True`` picks free-float ranking (tradeable shares),
    otherwise total ranking.
    """
    ts_code = to_ts_code(ticker)
    start, end = _date_window(ticker[:0] + curr_date, years_back=1)  # 1 yr is enough
    df = _fetch_top10_holders(ts_code, start, end, float_only)
    if df is None or df.empty:
        kind = "float holders" if float_only else "holders"
        return f"## Top-10 {kind} for {ticker}\n\n_No data._"

    # Keep only the most-recent end_date (one quarter's 10 rows)
    latest = df["end_date"].max()
    snap = df[df["end_date"] == latest].copy()

    # Trim noisy columns; keep what an LLM needs
    keep_cols = [c for c in (
        "holder_name", "hold_amount", "hold_ratio", "hold_float_ratio", "hold_change",
    ) if c in snap.columns]
    snap = snap[keep_cols]
    label = "float holders" if float_only else "holders"
    title = f"Top-10 {label} for {ticker} (period {from_ts_date(latest)}, tushare)"
    return format_df_as_md(snap, title, max_rows=10)
