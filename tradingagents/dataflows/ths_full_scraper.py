"""Comprehensive 同花顺 / A-share F10 + extras scraper.

Pulls every section that 同花顺's stock app shows on the F10 page plus a few
that the app surfaces elsewhere (interactive Q&A, attention metrics, block
trades). Each function returns a pandas.DataFrame; on failure it returns an
empty DataFrame and logs the exception — the caller can render a "_no data_"
placeholder rather than crashing the whole report.

All functions accept a 6-digit A-share ticker (e.g. '600031') and clear any
HTTP(S) proxy env vars for the duration of the call — akshare hits Chinese
endpoints (eastmoney.com, 10jqka.com.cn, sse.com.cn, cninfo.com.cn) that
cross-border proxies tend to break.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)


_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)


@contextmanager
def _no_proxy():
    saved = {k: os.environ.pop(k, None) for k in _PROXY_ENV_KEYS}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def _empty(reason: str = "") -> pd.DataFrame:
    df = pd.DataFrame()
    df.attrs["_skip_reason"] = reason
    return df


def _market_prefix(ticker: str) -> str:
    """6-digit ticker -> 'SH600031' / 'SZ000001' / 'BJ430047'."""
    if ticker.startswith(("60", "68", "9")):
        return f"SH{ticker}"
    if ticker.startswith(("4", "8")):
        return f"BJ{ticker}"
    return f"SZ{ticker}"


def _market_lower(ticker: str) -> str:
    """Returns 'sh' / 'sz' / 'bj' suitable for the `market` arg in some endpoints."""
    p = _market_prefix(ticker)[:2].lower()
    return p


def _safe(name: str, fn, *args, **kwargs) -> pd.DataFrame:
    """Run an akshare call inside the no-proxy ctx, swallow + log errors."""
    with _no_proxy():
        try:
            df = fn(*args, **kwargs)
            if df is None:
                return _empty("returned None")
            if not isinstance(df, pd.DataFrame):
                df = pd.DataFrame(df)
            return df
        except Exception as e:
            logger.warning("%s failed: %s: %s", name, type(e).__name__, e)
            return _empty(f"{type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 公司概况 / Company profile
# ─────────────────────────────────────────────────────────────────────────────

def get_company_profile(ticker: str) -> pd.DataFrame:
    """巨潮资讯公司档案 — 行业、注册地、主营、上市日、董秘、联系方式等 26 列。"""
    return _safe("stock_profile_cninfo", ak.stock_profile_cninfo, symbol=ticker)


def get_individual_info(ticker: str) -> pd.DataFrame:
    """东财行情卡片 — 总股本/流通股本/总市值/行业/上市时间。"""
    return _safe("stock_individual_info_em", ak.stock_individual_info_em, symbol=ticker)


def get_business_intro(ticker: str) -> pd.DataFrame:
    """同花顺主营介绍 — 主营业务、产品类型、经营范围。"""
    return _safe("stock_zyjs_ths", ak.stock_zyjs_ths, symbol=ticker)


def get_revenue_breakdown(ticker: str) -> pd.DataFrame:
    """东财主营构成 — 按行业/产品/地区拆分的收入、成本、毛利率，多个年度。"""
    return _safe("stock_zygc_em", ak.stock_zygc_em, symbol=_market_prefix(ticker))


def get_ipo_summary(ticker: str) -> pd.DataFrame:
    """巨潮上市信息 — 发行价、募资额、保荐机构。"""
    return _safe("stock_ipo_summary_cninfo", ak.stock_ipo_summary_cninfo, symbol=ticker)


# ─────────────────────────────────────────────────────────────────────────────
# 概念题材 / Concept & sector tags
# ─────────────────────────────────────────────────────────────────────────────

def get_concept_tags(ticker: str) -> pd.DataFrame:
    """东财个股热门概念关键词 — 直接的 ticker→概念映射，比反查概念板块成分快得多。"""
    return _safe(
        "stock_hot_keyword_em",
        ak.stock_hot_keyword_em,
        symbol=_market_prefix(ticker),
    )


# ─────────────────────────────────────────────────────────────────────────────
# 财务摘要 / Financial abstract
# ─────────────────────────────────────────────────────────────────────────────

def get_financial_abstract(ticker: str) -> pd.DataFrame:
    """同花顺财务摘要 — 报告期、净利润、扣非、营收、ROE、毛利率、负债率，所有季度。"""
    return _safe(
        "stock_financial_abstract_ths",
        ak.stock_financial_abstract_ths,
        symbol=ticker,
        indicator="按报告期",
    )


def get_profit_forecast(ticker: str) -> pd.DataFrame:
    """同花顺机构盈利预测 — 多家机构未来 2-3 年 EPS 预测的均值、最大、最小。"""
    return _safe(
        "stock_profit_forecast_ths",
        ak.stock_profit_forecast_ths,
        symbol=ticker,
        indicator="预测年报每股收益",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 股本结构 & 股东 / Share structure & holders
# ─────────────────────────────────────────────────────────────────────────────

def get_share_change(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """巨潮股本变动 — 每次配股/送转/解禁后的股本结构快照。"""
    return _safe(
        "stock_share_change_cninfo",
        ak.stock_share_change_cninfo,
        symbol=ticker,
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
    )


def get_shareholder_count(ticker: str) -> pd.DataFrame:
    """东财股东户数季度变化 — 户均持股、户数环比、对应区间股价涨跌幅。"""
    return _safe(
        "stock_zh_a_gdhs_detail_em",
        ak.stock_zh_a_gdhs_detail_em,
        symbol=ticker,
    )


def get_shareholder_change(ticker: str) -> pd.DataFrame:
    """同花顺大股东增减持 — 公告日、变动股东、数量、均价、剩余持股。"""
    return _safe(
        "stock_shareholder_change_ths",
        ak.stock_shareholder_change_ths,
        symbol=ticker,
    )


def get_management_change(ticker: str) -> pd.DataFrame:
    """同花顺高管变动/持股变化 — 142+ 条历史。"""
    return _safe(
        "stock_management_change_ths",
        ak.stock_management_change_ths,
        symbol=ticker,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 大事提醒 / Major events
# ─────────────────────────────────────────────────────────────────────────────

def get_restricted_release(ticker: str) -> pd.DataFrame:
    """东财限售解禁排期 — 解禁日、股东数、解禁数量、占总市值比例。"""
    return _safe(
        "stock_restricted_release_queue_em",
        ak.stock_restricted_release_queue_em,
        symbol=ticker,
    )


def get_dividend_history_em(ticker: str) -> pd.DataFrame:
    """东财分红送转明细 — 报告期、披露日、送转比例、现金分红比例、股息率。"""
    return _safe(
        "stock_fhps_detail_em",
        ak.stock_fhps_detail_em,
        symbol=ticker,
    )


def get_dividend_history_ths(ticker: str) -> pd.DataFrame:
    """同花顺分红方案历史 — 包含董事会日期、登记日、除权日、分红总额（更完整）。"""
    return _safe(
        "stock_fhps_detail_ths",
        ak.stock_fhps_detail_ths,
        symbol=ticker,
    )


def get_pledge_ratio(ticker: str, curr_date: str) -> pd.DataFrame:
    """东财股权质押比例（全市场快照后过滤）— 质押笔数、质押股数、质押比例。

    ``stock_gpzy_pledge_ratio_em`` 是周度快照，传入日期需为周五（trading week
    cutoff）。我们从 curr_date 往前找最近的周五。
    """
    d = datetime.strptime(curr_date, "%Y-%m-%d")
    while d.weekday() != 4:  # 4 = Friday
        d -= timedelta(days=1)
    df = _safe(
        "stock_gpzy_pledge_ratio_em",
        ak.stock_gpzy_pledge_ratio_em,
        date=d.strftime("%Y%m%d"),
    )
    if df.empty:
        return df
    code_col = next((c for c in df.columns if "代码" in c), None)
    if not code_col:
        return df
    hit = df[df[code_col].astype(str).str.zfill(6) == ticker].copy()
    hit.attrs["_snapshot_date"] = d.strftime("%Y-%m-%d")
    return hit


# ─────────────────────────────────────────────────────────────────────────────
# 业绩预告 / Earnings preview & express
# ─────────────────────────────────────────────────────────────────────────────

_QUARTER_ENDS_DDMM = ("0331", "0630", "0930", "1231")


def _recent_quarter_ends(curr_date: str, n: int = 8) -> list[str]:
    """Return the n most recent quarter-end dates as YYYYMMDD strings, latest first."""
    today = datetime.strptime(curr_date, "%Y-%m-%d")
    out = []
    year = today.year
    for _ in range(n + 4):
        for mmdd in reversed(_QUARTER_ENDS_DDMM):
            d = datetime.strptime(f"{year}{mmdd}", "%Y%m%d")
            if d <= today:
                out.append(d.strftime("%Y%m%d"))
                if len(out) >= n:
                    return out
        year -= 1
    return out


def _filter_by_ticker(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if df.empty:
        return df
    code_col = next((c for c in df.columns if "代码" in c or "code" in c.lower()), None)
    if not code_col:
        return df
    return df[df[code_col].astype(str).str.zfill(6) == ticker].copy()


def get_earnings_forecast(ticker: str, curr_date: str, max_quarters: int = 8) -> pd.DataFrame:
    """东财业绩预告（按报告期快照后过滤）— 预告净利润上下限、变动幅度。"""
    frames = []
    for q in _recent_quarter_ends(curr_date, max_quarters):
        df = _safe("stock_yjyg_em", ak.stock_yjyg_em, date=q)
        if not df.empty:
            hit = _filter_by_ticker(df, ticker)
            if not hit.empty:
                hit = hit.copy()
                hit["_报告期"] = pd.to_datetime(q).strftime("%Y-%m-%d")
                frames.append(hit)
    if not frames:
        return _empty("no preview in recent quarters")
    return pd.concat(frames, ignore_index=True)


def get_earnings_express(ticker: str, curr_date: str, max_quarters: int = 4) -> pd.DataFrame:
    """东财业绩快报 — 期末营收、利润、ROE 等快报披露。"""
    frames = []
    for q in _recent_quarter_ends(curr_date, max_quarters):
        df = _safe("stock_yjkb_em", ak.stock_yjkb_em, date=q)
        if not df.empty:
            hit = _filter_by_ticker(df, ticker)
            if not hit.empty:
                hit = hit.copy()
                hit["_报告期"] = pd.to_datetime(q).strftime("%Y-%m-%d")
                frames.append(hit)
    if not frames:
        return _empty("no express in recent quarters")
    return pd.concat(frames, ignore_index=True)


def get_earnings_report(ticker: str, curr_date: str, max_quarters: int = 4) -> pd.DataFrame:
    """东财业绩报告 — 正式季报披露的关键 KPI。"""
    frames = []
    for q in _recent_quarter_ends(curr_date, max_quarters):
        df = _safe("stock_yjbb_em", ak.stock_yjbb_em, date=q)
        if not df.empty:
            hit = _filter_by_ticker(df, ticker)
            if not hit.empty:
                hit = hit.copy()
                hit["_报告期"] = pd.to_datetime(q).strftime("%Y-%m-%d")
                frames.append(hit)
    if not frames:
        return _empty("no report in recent quarters")
    return pd.concat(frames, ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# 机构调研 / Institutional site visits
# ─────────────────────────────────────────────────────────────────────────────

def get_institutional_visits(
    ticker: str, curr_date: str, look_back_days: int = 180
) -> pd.DataFrame:
    """东财机构调研统计（单次 API + 按 ticker 过滤）— 接待日、机构数、调研方式、人员。

    使用 ``stock_jgdy_tj_em(date)`` —— 该端点返回从 ``date`` 至今的累计明细
    （单次调用即可），比按日迭代 ``stock_jgdy_detail_em`` 快 100 倍。
    """
    start = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=look_back_days)).strftime("%Y%m%d")
    df = _safe("stock_jgdy_tj_em", ak.stock_jgdy_tj_em, date=start)
    if df.empty:
        return df
    return _filter_by_ticker(df, ticker)


# ─────────────────────────────────────────────────────────────────────────────
# 大宗交易 / Block trade
# ─────────────────────────────────────────────────────────────────────────────

def get_block_trade(
    ticker: str, curr_date: str, look_back_days: int = 90
) -> pd.DataFrame:
    """东财大宗交易明细 — 单次 API 调用获取整段区间，再按 ticker 过滤。

    ``stock_dzjy_mrmx(symbol="A股", start_date, end_date)`` 接受 6 周区间一次
    返回，<1s 完成。``symbol`` 的合法值为 {'A股','B股','基金','债券'}（不是参与
    方类型）。
    """
    end = datetime.strptime(curr_date, "%Y-%m-%d")
    start = end - timedelta(days=look_back_days)
    df = _safe(
        "stock_dzjy_mrmx",
        ak.stock_dzjy_mrmx,
        symbol="A股",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if df.empty:
        return df
    code_col = next((c for c in df.columns if "证券代码" in c or ("代码" in c and "简" not in c)), None)
    if not code_col:
        return df
    hit = df[df[code_col].astype(str).str.zfill(6) == ticker].copy()
    if hit.empty:
        return _empty(f"no block trade in last {look_back_days}d")
    return hit


# ─────────────────────────────────────────────────────────────────────────────
# 资金 / Capital flow
# ─────────────────────────────────────────────────────────────────────────────

def get_individual_fund_flow(ticker: str) -> pd.DataFrame:
    """东财个股资金流向（按日）— 主力净流入、超大单、大单、中单、小单。"""
    return _safe(
        "stock_individual_fund_flow",
        ak.stock_individual_fund_flow,
        stock=ticker,
        market=_market_lower(ticker),
    )


def get_northbound_holding(ticker: str) -> pd.DataFrame:
    """东财北向持股历史 — 持股日期、持股数量、市值、占A股比例、当日增减。"""
    return _safe(
        "stock_hsgt_individual_em",
        ak.stock_hsgt_individual_em,
        symbol=ticker,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 互动易 / Investor Q&A
# ─────────────────────────────────────────────────────────────────────────────

def get_investor_qa(ticker: str) -> pd.DataFrame:
    """互动易/上证e互动 投资者问答 — 沪市走 sseinfo，深市走 cninfo。"""
    if _market_prefix(ticker).startswith("SH"):
        return _safe("stock_sns_sseinfo", ak.stock_sns_sseinfo, symbol=ticker)
    return _safe("stock_irm_cninfo", ak.stock_irm_cninfo, symbol=ticker)


# ─────────────────────────────────────────────────────────────────────────────
# 研报 / Research reports
# ─────────────────────────────────────────────────────────────────────────────

def get_research_reports(ticker: str) -> pd.DataFrame:
    """东财研报列表 — 标题、机构、评级、目标价、2026/2027 盈利预测。"""
    return _safe(
        "stock_research_report_em",
        ak.stock_research_report_em,
        symbol=ticker,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 新闻 / News
# ─────────────────────────────────────────────────────────────────────────────

def get_stock_news(ticker: str) -> pd.DataFrame:
    """东财个股新闻 — 已经在项目中通过 akshare_news.get_news_akshare 使用。"""
    return _safe("stock_news_em", ak.stock_news_em, symbol=ticker)


# ═════════════════════════════════════════════════════════════════════════════
# V2 EXTENSIONS — fuller coverage of 同花顺 single-stock interface
# ═════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# 财务三表 + 主要财务指标
# ─────────────────────────────────────────────────────────────────────────────

# 资产负债表 / 利润表 / 现金流量表 columns are 200-320 wide. We pick a curated
# subset of high-signal columns for display; the full DataFrame is still
# returned so downstream consumers can slice differently.

_BALANCE_KEY_COLS = [
    "SECURITY_NAME_ABBR", "REPORT_DATE",
    "TOTAL_ASSETS",                      # 总资产
    "TOTAL_LIABILITIES",                 # 总负债
    "TOTAL_EQUITY",                      # 所有者权益
    "MONETARYFUNDS",                     # 货币资金
    "ACCOUNTS_RECE",                     # 应收账款
    "INVENTORY",                         # 存货
    "FIXED_ASSET",                       # 固定资产
    "INTANGIBLE_ASSET",                  # 无形资产
    "GOODWILL",                          # 商誉
    "SHORT_LOAN",                        # 短期借款
    "LONG_LOAN",                         # 长期借款
    "ACCOUNTS_PAYABLE",                  # 应付账款
]

_INCOME_KEY_COLS = [
    "SECURITY_NAME_ABBR", "REPORT_DATE",
    "TOTAL_OPERATE_INCOME",              # 营业总收入
    "OPERATE_INCOME",                    # 营业收入
    "OPERATE_COST",                      # 营业成本
    "SALE_EXPENSE",                      # 销售费用
    "MANAGE_EXPENSE",                    # 管理费用
    "RESEARCH_EXPENSE",                  # 研发费用
    "FINANCE_EXPENSE",                   # 财务费用
    "OPERATE_PROFIT",                    # 营业利润
    "TOTAL_PROFIT",                      # 利润总额
    "NETPROFIT",                         # 净利润
    "PARENT_NETPROFIT",                  # 归母净利润
    "DEDUCT_PARENT_NETPROFIT",           # 扣非归母
    "BASIC_EPS",                         # 基本 EPS
]

_CASHFLOW_KEY_COLS = [
    "SECURITY_NAME_ABBR", "REPORT_DATE",
    "SALES_SERVICES",                    # 销售商品收现
    "NETCASH_OPERATE",                   # 经营活动现金流量净额
    "CONSTRUCT_LONG_ASSET",              # 购建固定资产支出
    "NETCASH_INVEST",                    # 投资活动现金流量净额
    "ACCEPT_INVEST_CASH",                # 吸收投资
    "NETCASH_FINANCE",                   # 筹资活动现金流量净额
    "END_BALANCE",                       # 期末现金余额
]


def _trim_cols(df: pd.DataFrame, keep_cols: list[str], head: int = 8) -> pd.DataFrame:
    """Return a slice with only `keep_cols` (when they exist) and at most `head` rows."""
    if df is None or df.empty:
        return df
    cols = [c for c in keep_cols if c in df.columns]
    if not cols:
        return df.head(head)
    out = df[cols].head(head).copy()
    return out


def get_balance_sheet(ticker: str, latest_n: int = 8) -> pd.DataFrame:
    """东财资产负债表（最近 N 个报告期，关键列）."""
    df = _safe(
        "stock_balance_sheet_by_report_em",
        ak.stock_balance_sheet_by_report_em,
        symbol=_market_prefix(ticker),
    )
    return _trim_cols(df, _BALANCE_KEY_COLS, head=latest_n)


def get_income_statement(ticker: str, latest_n: int = 8) -> pd.DataFrame:
    """东财利润表（最近 N 个报告期，关键列）."""
    df = _safe(
        "stock_profit_sheet_by_report_em",
        ak.stock_profit_sheet_by_report_em,
        symbol=_market_prefix(ticker),
    )
    return _trim_cols(df, _INCOME_KEY_COLS, head=latest_n)


def get_cash_flow(ticker: str, latest_n: int = 8) -> pd.DataFrame:
    """东财现金流量表（最近 N 个报告期，关键列）."""
    df = _safe(
        "stock_cash_flow_sheet_by_report_em",
        ak.stock_cash_flow_sheet_by_report_em,
        symbol=_market_prefix(ticker),
    )
    return _trim_cols(df, _CASHFLOW_KEY_COLS, head=latest_n)


def get_financial_indicators(ticker: str, start_year: str = "2018") -> pd.DataFrame:
    """新浪历年财务指标（86 列）— 包含 ROE、ROA、负债率、毛利率、周转率等。"""
    return _safe(
        "stock_financial_analysis_indicator",
        ak.stock_financial_analysis_indicator,
        symbol=ticker,
        start_year=start_year,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 十大股东 / 十大流通股东 / 一致行动人 / 历史股东变动
# ─────────────────────────────────────────────────────────────────────────────

def _latest_quarter_end(curr_date: str) -> str:
    """Return YYYYMMDD of the most recent quarter-end on or before curr_date."""
    d = datetime.strptime(curr_date, "%Y-%m-%d")
    # Walk backward 1 day at a time until we hit a quarter end. Cheap.
    for _ in range(95):
        if (d.month, d.day) in ((3, 31), (6, 30), (9, 30), (12, 31)):
            return d.strftime("%Y%m%d")
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def get_top10_holders(ticker: str, curr_date: str) -> pd.DataFrame:
    """东财十大股东（最近披露的报告期）."""
    q = _latest_quarter_end(curr_date)
    # 东财端点要小写市场前缀
    return _safe(
        "stock_gdfx_top_10_em",
        ak.stock_gdfx_top_10_em,
        symbol=_market_prefix(ticker).lower(),
        date=q,
    )


def get_top10_free_holders(ticker: str, curr_date: str) -> pd.DataFrame:
    """东财十大流通股东（最近披露的报告期）."""
    q = _latest_quarter_end(curr_date)
    return _safe(
        "stock_gdfx_free_top_10_em",
        ak.stock_gdfx_free_top_10_em,
        symbol=_market_prefix(ticker).lower(),
        date=q,
    )


def get_main_holder_history(ticker: str) -> pd.DataFrame:
    """巨潮主要股东（多季度历史，可比对持仓变化）."""
    return _safe(
        "stock_main_stock_holder",
        ak.stock_main_stock_holder,
        stock=ticker,
    )


def get_circulate_holder_history(ticker: str) -> pd.DataFrame:
    """巨潮流通股东（多季度历史）."""
    return _safe(
        "stock_circulate_stock_holder",
        ak.stock_circulate_stock_holder,
        symbol=ticker,
    )


def get_concerted_action(ticker: str, curr_date: str) -> pd.DataFrame:
    """东财一致行动人（按季度快照过滤）— 实控人 / 一致行动人组合及合计持股比例。"""
    q = _latest_quarter_end(curr_date)
    df = _safe("stock_yzxdr_em", ak.stock_yzxdr_em, date=q)
    if df.empty:
        return df
    return _filter_by_ticker(df, ticker)


# ─────────────────────────────────────────────────────────────────────────────
# 估值 / Valuation
# ─────────────────────────────────────────────────────────────────────────────

def get_valuation_daily(ticker: str, last_n_days: int = 250) -> pd.DataFrame:
    """东财日频估值（PE-TTM / PE静 / PB / PEG / 市现率 / 市销率 / 总市值 / 流通市值）."""
    df = _safe("stock_value_em", ak.stock_value_em, symbol=ticker)
    if df.empty:
        return df
    return df.tail(last_n_days).reset_index(drop=True)


def get_valuation_baidu_pe(ticker: str) -> pd.DataFrame:
    """百度近一年 PE-TTM 历史（独立来源，可与东财对照）."""
    return _safe(
        "stock_zh_valuation_baidu",
        ak.stock_zh_valuation_baidu,
        symbol=ticker,
        indicator="市盈率(TTM)",
        period="近一年",
    )


def get_valuation_baidu_pb(ticker: str) -> pd.DataFrame:
    """百度近一年 PB 历史."""
    return _safe(
        "stock_zh_valuation_baidu",
        ak.stock_zh_valuation_baidu,
        symbol=ticker,
        indicator="市净率",
        period="近一年",
    )


# ─────────────────────────────────────────────────────────────────────────────
# 行情 / Market data
# ─────────────────────────────────────────────────────────────────────────────

def get_daily_kline(ticker: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
    """新浪日 K 线（OHLCV + 换手率，前复权）— 比 push2.eastmoney 稳定得多。"""
    return _safe(
        "stock_zh_a_daily",
        ak.stock_zh_a_daily,
        symbol=_market_prefix(ticker).lower(),
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust=adjust,
    )


def get_intraday_kline(ticker: str, period: str = "60", adjust: str = "qfq") -> pd.DataFrame:
    """新浪分钟 K 线 — period ∈ {1,5,15,30,60} 分钟。默认 60min，给图表/震荡范围用。"""
    df = _safe(
        "stock_zh_a_minute",
        ak.stock_zh_a_minute,
        symbol=_market_prefix(ticker).lower(),
        period=period,
        adjust=adjust,
    )
    if df.empty:
        return df
    # Sina 偶尔返回 0-row 但 columns 正常；保留最后 ~60 根
    return df.tail(60).reset_index(drop=True)


# ─────────────────────────────────────────────────────────────────────────────
# 千股千评 / Stock comment
# ─────────────────────────────────────────────────────────────────────────────

def get_stock_comment(ticker: str) -> pd.DataFrame:
    """东财千股千评 — 综合得分、主力成本、机构参与度、关注指数、目前排名。"""
    df = _safe("stock_comment_em", ak.stock_comment_em)
    if df.empty:
        return df
    code_col = next((c for c in df.columns if c == "代码"), None)
    if not code_col:
        return df
    return df[df[code_col].astype(str).str.zfill(6) == ticker].copy()


def get_comment_focus_history(ticker: str) -> pd.DataFrame:
    """东财关注指数历史（最近 30 个交易日）."""
    return _safe(
        "stock_comment_detail_scrd_focus_em",
        ak.stock_comment_detail_scrd_focus_em,
        symbol=ticker,
    )


def get_comment_score_history(ticker: str) -> pd.DataFrame:
    """东财综合评分历史（最近 30 个交易日）."""
    return _safe(
        "stock_comment_detail_zhpj_lspf_em",
        ak.stock_comment_detail_zhpj_lspf_em,
        symbol=ticker,
    )


def get_comment_institution_participation(ticker: str) -> pd.DataFrame:
    """东财机构参与度历史（最近 ~40 个交易日）."""
    return _safe(
        "stock_comment_detail_zlkp_jgcyd_em",
        ak.stock_comment_detail_zlkp_jgcyd_em,
        symbol=ticker,
    )


def get_comment_participation_desire(ticker: str) -> pd.DataFrame:
    """东财参与意愿（最近 5 个交易日）."""
    return _safe(
        "stock_comment_detail_scrd_desire_em",
        ak.stock_comment_detail_scrd_desire_em,
        symbol=ticker,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 雪球热度
# ─────────────────────────────────────────────────────────────────────────────

def get_xueqiu_hot_follow(ticker: str) -> pd.DataFrame:
    """雪球最热门关注列表（5000+ 票）— 过滤本 ticker，得到当前关注人数。"""
    df = _safe("stock_hot_follow_xq", ak.stock_hot_follow_xq, symbol="最热门")
    if df.empty:
        return df
    code_col = next((c for c in df.columns if "代码" in c), None)
    if not code_col:
        return df
    sym = _market_prefix(ticker)
    return df[df[code_col].astype(str).str.upper() == sym].copy()


def get_xueqiu_hot_tweet(ticker: str) -> pd.DataFrame:
    """雪球最热门讨论 — 同样过滤本 ticker."""
    df = _safe("stock_hot_tweet_xq", ak.stock_hot_tweet_xq, symbol="最热门")
    if df.empty:
        return df
    code_col = next((c for c in df.columns if "代码" in c), None)
    if not code_col:
        return df
    sym = _market_prefix(ticker)
    return df[df[code_col].astype(str).str.upper() == sym].copy()


def get_xueqiu_hot_deal(ticker: str) -> pd.DataFrame:
    """雪球最热门成交（雪球用户买入/卖出最活跃的股票） — 过滤本 ticker."""
    df = _safe("stock_hot_deal_xq", ak.stock_hot_deal_xq, symbol="最热门")
    if df.empty:
        return df
    code_col = next((c for c in df.columns if "代码" in c), None)
    if not code_col:
        return df
    sym = _market_prefix(ticker)
    return df[df[code_col].astype(str).str.upper() == sym].copy()


# ─────────────────────────────────────────────────────────────────────────────
# 业绩说明会
# ─────────────────────────────────────────────────────────────────────────────

def get_performance_briefing(ticker: str, curr_date: str, max_quarters: int = 4) -> pd.DataFrame:
    """东财业绩说明会（按季度快照过滤）— 首次预约时间、变更日期等。"""
    frames = []
    for q in _recent_quarter_ends(curr_date, max_quarters):
        df = _safe("stock_yysj_em", ak.stock_yysj_em, symbol="沪深A股", date=q)
        if not df.empty:
            hit = _filter_by_ticker(df, ticker)
            if not hit.empty:
                hit = hit.copy()
                hit["_报告期"] = pd.to_datetime(q).strftime("%Y-%m-%d")
                frames.append(hit)
    if not frames:
        return _empty("no briefing schedule")
    return pd.concat(frames, ignore_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# AI 投资要点 / Synthesis (no LLM call — rule-based from pulled data)
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_num(v, fmt: str = ".2f") -> str:
    """Format a numeric value safely; return '?' for None/NaN/non-numeric."""
    try:
        if v is None:
            return "?"
        f = float(v)
        if f != f:  # NaN
            return "?"
        return format(f, fmt)
    except (TypeError, ValueError):
        return "?"


def synthesize_highlights(sections: dict[str, pd.DataFrame], ticker: str) -> list[str]:
    """Compose a 同花顺-style "AI 投资要点" from the pulled sections.

    Rule-based — no LLM. Each rule peeks at one section and emits 0–N bullet
    points. Pulls everything together at the top of the HTML so the user gets
    a glance-level summary before diving into the tables.
    """
    bullets: list[str] = []

    # ─ 估值 ────────────────────────────────────────────────────────────────
    val = sections.get("valuation_daily")
    if val is not None and not val.empty:
        latest = val.iloc[-1]
        pe = _fmt_num(latest.get("PE(TTM)"), ".1f")
        pb = _fmt_num(latest.get("市净率"), ".2f")
        mc = _fmt_num((latest.get("总市值") or 0) / 1e8, ".0f")
        bullets.append(f"💰 估值：当前 PE-TTM {pe}x，PB {pb}x，总市值 {mc} 亿。")

    # ─ 概念题材 ──────────────────────────────────────────────────────────────
    concepts = sections.get("concept_tags")
    if concepts is not None and not concepts.empty:
        top = concepts.head(3)
        tags = " / ".join(f"{r['概念名称']}({r['热度']})" for _, r in top.iterrows())
        bullets.append(f"🏷️ 题材：Top 概念 → {tags}")

    # ─ 千股千评 ──────────────────────────────────────────────────────────────
    comment = sections.get("stock_comment")
    if comment is not None and not comment.empty:
        c = comment.iloc[0]
        bullets.append(
            f"📊 千股千评：综合得分 {_fmt_num(c.get('综合得分'), '.1f')}，"
            f"主力成本 {_fmt_num(c.get('主力成本'), '.2f')} 元，"
            f"机构参与度 {_fmt_num(c.get('机构参与度'), '.2f')}%，"
            f"目前排名 {c.get('目前排名', '?')}"
        )

    # ─ 一致行动人 ────────────────────────────────────────────────────────────
    ca = sections.get("concerted_action")
    if ca is not None and not ca.empty:
        row = ca.iloc[0]
        bullets.append(
            f"👥 一致行动人：{row.get('一致行动人', '?')}，合计持股比例 {row.get('持股比例', '?')}%"
        )

    # ─ 限售解禁 ──────────────────────────────────────────────────────────────
    # Only flag FUTURE unlocks (the source mixes past + future). Sort ascending.
    rr = sections.get("restricted_release")
    if rr is not None and not rr.empty:
        try:
            rr2 = rr.copy()
            rr2["_dt"] = pd.to_datetime(rr2["解禁时间"], errors="coerce")
            today = pd.Timestamp(datetime.now().date())
            future = rr2[rr2["_dt"] >= today].sort_values("_dt").head(2)
        except Exception:
            future = rr.head(2)
        for _, row in future.iterrows():
            amt = row.get("实际解禁数量") or row.get("解禁数量") or 0
            try:
                amt_y = float(amt) / 1e8
            except (TypeError, ValueError):
                amt_y = 0
            bullets.append(
                f"⏰ 待解禁：{row.get('解禁时间', '?')} 解禁 {amt_y:.2f} 亿股，"
                f"占总市值 {_fmt_num(row.get('占总市值比例'), '.3f')}%"
            )

    # ─ 北向资金 ──────────────────────────────────────────────────────────────
    nb = sections.get("northbound")
    if nb is not None and not nb.empty:
        recent = nb.head(20)
        if "今日增持股数" in recent.columns:
            net_add = recent["今日增持股数"].astype(float).sum()
            bullets.append(
                f"🌐 北向：近 20 个交易日净增持 {net_add/1e6:.1f} 万股（正=增持，负=减持）"
            )

    # ─ 研报 ─────────────────────────────────────────────────────────────────
    rr_reports = sections.get("research_reports")
    if rr_reports is not None and not rr_reports.empty:
        buy = rr_reports[rr_reports.get("东财评级", pd.Series([])).astype(str).str.contains("买入|增持", na=False)]
        bullets.append(
            f"📈 研报：近期 {len(rr_reports)} 份研报，其中 {len(buy)} 份给"
            f"\"买入/增持\"评级"
        )

    # ─ 盈利预测 ──────────────────────────────────────────────────────────────
    pf = sections.get("profit_forecast")
    if pf is not None and not pf.empty:
        for _, row in pf.iterrows():
            year = row.get("年度")
            mean_eps = row.get("均值")
            n_inst = row.get("预测机构数")
            bullets.append(f"🔮 {year} 年 {n_inst} 家机构平均预测 EPS = {mean_eps} 元")

    # ─ 互动易 ───────────────────────────────────────────────────────────────
    qa = sections.get("investor_qa")
    if qa is not None and not qa.empty:
        bullets.append(
            f"💬 投资者互动：上证 e 互动有 {len(qa)} 条近期问答（含官方回答）"
        )

    # ─ 雪球 ─────────────────────────────────────────────────────────────────
    xq = sections.get("xueqiu_hot_follow")
    if xq is not None and not xq.empty:
        follow = xq.iloc[0].get("关注", 0)
        bullets.append(f"🐂 雪球：被 {int(follow):,} 个用户关注（最热门列表）")

    # ─ 财务高频信号 ────────────────────────────────────────────────────────
    # financial_abstract 按报告期升序排列，最新一期在最后一行
    fin = sections.get("financial_abstract")
    if fin is not None and not fin.empty:
        try:
            fin2 = fin.copy()
            fin2["_dt"] = pd.to_datetime(fin2["报告期"], errors="coerce")
            fin2 = fin2.sort_values("_dt", ascending=False).dropna(subset=["_dt"])
            latest = fin2.iloc[0] if not fin2.empty else fin.iloc[-1]
        except Exception:
            latest = fin.iloc[-1]
        def _v(field):
            v = latest.get(field, "?")
            return "?" if (v is False or v is None or str(v) == "False") else v
        bullets.append(
            f"📑 最新季报（{_v('报告期')}）：营收 {_v('营业总收入')}（YoY {_v('营业总收入同比增长率')}），"
            f"归母 {_v('净利润')}（YoY {_v('净利润同比增长率')}），"
            f"毛利率 {_v('销售毛利率')}，ROE {_v('净资产收益率')}"
        )

    # ─ 业绩报告（更精确的最新季报数字）──────────────────────────────────────
    er = sections.get("earnings_report")
    if er is not None and not er.empty:
        latest = er.iloc[0]
        rev = latest.get("营业总收入-营业总收入") or latest.get("营业收入")
        np_ = latest.get("净利润") or latest.get("净利润-净利润")
        roe = latest.get("净资产收益率") or latest.get("加权平均净资产收益率")
        period = latest.get("_报告期") or latest.get("报告期") or "?"
        bullets.append(
            f"📊 业绩报告（{period}）：营收 {rev}，归母净利润 {np_}，ROE {roe}"
        )

    # ─ 资产负债 ────────────────────────────────────────────────────────────
    bs = sections.get("balance_sheet")
    if bs is not None and not bs.empty:
        latest = bs.iloc[0]
        ta = _fmt_num((latest.get("TOTAL_ASSETS") or 0)/1e8, ".0f")
        tl = _fmt_num((latest.get("TOTAL_LIABILITIES") or 0)/1e8, ".0f")
        eq = _fmt_num((latest.get("TOTAL_EQUITY") or 0)/1e8, ".0f")
        cash = _fmt_num((latest.get("MONETARYFUNDS") or 0)/1e8, ".1f")
        bullets.append(
            f"📚 资产负债：总资产 {ta} 亿，总负债 {tl} 亿，所有者权益 {eq} 亿，货币资金 {cash} 亿"
        )

    # ─ 现金流 ──────────────────────────────────────────────────────────────
    cf = sections.get("cash_flow")
    if cf is not None and not cf.empty:
        latest = cf.iloc[0]
        op = _fmt_num((latest.get("NETCASH_OPERATE") or 0)/1e8, ".1f")
        inv = _fmt_num((latest.get("NETCASH_INVEST") or 0)/1e8, ".1f")
        fin_ = _fmt_num((latest.get("NETCASH_FINANCE") or 0)/1e8, ".1f")
        bullets.append(
            f"💵 现金流：经营 {op} 亿，投资 {inv} 亿，筹资 {fin_} 亿"
        )

    # ─ 大股东增持/减持近况 ─────────────────────────────────────────────────
    sc = sections.get("shareholder_change")
    if sc is not None and not sc.empty:
        recent = sc.head(3)
        # Count net direction
        net_buy, net_sell = 0, 0
        for _, row in recent.iterrows():
            vol = row.get("变动数量", "")
            if isinstance(vol, str) and "增" in vol:
                net_buy += 1
            elif isinstance(vol, str) and "减" in vol:
                net_sell += 1
        if net_buy or net_sell:
            bullets.append(f"🪙 大股东动作：近 3 次公告 {net_buy} 次增持 / {net_sell} 次减持")

    return bullets


# ═════════════════════════════════════════════════════════════════════════════
# AGENT TOOL ADAPTERS — string-returning wrappers wired into route_to_vendor
# ═════════════════════════════════════════════════════════════════════════════
#
# The DataFrame-returning functions above are great for batch reports / HTML
# rendering, but the LangChain @tool layer needs string output for the LLM
# to consume. These adapters:
#   * normalise the ticker (the agent passes "600031.SH"; we want "600031")
#   * call the underlying DataFrame fn
#   * render with the shared format_df_as_md helper
#   * accept (ticker, curr_date) uniformly so every @tool has the same signature
#
# curr_date is required by some scrapers (top10_holders, pledge_ratio, etc.)
# but unused by others (concept_tags, profit_forecast). For uniformity we
# always accept it; functions that don't need it ignore the arg.

from .akshare_common import format_df_as_md, to_ak_symbol


def _norm(ticker: str) -> str:
    """600031.SH | sh600031 | 600031 -> 600031."""
    if not ticker:
        return ticker
    return to_ak_symbol(ticker)


# ─── Fundamentals adapters ──────────────────────────────────────────────────

def get_financial_indicators_md(ticker: str, curr_date: str = None) -> str:
    """86 项财务指标（历年）— 给 fundamentals_analyst 用。"""
    code = _norm(ticker)
    df = get_financial_indicators(code, start_year="2018")
    return format_df_as_md(df, f"Financial indicators (86 项, 自 2018) for {ticker}", max_rows=20)


def get_revenue_breakdown_md(ticker: str, curr_date: str = None) -> str:
    """主营构成（按行业 / 产品 / 地区拆分）— 给 fundamentals_analyst 用。"""
    code = _norm(ticker)
    df = get_revenue_breakdown(code)
    return format_df_as_md(df, f"Revenue breakdown for {ticker}", max_rows=40)


def get_dividend_history_md(ticker: str, curr_date: str = None) -> str:
    """分红方案历史（同花顺，含登记日 / 除权日 / 实施日 / 分红总额）。"""
    code = _norm(ticker)
    df = get_dividend_history_ths(code)
    return format_df_as_md(df, f"Dividend history for {ticker}", max_rows=20)


def get_profit_forecast_md(ticker: str, curr_date: str = None) -> str:
    """机构盈利预测（参与机构数、未来 EPS 均值 / 最大 / 最小、行业平均）。"""
    code = _norm(ticker)
    df = get_profit_forecast(code)
    return format_df_as_md(df, f"Analyst profit forecast for {ticker}", max_rows=10)


# ─── Sentiment / event adapters ─────────────────────────────────────────────

def get_concept_tags_md(ticker: str, curr_date: str = None) -> str:
    """概念题材（带热度）— 给 sentiment_analyst 用。"""
    code = _norm(ticker)
    df = get_concept_tags(code)
    return format_df_as_md(df, f"Concept tags (with heat score) for {ticker}", max_rows=30)


def get_stock_comment_md(ticker: str, curr_date: str = None) -> str:
    """千股千评（综合得分、主力成本、机构参与度、关注指数、排名）。"""
    code = _norm(ticker)
    df = get_stock_comment(code)
    return format_df_as_md(df, f"Stock comment (千股千评) for {ticker}", max_rows=5)


def get_xueqiu_hot_md(ticker: str, curr_date: str = None) -> str:
    """雪球热度（关注 / 讨论 / 成交 三榜的当前位置）。"""
    code = _norm(ticker)
    follow = get_xueqiu_hot_follow(code)
    tweet = get_xueqiu_hot_tweet(code)
    deal = get_xueqiu_hot_deal(code)
    return "\n\n".join([
        format_df_as_md(follow, f"Xueqiu hot follow (关注) for {ticker}", 5),
        format_df_as_md(tweet,  f"Xueqiu hot tweet (讨论) for {ticker}", 5),
        format_df_as_md(deal,   f"Xueqiu hot deal (成交) for {ticker}", 5),
    ])


def get_investor_qa_md(ticker: str, curr_date: str = None) -> str:
    """投资者互动（上证 e 互动 / 巨潮互动易）— 含官方回答。"""
    code = _norm(ticker)
    df = get_investor_qa(code)
    return format_df_as_md(df, f"Investor Q&A for {ticker}", max_rows=15)


def get_performance_briefing_md(ticker: str, curr_date: str = None) -> str:
    """业绩说明会日程（首次预约、变更日期）— 事件驱动信号。"""
    code = _norm(ticker)
    if curr_date is None:
        curr_date = datetime.now().strftime("%Y-%m-%d")
    df = get_performance_briefing(code, curr_date)
    return format_df_as_md(df, f"Performance briefing schedule for {ticker}", max_rows=10)


def get_restricted_release_md(ticker: str, curr_date: str = None) -> str:
    """限售解禁日历（解禁日、解禁数量、占总市值比例）— 供给冲击事件。"""
    code = _norm(ticker)
    df = get_restricted_release(code)
    return format_df_as_md(df, f"Restricted-release schedule for {ticker}", max_rows=20)


def get_pledge_ratio_md(ticker: str, curr_date: str = None) -> str:
    """股权质押比例（周度快照）— 风险因子。"""
    code = _norm(ticker)
    if curr_date is None:
        curr_date = datetime.now().strftime("%Y-%m-%d")
    df = get_pledge_ratio(code, curr_date)
    snapshot = df.attrs.get("_snapshot_date", "?") if hasattr(df, "attrs") else "?"
    return format_df_as_md(df, f"Pledge ratio for {ticker} (snapshot {snapshot})", max_rows=5)


# ─── Capital-flow / holder adapters ─────────────────────────────────────────

def get_top10_holders_md(ticker: str, curr_date: str = None) -> str:
    """十大股东（最新报告期）— 名次、股份类型、持股数、占比、增减。"""
    code = _norm(ticker)
    if curr_date is None:
        curr_date = datetime.now().strftime("%Y-%m-%d")
    df = get_top10_holders(code, curr_date)
    return format_df_as_md(df, f"Top 10 holders for {ticker}", max_rows=15)


def get_top10_free_holders_md(ticker: str, curr_date: str = None) -> str:
    """十大流通股东（最新报告期）."""
    code = _norm(ticker)
    if curr_date is None:
        curr_date = datetime.now().strftime("%Y-%m-%d")
    df = get_top10_free_holders(code, curr_date)
    return format_df_as_md(df, f"Top 10 free-circulating holders for {ticker}", max_rows=15)


def get_concerted_action_md(ticker: str, curr_date: str = None) -> str:
    """一致行动人（实控人 + 一致行动人，合计持股比例）."""
    code = _norm(ticker)
    if curr_date is None:
        curr_date = datetime.now().strftime("%Y-%m-%d")
    df = get_concerted_action(code, curr_date)
    return format_df_as_md(df, f"Concerted-action group for {ticker}", max_rows=5)


def get_block_trade_md(ticker: str, curr_date: str = None) -> str:
    """大宗交易（近 90 天）— 成交价、折溢价、买 / 卖营业部。"""
    code = _norm(ticker)
    if curr_date is None:
        curr_date = datetime.now().strftime("%Y-%m-%d")
    df = get_block_trade(code, curr_date, look_back_days=90)
    return format_df_as_md(df, f"Block trades (last 90 days) for {ticker}", max_rows=30)


def get_shareholder_change_md(ticker: str, curr_date: str = None) -> str:
    """大股东增减持记录."""
    code = _norm(ticker)
    df = get_shareholder_change(code)
    return format_df_as_md(df, f"Large-shareholder transactions for {ticker}", max_rows=30)


def get_management_change_md(ticker: str, curr_date: str = None) -> str:
    """高管持股变动记录."""
    code = _norm(ticker)
    df = get_management_change(code)
    return format_df_as_md(df, f"Management share-transaction history for {ticker}", max_rows=30)


# ─────────────────────────────────────────────────────────────────────────────
# Aggregator — pull everything in one call
# ─────────────────────────────────────────────────────────────────────────────

def fetch_all(ticker: str, curr_date: str) -> dict[str, pd.DataFrame]:
    """One-shot fetch of every section for ``ticker``.

    Returns a dict keyed by a stable English section name. Each value is a
    DataFrame; check ``.empty`` and ``.attrs.get('_skip_reason')`` to know
    why a section is missing.

    `curr_date` is YYYY-MM-DD; used to bound the date-iterating endpoints
    (业绩预告 / 机构调研 / 大宗交易 / 股权质押).
    """
    start_date = (
        datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=365 * 3)
    ).strftime("%Y-%m-%d")

    sections = {
        # Company basics
        "company_profile":      get_company_profile(ticker),
        "individual_info":      get_individual_info(ticker),
        "business_intro":       get_business_intro(ticker),
        "revenue_breakdown":    get_revenue_breakdown(ticker),
        "ipo_summary":          get_ipo_summary(ticker),
        # Concept tags
        "concept_tags":         get_concept_tags(ticker),
        # Financials
        "financial_abstract":   get_financial_abstract(ticker),
        "profit_forecast":      get_profit_forecast(ticker),
        # Share structure
        "share_change":         get_share_change(ticker, start_date, curr_date),
        "shareholder_count":    get_shareholder_count(ticker),
        "shareholder_change":   get_shareholder_change(ticker),
        "management_change":    get_management_change(ticker),
        # Major events
        "restricted_release":   get_restricted_release(ticker),
        "dividend_em":          get_dividend_history_em(ticker),
        "dividend_ths":         get_dividend_history_ths(ticker),
        "pledge_ratio":         get_pledge_ratio(ticker, curr_date),
        # Earnings
        "earnings_forecast":    get_earnings_forecast(ticker, curr_date),
        "earnings_express":     get_earnings_express(ticker, curr_date),
        "earnings_report":      get_earnings_report(ticker, curr_date),
        # Institutional & block trade
        "institutional_visits": get_institutional_visits(ticker, curr_date),
        "block_trade":          get_block_trade(ticker, curr_date),
        # Capital flow
        "fund_flow":            get_individual_fund_flow(ticker),
        "northbound":           get_northbound_holding(ticker),
        # Q&A
        "investor_qa":          get_investor_qa(ticker),
        # Research & news
        "research_reports":     get_research_reports(ticker),
        "stock_news":           get_stock_news(ticker),
    }
    return sections
