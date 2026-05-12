"""TuShare-backed A-share capital-flow signals.

Replaces six akshare endpoints (slower / less complete):
  akshare                          → tushare
  ------------------------------------------------------
  stock_lhb_stock_detail_em        → pro.top_list   (per-stock LHB rows)
  stock_lhb_jgmmtj_em              → pro.top_inst   (institutional seats)
  stock_hsgt_individual_em         → pro.hk_hold    (northbound per-stock)
  stock_hsgt_hist_em               → pro.moneyflow_hsgt  (market-wide N/S flow)
  stock_margin_detail_sse/szse     → pro.margin_detail   (per-stock 融资融券)
  stock_individual_fund_flow       → pro.moneyflow       (smart-money breakdown)

All six tushare endpoints take a per-day or per-window query; we do a
client-side filter on ts_code when the tushare endpoint is "全市场 by trade
date" (top_list / top_inst). Pagination isn't needed at our query depths.
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


# === 公共工具 ===


def _date_window(curr_date: str, look_back_days: int) -> tuple[str, str]:
    """``(YYYY-MM-DD, look_back_days)`` → ``(YYYYMMDD_start, YYYYMMDD_end)``.

    Both bounds inclusive. ``curr_date`` is the right edge (typically the
    user-specified trade_date); window extends look_back_days behind it.
    """
    end = datetime.strptime(curr_date, "%Y-%m-%d") if "-" in curr_date else datetime.strptime(curr_date, "%Y%m%d")
    start = end - timedelta(days=look_back_days)
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


# === (1) 龙虎榜个股明细 — pro.top_list ===
#
# 实测:pro.top_list 必填 trade_date,不支持窗口查询(start_date/end_date 拒绝)。
# 走逐交易日 loop + ts_code 过滤,然后纵向 concat。窗口典型 5-10 天 → 5-10 次
# API 调用,在 2000 积分 tier 不会触发限流。
#
# 进一步优化:服务端的 ts_code 参数可以和 trade_date 同时传(已实测),
# 直接拿到该日该股的 LHB 行(若上榜),省客户端过滤。


def _iter_trade_days(start: str, end: str):
    """生成 [start, end] 闭区间内每天的 YYYYMMDD 字符串(含周末/节假日)。

    我们不预先判断是否交易日 — tushare 的 top_list 对非交易日返回空,
    多调几次 API 的代价远小于本地 trade_cal 查询的复杂度(还得拉日历缓存)。
    """
    cur = datetime.strptime(start, "%Y%m%d")
    end_dt = datetime.strptime(end, "%Y%m%d")
    while cur <= end_dt:
        yield cur.strftime("%Y%m%d")
        cur += timedelta(days=1)


@tushare_retry()
def _fetch_top_list_one_day(ts_code: str, trade_date: str) -> "pd.DataFrame":
    pro = get_pro_client()
    return pro.top_list(trade_date=trade_date, ts_code=ts_code)


def get_lhb_detail_tushare(ticker: str, curr_date: str, look_back_days: int = 5) -> str:
    """Dragon-Tiger LHB rows for a single ticker over a recent window."""
    ts_code = to_ts_code(ticker)
    start, end = _date_window(curr_date, look_back_days)
    frames = []
    for d in _iter_trade_days(start, end):
        try:
            daily = _fetch_top_list_one_day(ts_code, d)
        except Exception as exc:
            logger.debug("top_list %s %s failed: %s", ts_code, d, exc)
            continue
        if daily is not None and not daily.empty:
            frames.append(daily)
    if not frames:
        return (
            f"## Dragon-Tiger seats for {ticker} (last {look_back_days}d)\n\n"
            "_未上龙虎榜 (tushare top_list returned no rows)._"
        )

    df = pd.concat(frames, ignore_index=True)
    keep = [c for c in (
        "trade_date", "name", "close", "pct_change", "turnover_rate", "amount",
        "l_sell", "l_buy", "l_amount", "net_amount", "net_rate", "amount_rate",
        "float_values", "reason",
    ) if c in df.columns]
    df = df.sort_values("trade_date", ascending=False).reset_index(drop=True)[keep].copy()
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(from_ts_date)
    return format_df_as_md(
        df,
        f"Dragon-Tiger seats for {ticker} (last {look_back_days}d, tushare)",
        max_rows=20,
    )


# === (2) 龙虎榜机构席位 — pro.top_inst ===
# 与 top_list 同款:必填 trade_date,逐日循环。


@tushare_retry()
def _fetch_top_inst_one_day(ts_code: str, trade_date: str) -> "pd.DataFrame":
    pro = get_pro_client()
    return pro.top_inst(trade_date=trade_date, ts_code=ts_code)


def get_lhb_institutional_tushare(ticker: str, curr_date: str, look_back_days: int = 10) -> str:
    """机构席位龙虎榜:含买入金额、卖出金额、净买入等。"""
    ts_code = to_ts_code(ticker)
    start, end = _date_window(curr_date, look_back_days)
    frames = []
    for d in _iter_trade_days(start, end):
        try:
            daily = _fetch_top_inst_one_day(ts_code, d)
        except Exception as exc:
            logger.debug("top_inst %s %s failed: %s", ts_code, d, exc)
            continue
        if daily is not None and not daily.empty:
            frames.append(daily)
    if not frames:
        return (
            f"## Institutional LHB seats for {ticker} (last {look_back_days}d)\n\n"
            "_无机构席位上榜记录 (tushare top_inst)._"
        )

    df = pd.concat(frames, ignore_index=True)
    keep = [c for c in (
        "trade_date", "exalter", "side", "buy", "buy_rate", "sell", "sell_rate",
        "net_buy", "reason",
    ) if c in df.columns]
    df = df.sort_values("trade_date", ascending=False).reset_index(drop=True)[keep].copy()
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(from_ts_date)
    return format_df_as_md(
        df,
        f"Institutional LHB seats for {ticker} (last {look_back_days}d, tushare)",
        max_rows=30,
    )


# === (3) 北向资金 — 个股 (pro.hk_hold) ===


@tushare_retry()
def _fetch_hk_hold(ts_code: str, start: str, end: str) -> "pd.DataFrame":
    """pro.hk_hold:沪深港通持股变化。返回 (trade_date, ts_code, vol, ratio, ...)."""
    pro = get_pro_client()
    return pro.hk_hold(ts_code=ts_code, start_date=start, end_date=end)


def get_north_capital_individual_tushare(ticker: str, curr_date: str, look_back_days: int = 10) -> str:
    """Per-stock northbound holding history for the look-back window."""
    ts_code = to_ts_code(ticker)
    start, end = _date_window(curr_date, look_back_days)
    df = _fetch_hk_hold(ts_code, start, end)
    if df is None or df.empty:
        return (
            f"## Northbound holdings for {ticker} (last {look_back_days}d)\n\n"
            "_无沪深港通持股数据 (该股可能未纳入港股通标的)._"
        )

    # 字段:trade_date, ts_code, name, vol(股), ratio(%), exchange
    keep = [c for c in ("trade_date", "vol", "ratio", "exchange") if c in df.columns]
    df = df.sort_values("trade_date", ascending=False).reset_index(drop=True)[keep].copy()

    # 算环比变化(更易读)
    if "vol" in df.columns:
        df["vol_change"] = (
            -df["vol"].astype(float).diff().fillna(0).astype("Int64")
        )  # 反转因为 sort 是降序: 上一行(更新)的差是当天 - 昨天

    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(from_ts_date)
    return format_df_as_md(
        df,
        f"Northbound holdings for {ticker} (last {look_back_days}d, tushare)",
        max_rows=look_back_days,
    )


# === (4) 北向资金 — 市场总额 (pro.moneyflow_hsgt) ===


@tushare_retry()
def _fetch_moneyflow_hsgt(start: str, end: str) -> "pd.DataFrame":
    """市场总额北向资金:ggt_ss / ggt_sz / hgt / sgt / north_money / south_money."""
    pro = get_pro_client()
    return pro.moneyflow_hsgt(start_date=start, end_date=end)


def get_north_capital_overall_tushare(curr_date: str, look_back_days: int = 10) -> str:
    """Market-wide net N/S flow over the look-back window."""
    start, end = _date_window(curr_date, look_back_days)
    df = _fetch_moneyflow_hsgt(start, end)
    if df is None or df.empty:
        return (
            f"## Northbound net flow (last {look_back_days}d)\n\n"
            "_无北向资金市场总额数据 (tushare moneyflow_hsgt)._"
        )
    # 北向净流入 north_money = hgt + sgt;南向 south_money 用于对照
    keep = [c for c in ("trade_date", "hgt", "sgt", "north_money", "south_money") if c in df.columns]
    df = df.sort_values("trade_date", ascending=False).reset_index(drop=True)[keep].copy()
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(from_ts_date)
    return format_df_as_md(
        df,
        f"Northbound net flow (last {look_back_days}d, tushare)",
        max_rows=look_back_days,
    )


# === (5) 融资融券 — 个股 (pro.margin_detail) ===


@tushare_retry()
def _fetch_margin_detail(ts_code: str, start: str, end: str) -> "pd.DataFrame":
    """pro.margin_detail:per-stock 融资融券明细。

    字段:trade_date, ts_code, rzye(融资余额), rqye(融券余额), rzmre(融资买入额),
         rqyl(融券余量), rzche(融资偿还额), rqchl(融券偿还量), rqmcl(融券卖出量),
         rzrqye(融资融券余额合计)。
    """
    pro = get_pro_client()
    return pro.margin_detail(ts_code=ts_code, start_date=start, end_date=end)


def get_margin_trading_tushare(ticker: str, curr_date: str, look_back_days: int = 10) -> str:
    """Per-stock margin balances (融资余额 / 融券余量) over a recent window."""
    ts_code = to_ts_code(ticker)
    start, end = _date_window(curr_date, look_back_days)
    df = _fetch_margin_detail(ts_code, start, end)
    if df is None or df.empty:
        return (
            f"## Margin trading for {ticker} (last {look_back_days}d)\n\n"
            "_该股可能不在两融标的池 (tushare margin_detail)._"
        )

    # 数值是元、股,转成亿元 / 万股更可读
    df = df.sort_values("trade_date", ascending=False).reset_index(drop=True).copy()
    for col in ("rzye", "rzmre", "rzche", "rzrqye"):
        if col in df.columns:
            df[col] = (pd.to_numeric(df[col], errors="coerce") / 1e8).round(3)  # 元 → 亿元
    for col in ("rqyl", "rqmcl", "rqchl"):
        if col in df.columns:
            df[col] = (pd.to_numeric(df[col], errors="coerce") / 1e4).round(2)  # 股 → 万股
    # 把列重命名加单位,LLM 更容易读
    rename = {
        "rzye": "融资余额(亿)", "rqye": "融券余额", "rzmre": "融资买入额(亿)",
        "rqyl": "融券余量(万股)", "rzche": "融资偿还额(亿)", "rqchl": "融券偿还量(万股)",
        "rqmcl": "融券卖出量(万股)", "rzrqye": "两融余额合计(亿)",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(from_ts_date)
    return format_df_as_md(
        df,
        f"Margin trading for {ticker} (last {look_back_days}d, tushare)",
        max_rows=look_back_days,
    )


# === (6) 主力资金流向 — 个股 (pro.moneyflow) ===


@tushare_retry()
def _fetch_moneyflow(ts_code: str, start: str, end: str) -> "pd.DataFrame":
    """pro.moneyflow:个股逐日主力 / 大单 / 中单 / 小单买卖明细。

    字段:ts_code, trade_date,
         buy/sell_sm_vol/amount  小单
         buy/sell_md_vol/amount  中单
         buy/sell_lg_vol/amount  大单
         buy/sell_elg_vol/amount 超大单
         net_mf_vol/amount       净流入(主力净流入)
    """
    pro = get_pro_client()
    return pro.moneyflow(ts_code=ts_code, start_date=start, end_date=end)


def get_fund_flow_tushare(ticker: str, curr_date: str, look_back_days: int = 10) -> str:
    """Smart-money breakdown by lot size for the past `look_back_days`.

    Tushare returns granular buy/sell vol+amount for super-large / large /
    medium / small lots. We surface the net (buy - sell) amount for each
    lot tier — that's what humans want to see ("超大单净流入"等).
    """
    ts_code = to_ts_code(ticker)
    start, end = _date_window(curr_date, look_back_days)
    df = _fetch_moneyflow(ts_code, start, end)
    if df is None or df.empty:
        return (
            f"## Smart-money flow for {ticker} (last {look_back_days}d)\n\n"
            "_无资金流数据 (tushare moneyflow)._"
        )

    df = df.sort_values("trade_date", ascending=False).reset_index(drop=True).copy()

    # 计算每档净流入(amount = 万元 / vol = 万股)
    def _net(buy_col: str, sell_col: str) -> Optional["pd.Series"]:
        if buy_col in df.columns and sell_col in df.columns:
            return (
                pd.to_numeric(df[buy_col], errors="coerce")
                - pd.to_numeric(df[sell_col], errors="coerce")
            )
        return None

    result = pd.DataFrame()
    result["trade_date"] = df["trade_date"].apply(from_ts_date) if "trade_date" in df.columns else ""

    # 单位:万元 → 亿元
    for label, b, s in (
        ("超大单净(亿)", "buy_elg_amount", "sell_elg_amount"),
        ("大单净(亿)",  "buy_lg_amount",  "sell_lg_amount"),
        ("中单净(亿)",  "buy_md_amount",  "sell_md_amount"),
        ("小单净(亿)",  "buy_sm_amount",  "sell_sm_amount"),
    ):
        net = _net(b, s)
        if net is not None:
            result[label] = (net / 1e4).round(3)  # 万元 → 亿元

    # 主力净流入 = 超大单 + 大单
    if "超大单净(亿)" in result.columns and "大单净(亿)" in result.columns:
        result["主力净(亿)"] = (result["超大单净(亿)"] + result["大单净(亿)"]).round(3)
        # 把主力净放到前面
        cols = ["trade_date", "主力净(亿)"] + [c for c in result.columns
                                              if c not in ("trade_date", "主力净(亿)")]
        result = result[cols]

    return format_df_as_md(
        result,
        f"Smart-money flow for {ticker} (last {look_back_days}d, tushare)",
        max_rows=look_back_days,
    )
