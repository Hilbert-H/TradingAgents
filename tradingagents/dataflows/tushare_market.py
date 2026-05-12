"""TuShare-backed market data: insider transactions.

Replaces:
  ak.stock_ggcg_em + ak.stock_share_hold_change_sse/szse
    →  pro.stk_holdertrade

stk_holdertrade covers both:
  - holder_type='C' (法人/控股股东) — 大股东减持/增持
  - holder_type='G' (个人/高管) — 董监高减持/增持
  - holder_type='P' (PE/创投) — 战略投资者减持

so it's a strict superset of the akshare endpoints (which split this into
two separate APIs).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd

from .akshare_common import format_df_as_md
from .tushare_common import (
    get_pro_client, to_ts_code, from_ts_date, tushare_retry,
)

logger = logging.getLogger(__name__)


# === DATA SCHEMA ===
#
# stk_holdertrade : pd.DataFrame
#   ts_code      str   '600487.SH'
#   ann_date     str   公告日期 'YYYYMMDD'
#   holder_name  str   股东 / 高管姓名
#   holder_type  str   {'C': 法人/控股, 'G': 个人/高管, 'P': PE/创投}
#   in_de        str   {'IN': 增持, 'DE': 减持}
#   change_vol   float 增减股数 (股, 1e6 = 100 万)
#   change_ratio float 占总股本比例 (%)
#   after_share  float 操作后持股数 (股)
#   after_ratio  float 操作后持股比例 (%)
#   avg_price    float 平均成交价 (元)
#   total_share  float 总股本 (股)


_HOLDER_TYPE_CN = {"C": "法人/控股股东", "G": "个人/高管", "P": "PE/创投"}
_IN_DE_CN = {"IN": "增持", "DE": "减持"}


@tushare_retry()
def _fetch_holdertrade(ts_code: str, start: str, end: str) -> "pd.DataFrame":
    pro = get_pro_client()
    return pro.stk_holdertrade(ts_code=ts_code, start_date=start, end_date=end)


def get_insider_transactions_tushare(
    ticker: str,
    curr_date: str,
    look_back_days: int = 90,
) -> str:
    """Insider transactions (大股东 + 高管 + PE 增减持) over a recent window.

    Default look-back is 90 days — short enough to be relevant, long enough
    to actually pick up the typically-monthly disclosure cadence. Useful
    signals an LLM should call out:

    - 大股东大幅减持 → 信号转弱
    - 多名高管同步减持 → 内部信心分歧
    - 控股股东增持 → 强托底信号
    - PE/创投退出 → 解禁退潮(中性偏空)
    """
    ts_code = to_ts_code(ticker)
    end = datetime.strptime(curr_date, "%Y-%m-%d") if "-" in curr_date else datetime.strptime(curr_date, "%Y%m%d")
    start = end - timedelta(days=look_back_days)
    df = _fetch_holdertrade(ts_code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    if df is None or df.empty:
        return (
            f"## Insider transactions for {ticker} (last {look_back_days}d)\n\n"
            "_最近无大股东 / 高管 / PE 增减持公告 (tushare stk_holdertrade)._"
        )

    df = df.sort_values("ann_date", ascending=False).reset_index(drop=True).copy()

    # 数值列优化展示
    if "change_vol" in df.columns:
        df["change_vol_万股"] = (pd.to_numeric(df["change_vol"], errors="coerce") / 1e4).round(2)
        df.drop(columns=["change_vol"], inplace=True)
    if "change_ratio" in df.columns:
        df["change_ratio"] = pd.to_numeric(df["change_ratio"], errors="coerce").round(4)
    if "after_ratio" in df.columns:
        df["after_ratio"] = pd.to_numeric(df["after_ratio"], errors="coerce").round(4)

    # 中文化 in_de / holder_type 提高可读性
    if "in_de" in df.columns:
        df["方向"] = df["in_de"].map(_IN_DE_CN).fillna(df["in_de"])
        df.drop(columns=["in_de"], inplace=True)
    if "holder_type" in df.columns:
        df["持有人类型"] = df["holder_type"].map(_HOLDER_TYPE_CN).fillna(df["holder_type"])
        df.drop(columns=["holder_type"], inplace=True)
    if "ann_date" in df.columns:
        df["ann_date"] = df["ann_date"].apply(from_ts_date)

    # 列顺序
    front = ["ann_date", "持有人类型", "holder_name", "方向", "change_vol_万股",
             "change_ratio", "after_ratio", "avg_price"]
    cols = [c for c in front if c in df.columns] + [c for c in df.columns if c not in front]
    df = df[cols]

    return format_df_as_md(
        df,
        f"Insider transactions for {ticker} (last {look_back_days}d, tushare)",
        max_rows=20,
    )
