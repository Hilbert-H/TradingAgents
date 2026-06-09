"""Pull every available data section for 三一重工 (600031) and render to HTML.

Usage:
    python scripts/sany_full_report.py [--ticker 600031] [--date 2026-05-12] [--out report.html]

The script combines:
  * tradingagents/dataflows/ths_full_scraper.py — comprehensive 同花顺 F10 +
    earnings + holders + valuation + market + sentiment + Q&A + briefing
  * existing project dataflows (lhb, margin, north_capital, hot_rank,
    announcements, global news)

Output is a single self-contained HTML file. Open in any browser.
"""

from __future__ import annotations

import argparse
import html
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Ensure project root is on sys.path so we can import tradingagents.*
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tradingagents.dataflows import ths_full_scraper as ths
from tradingagents.dataflows.akshare_news import (
    get_announcements_akshare,
    get_global_news_akshare,
)
from tradingagents.dataflows.akshare_sentiment import (
    get_stock_hot_rank_akshare,
)
from tradingagents.dataflows.akshare_capital_flow import (
    get_lhb_detail_akshare,
    get_lhb_institutional_akshare,
    get_margin_trading_akshare,
    get_north_capital_individual_akshare,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("sany_report")


# ─── Section catalog ────────────────────────────────────────────────────────
# (anchor_id, display_title, akshare_endpoint, what_it_shows, max_rows)
# Sections are grouped via GROUPS below; the catalog feeds the renderer.

SECTION_CATALOG: dict[str, tuple[str, str, str, int]] = {
    # Group: 公司概况
    "company_profile":    ("公司概况",            "ak.stock_profile_cninfo",
                            "巨潮资讯公司档案：法人、注册地、上市日、董秘、邮箱、入选指数等 26 字段。", 5),
    "business_intro":     ("主营介绍",            "ak.stock_zyjs_ths",
                            "同花顺主营业务：主营业务、产品类型、经营范围。", 5),
    "revenue_breakdown":  ("主营构成",            "ak.stock_zygc_em",
                            "东财主营构成：按行业 / 产品 / 地区拆分的收入、成本、毛利率。", 60),
    "ipo_summary":        ("IPO 信息",            "ak.stock_ipo_summary_cninfo",
                            "巨潮上市信息：发行价、募资额、保荐机构。", 5),
    "individual_info":    ("行情卡片",            "ak.stock_individual_info_em",
                            "东财行情卡片：总股本、流通股本、总市值、行业、上市时间。", 20),

    # Group: 概念 / 题材
    "concept_tags":       ("概念题材",            "ak.stock_hot_keyword_em",
                            "东财个股热门概念（带热度）：直接的 ticker → 概念映射。", 30),

    # Group: 财务
    "financial_abstract": ("财务摘要（同花顺）",    "ak.stock_financial_abstract_ths",
                            "同花顺核心财务指标：营收 / 净利润 / 扣非 / 毛利率 / ROE / 负债率。", 12),
    "financial_indicators": ("财务指标（86 项）",   "ak.stock_financial_analysis_indicator",
                            "新浪历年财务指标全集（86 列）：EPS / ROE / ROA / 毛利率 / 周转率 / 流动比率。", 20),
    "balance_sheet":      ("资产负债表",          "ak.stock_balance_sheet_by_report_em",
                            "东财资产负债表（最近 8 个报告期，关键列）：总资产 / 负债 / 权益 / 货币资金 / 借款。", 10),
    "income_statement":   ("利润表",              "ak.stock_profit_sheet_by_report_em",
                            "东财利润表（最近 8 个报告期）：营收 / 成本 / 销售 / 研发 / 净利润 / EPS。", 10),
    "cash_flow":          ("现金流量表",          "ak.stock_cash_flow_sheet_by_report_em",
                            "东财现金流量表（最近 8 个报告期）：经营 / 投资 / 筹资活动现金流量净额。", 10),
    "profit_forecast":    ("盈利预测",            "ak.stock_profit_forecast_ths",
                            "同花顺机构盈利预测：未来 2-3 年 EPS 的均值 / 最大 / 最小。", 10),

    # Group: 业绩披露
    "earnings_forecast":  ("业绩预告",            "ak.stock_yjyg_em",
                            "东财业绩预告：预告净利润上下限、同比变动幅度。", 8),
    "earnings_express":   ("业绩快报",            "ak.stock_yjkb_em",
                            "东财业绩快报：期末营收 / 利润 / ROE。", 8),
    "earnings_report":    ("业绩报告",            "ak.stock_yjbb_em",
                            "东财正式季报披露 KPI。", 8),
    "performance_briefing": ("业绩说明会日程",     "ak.stock_yysj_em",
                            "东财业绩说明会：首次预约时间、变更日期。", 8),

    # Group: 股东
    "top10_holders":      ("十大股东",            "ak.stock_gdfx_top_10_em",
                            "东财十大股东：最新报告期的持股名次、数量、占比、增减。", 15),
    "top10_free_holders": ("十大流通股东",        "ak.stock_gdfx_free_top_10_em",
                            "东财十大流通股东（同上）。", 15),
    "main_holder_history": ("主要股东（历史）",    "ak.stock_main_stock_holder",
                            "巨潮主要股东：多季度持股数量 / 比例 / 股本性质（可比对变化）。", 30),
    "circulate_holder_history": ("流通股东（历史）", "ak.stock_circulate_stock_holder",
                            "巨潮流通股东多季度历史。", 30),
    "concerted_action":   ("一致行动人",          "ak.stock_yzxdr_em",
                            "东财一致行动人组合：实控人 + 一致行动人，合计持股比例。", 5),
    "shareholder_count":  ("股东户数变化",        "ak.stock_zh_a_gdhs_detail_em",
                            "东财股东户数季度变化：户数、户均持股、区间股价。", 20),
    "shareholder_change": ("大股东增减持",        "ak.stock_shareholder_change_ths",
                            "同花顺大股东变动公告。", 30),
    "management_change":  ("高管变动",            "ak.stock_management_change_ths",
                            "同花顺高管持股变动记录。", 30),
    "share_change":       ("股本变动",            "ak.stock_share_change_cninfo",
                            "巨潮股本变动：每次配股 / 送转 / 解禁 / 增发后的股本结构快照。", 20),

    # Group: 大事 / Events
    "restricted_release": ("限售解禁日历",        "ak.stock_restricted_release_queue_em",
                            "东财限售解禁排期：解禁日、解禁数量、占总市值比例。", 20),
    "dividend_em":        ("分红送转（东财）",     "ak.stock_fhps_detail_em",
                            "东财分红明细：送转比例、现金分红、股息率。", 20),
    "dividend_ths":       ("分红方案（同花顺）",   "ak.stock_fhps_detail_ths",
                            "同花顺分红方案历史：登记日 / 除权日 / 实施日 / 分红总额。", 20),
    "pledge_ratio":       ("股权质押比例",        "ak.stock_gpzy_pledge_ratio_em",
                            "东财质押比例快照：质押笔数、股数、占总股本比例。", 5),

    # Group: 估值
    "valuation_daily":    ("估值（东财日频）",     "ak.stock_value_em",
                            "东财日频估值：PE-TTM / PE静 / PB / PEG / 市现率 / 市销率 / 总市值。", 30),
    "valuation_baidu_pe": ("PE-TTM（百度近一年）", "ak.stock_zh_valuation_baidu",
                            "百度估值 PE-TTM 近一年（与东财对照）。", 30),
    "valuation_baidu_pb": ("PB（百度近一年）",     "ak.stock_zh_valuation_baidu",
                            "百度估值 PB 近一年。", 30),

    # Group: 市场行情
    "daily_kline":        ("日 K 线（前复权）",    "ak.stock_zh_a_daily",
                            "新浪日 K 线（OHLCV + 换手率，前复权）— 最近 ~6 个月。", 40),
    "intraday_kline":     ("60min K 线",           "ak.stock_zh_a_minute",
                            "新浪 60 分钟 K 线 — 最近 60 根。", 60),

    # Group: 资金
    "fund_flow":          ("个股资金流向",        "ak.stock_individual_fund_flow",
                            "东财个股日频资金流向：主力 / 超大单 / 大单 / 中单 / 小单。", 30),
    "northbound":         ("北向持股历史",        "ak.stock_hsgt_individual_em",
                            "东财北向持股：持股数量 / 市值 / 占A股比例 / 当日增减。", 30),

    # Group: 千股千评 / 评级 / 研报
    "stock_comment":      ("千股千评（当日）",     "ak.stock_comment_em",
                            "东财千股千评：综合得分 / 主力成本 / 机构参与度 / 关注指数 / 排名。", 5),
    "comment_focus":      ("关注指数（30 日）",    "ak.stock_comment_detail_scrd_focus_em",
                            "东财关注指数日序。", 30),
    "comment_score":      ("综合评分（30 日）",    "ak.stock_comment_detail_zhpj_lspf_em",
                            "东财综合评分日序。", 30),
    "comment_inst_part":  ("机构参与度（40 日）",  "ak.stock_comment_detail_zlkp_jgcyd_em",
                            "东财机构参与度日序。", 40),
    "comment_desire":     ("参与意愿",            "ak.stock_comment_detail_scrd_desire_em",
                            "东财参与意愿（最近 5 个交易日）+ 5 日平均。", 10),
    "research_reports":   ("研报",                "ak.stock_research_report_em",
                            "东财研报：标题 / 机构 / 评级 / 近一月研报数 / 26/27/28 盈利预测。", 30),

    # Group: 互动 / 情绪
    "investor_qa":        ("投资者互动",          "ak.stock_sns_sseinfo / ak.stock_irm_cninfo",
                            "上证 e 互动（沪市）/ 巨潮互动易（深市）问答。", 30),
    "xueqiu_hot_follow":  ("雪球关注（最热门）",   "ak.stock_hot_follow_xq",
                            "雪球最热门关注列表中本股的当前关注人数。", 5),
    "xueqiu_hot_tweet":   ("雪球讨论（最热门）",   "ak.stock_hot_tweet_xq",
                            "雪球最热门讨论列表中本股的位置。", 5),
    "xueqiu_hot_deal":    ("雪球成交（最热门）",   "ak.stock_hot_deal_xq",
                            "雪球用户最热门买卖列表中本股的位置。", 5),
    "institutional_visits": ("机构调研",           "ak.stock_jgdy_tj_em",
                            "东财机构调研（近 180 天）：接待日 / 机构数 / 调研方式 / 人员。", 30),
    "block_trade":        ("大宗交易",            "ak.stock_dzjy_mrmx",
                            "东财大宗交易（近 90 天）：成交价、折溢价、买 / 卖营业部。", 30),
    "stock_news":         ("个股新闻",            "ak.stock_news_em",
                            "东财个股新闻流。", 30),
}


# Group ordering and section assignment
GROUPS: list[tuple[str, list[str]]] = [
    ("公司概况", ["company_profile", "business_intro", "revenue_breakdown",
                  "ipo_summary", "individual_info"]),
    ("概念题材", ["concept_tags"]),
    ("财务",     ["financial_abstract", "financial_indicators", "balance_sheet",
                  "income_statement", "cash_flow", "profit_forecast"]),
    ("业绩披露", ["earnings_forecast", "earnings_express", "earnings_report",
                  "performance_briefing"]),
    ("股东",     ["top10_holders", "top10_free_holders", "main_holder_history",
                  "circulate_holder_history", "concerted_action", "shareholder_count",
                  "shareholder_change", "management_change", "share_change"]),
    ("大事",     ["restricted_release", "dividend_em", "dividend_ths", "pledge_ratio"]),
    ("估值",     ["valuation_daily", "valuation_baidu_pe", "valuation_baidu_pb"]),
    ("行情",     ["daily_kline", "intraday_kline"]),
    ("资金",     ["fund_flow", "northbound"]),
    ("评级/研报", ["stock_comment", "comment_focus", "comment_score",
                   "comment_inst_part", "comment_desire", "research_reports"]),
    ("互动/情绪", ["investor_qa", "xueqiu_hot_follow", "xueqiu_hot_tweet",
                   "xueqiu_hot_deal", "institutional_visits", "block_trade",
                   "stock_news"]),
]


# Sections that come from EXISTING project dataflows (formatted as markdown).
EXISTING_SECTIONS: list[tuple[str, str, str, str]] = [
    ("hot_rank",       "东财热度排名",       "akshare_sentiment.get_stock_hot_rank_akshare",
        "东财热度榜：当前榜单 + 近 30 个交易日的关注度变化。"),
    ("lhb_detail",     "龙虎榜（个股）",      "akshare_capital_flow.get_lhb_detail_akshare",
        "近期上榜龙虎榜的全部明细（净买/净卖、营业部）。"),
    ("lhb_inst",       "龙虎榜（机构席位）",  "akshare_capital_flow.get_lhb_institutional_akshare",
        "近期机构席位上榜情况。"),
    ("margin",         "融资融券",            "akshare_capital_flow.get_margin_trading_akshare",
        "近 10 个交易日融资余额、融资买入额、融券余额。"),
    ("north_indiv",    "北向资金（个股净流入）", "akshare_capital_flow.get_north_capital_individual_akshare",
        "近期沪 / 深港通持有该股的明细变化。"),
    ("announcements",  "公司公告",            "akshare_news.get_announcements_akshare",
        "巨潮 / 东财法定信披公告（窗口期内）。"),
    ("global_news",    "全市场宏观新闻",      "akshare_news.get_global_news_akshare",
        "5 路并行的全球财经快讯（东财 + 财联社 + 新浪 + 富途 + 同花顺）。"),
]


# ─── HTML renderers ─────────────────────────────────────────────────────────

def render_table(df: pd.DataFrame, max_rows: int = 30) -> str:
    """Render a DataFrame to a clean HTML table. Truncates rows + escapes cells."""
    if df is None or df.empty:
        reason = ""
        if df is not None and df.attrs.get("_skip_reason"):
            reason = f' <span class="reason">({html.escape(df.attrs["_skip_reason"])})</span>'
        return f'<p class="empty">— 无数据{reason} —</p>'

    truncated = len(df) > max_rows
    view = df.head(max_rows).copy()

    # Stringify everything safely
    for col in view.columns:
        view[col] = view[col].apply(
            lambda v: "" if pd.isna(v) else html.escape(str(v))
        )

    th = "".join(f"<th>{html.escape(str(c))}</th>" for c in view.columns)
    rows_html = []
    for _, row in view.iterrows():
        tds = "".join(f"<td>{v}</td>" for v in row.values)
        rows_html.append(f"<tr>{tds}</tr>")
    table_html = (
        f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody></table></div>'
    )
    if truncated:
        table_html += f'<p class="truncated">显示前 {max_rows} 条 / 共 {len(df)} 条</p>'
    return table_html


def render_markdown_block(text: str) -> str:
    if not text or not text.strip():
        return '<p class="empty">— 无数据 —</p>'
    return f'<pre class="md">{html.escape(text)}</pre>'


def build_html(
    ticker: str,
    name: str,
    curr_date: str,
    sections: dict[str, pd.DataFrame],
    existing: dict[str, str],
    timing: dict[str, float],
    highlights: list[str],
) -> str:
    css = """
    :root {
      --bg: #0f1419;
      --panel: #1a1f29;
      --panel-2: #232934;
      --border: #2d3441;
      --fg: #e4e7eb;
      --fg-dim: #9aa3ad;
      --accent: #ef4444;
      --accent-soft: #fb7185;
      --link: #60a5fa;
      --good: #22c55e;
      --bad: #ef4444;
      --warn: #eab308;
      --highlight-bg: rgba(239, 68, 68, 0.07);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--fg);
      line-height: 1.55;
      font-size: 14px;
    }
    .layout { display: grid; grid-template-columns: 260px 1fr; min-height: 100vh; }
    nav.sidebar {
      background: var(--panel);
      border-right: 1px solid var(--border);
      padding: 24px 0;
      position: sticky;
      top: 0;
      max-height: 100vh;
      overflow-y: auto;
    }
    nav.sidebar h1 { margin: 0 20px 6px; font-size: 16px; color: var(--accent); }
    nav.sidebar .meta { margin: 0 20px 16px; font-size: 12px; color: var(--fg-dim); }
    nav.sidebar h2 {
      font-size: 11px;
      color: var(--accent-soft);
      text-transform: uppercase;
      letter-spacing: 0.06em;
      margin: 18px 20px 4px;
      border-top: 1px solid var(--border);
      padding-top: 12px;
    }
    nav.sidebar h2:first-of-type { border-top: 0; padding-top: 0; }
    nav.sidebar ul { list-style: none; padding: 0; margin: 0; }
    nav.sidebar li a {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 4px 20px;
      color: var(--fg-dim);
      text-decoration: none;
      border-left: 2px solid transparent;
      font-size: 12.5px;
    }
    nav.sidebar li a:hover {
      color: var(--fg);
      background: var(--panel-2);
      border-left-color: var(--accent-soft);
    }
    nav.sidebar li a .count {
      font-size: 10.5px;
      color: var(--good);
      font-variant-numeric: tabular-nums;
    }
    nav.sidebar li a .count.empty { color: var(--fg-dim); }
    main { padding: 32px 40px 80px; max-width: 1500px; }
    header.hero {
      background: linear-gradient(135deg, #1a1f29 0%, #281a25 100%);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 28px 32px;
      margin-bottom: 24px;
    }
    header.hero h1 { margin: 0 0 8px; font-size: 28px; color: var(--accent); }
    header.hero .sub { color: var(--fg-dim); }
    header.hero .stats { display: flex; gap: 20px; margin-top: 18px; flex-wrap: wrap; }
    header.hero .stat {
      background: rgba(255,255,255,0.04);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 16px;
      min-width: 110px;
    }
    header.hero .stat-label { font-size: 11px; color: var(--fg-dim); text-transform: uppercase; }
    header.hero .stat-value { font-size: 20px; font-weight: 600; margin-top: 2px; }

    section.highlights {
      background: linear-gradient(135deg, #1d1318 0%, #1a1f29 100%);
      border: 1px solid var(--accent);
      border-radius: 10px;
      padding: 20px 24px;
      margin-bottom: 24px;
    }
    section.highlights h2 {
      margin: 0 0 12px;
      font-size: 18px;
      color: var(--accent-soft);
      display: flex;
      align-items: center;
      gap: 8px;
    }
    section.highlights ul { margin: 0; padding-left: 0; list-style: none; }
    section.highlights li {
      padding: 6px 12px;
      border-left: 2px solid var(--accent);
      background: var(--highlight-bg);
      margin-bottom: 6px;
      border-radius: 4px;
      font-size: 13.5px;
    }

    h2.group {
      font-size: 22px;
      color: var(--accent-soft);
      border-bottom: 1px solid var(--border);
      padding-bottom: 6px;
      margin: 38px 0 12px;
    }
    h2.group:first-of-type { margin-top: 18px; }

    section.card {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px 24px;
      margin-bottom: 18px;
    }
    section.card > h3 {
      margin: 0 0 4px;
      font-size: 17px;
      color: var(--fg);
      display: flex;
      align-items: baseline;
      gap: 10px;
    }
    section.card > h3 .badge {
      font-size: 10.5px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 999px;
      letter-spacing: 0.04em;
    }
    .badge.ok    { background: rgba(34,197,94,0.15);  color: var(--good); }
    .badge.empty { background: rgba(154,163,173,0.15); color: var(--fg-dim); }
    .badge.have  { background: rgba(96,165,250,0.15); color: var(--link); }

    section.card .src {
      font-family: "SF Mono", Menlo, monospace;
      font-size: 11px;
      color: var(--fg-dim);
      margin: 0 0 10px;
    }
    section.card .desc { color: var(--fg-dim); font-size: 13px; margin: 0 0 12px; }

    .table-wrap { overflow-x: auto; max-width: 100%; }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      min-width: 600px;
    }
    thead { background: var(--panel-2); }
    th, td {
      border: 1px solid var(--border);
      padding: 5px 9px;
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }
    th {
      color: var(--fg);
      font-weight: 600;
      font-size: 11.5px;
      position: sticky;
      top: 0;
      background: var(--panel-2);
    }
    td {
      color: var(--fg-dim);
      max-width: 420px;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    tbody tr:hover td { background: rgba(255,255,255,0.02); color: var(--fg); }
    .empty { color: var(--fg-dim); font-style: italic; padding: 10px 0; margin: 0; }
    .empty .reason { font-size: 11px; opacity: 0.7; }
    .truncated { color: var(--fg-dim); font-size: 11px; margin: 6px 0 0; text-align: right; }
    pre.md {
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px 14px;
      overflow-x: auto;
      font-family: "SF Mono", Menlo, monospace;
      font-size: 11px;
      color: var(--fg);
      white-space: pre;
      line-height: 1.45;
    }
    footer {
      margin-top: 60px;
      color: var(--fg-dim);
      font-size: 12px;
      text-align: center;
      padding: 20px;
    }
    """

    # ── Sidebar nav with group structure
    nav_html = [
        f'<h1>{html.escape(name)}</h1>',
        f'<div class="meta">{html.escape(ticker)} · {html.escape(curr_date)}</div>',
    ]
    for group_title, anchors in GROUPS:
        nav_html.append(f'<h2>{html.escape(group_title)}</h2><ul>')
        for anchor in anchors:
            meta = SECTION_CATALOG.get(anchor)
            if not meta:
                continue
            title, _ep, _desc, _max = meta
            df = sections.get(anchor, pd.DataFrame())
            count = 0 if df is None or df.empty else len(df)
            count_class = "count" if count > 0 else "count empty"
            count_str = str(count) if count > 0 else "—"
            nav_html.append(
                f'<li><a href="#{anchor}">{html.escape(title)}'
                f'<span class="{count_class}">{count_str}</span></a></li>'
            )
        nav_html.append('</ul>')
    nav_html.append('<h2>已有项目数据</h2><ul>')
    for anchor, title, *_ in EXISTING_SECTIONS:
        nav_html.append(f'<li><a href="#{anchor}">{html.escape(title)}'
                        f'<span class="count">md</span></a></li>')
    nav_html.append('</ul>')
    sidebar = "\n".join(nav_html)

    # ── Hero
    profile = sections.get("company_profile", pd.DataFrame())
    industry = ""
    listing_date = ""
    if not profile.empty:
        col = next((c for c in profile.columns if "行业" in c), None)
        if col:
            industry = str(profile.iloc[0][col])
        col = next((c for c in profile.columns if "上市" in c and "日" in c), None)
        if col:
            listing_date = str(profile.iloc[0][col])

    n_ok = sum(1 for k, v in sections.items() if v is not None and not v.empty)
    n_total = len(SECTION_CATALOG)
    total_sec = sum(timing.values())

    hero = f"""
    <header class="hero">
      <h1>{html.escape(name)} <span style="color:var(--fg-dim);font-weight:400;font-size:18px">{html.escape(ticker)}</span></h1>
      <div class="sub">同花顺 F10 + 估值 + 行情 + 评级 + 情绪 全量数据快照 · 报告日 {html.escape(curr_date)}</div>
      <div class="stats">
        <div class="stat"><div class="stat-label">行业</div><div class="stat-value" style="font-size:16px">{html.escape(industry or '—')}</div></div>
        <div class="stat"><div class="stat-label">上市日</div><div class="stat-value" style="font-size:16px">{html.escape(listing_date or '—')}</div></div>
        <div class="stat"><div class="stat-label">数据节</div><div class="stat-value">{n_ok}/{n_total}</div></div>
        <div class="stat"><div class="stat-label">耗时</div><div class="stat-value" style="font-size:16px">{total_sec:.0f} s</div></div>
      </div>
    </header>
    """

    # ── Highlights synthesis
    if highlights:
        bullets_html = "".join(f"<li>{html.escape(b)}</li>" for b in highlights)
        highlights_html = (
            f'<section class="highlights"><h2>🤖 投资要点（规则化合成，非 LLM）</h2>'
            f'<ul>{bullets_html}</ul></section>'
        )
    else:
        highlights_html = ""

    # ── Section bodies, grouped
    body_parts = [hero, highlights_html]
    for group_title, anchors in GROUPS:
        body_parts.append(f'<h2 class="group">📂 {html.escape(group_title)}</h2>')
        for anchor in anchors:
            meta = SECTION_CATALOG.get(anchor)
            if not meta:
                continue
            title, endpoint, desc, max_rows = meta
            df = sections.get(anchor, pd.DataFrame())
            ms = timing.get(anchor, 0.0)
            if df is None or df.empty:
                badge_cls = "empty"
                badge_text = "—"
            else:
                badge_cls = "ok"
                badge_text = "新接入"
            n_rows = 0 if df is None or df.empty else len(df)
            body_parts.append(f"""
            <section class="card" id="{anchor}">
              <h3>{html.escape(title)} <span class="badge {badge_cls}">{badge_text}</span></h3>
              <p class="src">{html.escape(endpoint)} · {ms*1000:.0f} ms · 行数 {n_rows}</p>
              <p class="desc">{html.escape(desc)}</p>
              {render_table(df, max_rows)}
            </section>
            """)

    # ── Existing-dataflow sections
    body_parts.append('<h2 class="group">📂 已有项目数据</h2>')
    for anchor, title, endpoint, desc in EXISTING_SECTIONS:
        md = existing.get(anchor, "")
        ms = timing.get(f"existing:{anchor}", 0.0)
        body_parts.append(f"""
        <section class="card" id="{anchor}">
          <h3>{html.escape(title)} <span class="badge have">已有</span></h3>
          <p class="src">{html.escape(endpoint)} · {ms*1000:.0f} ms</p>
          <p class="desc">{html.escape(desc)}</p>
          {render_markdown_block(md)}
        </section>
        """)

    body_parts.append(
        f'<footer>由 TradingAgents · ths_full_scraper 生成 · '
        f'{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</footer>'
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>{html.escape(name)} 全量F10快照 · {html.escape(curr_date)}</title>
  <style>{css}</style>
</head>
<body>
  <div class="layout">
    <nav class="sidebar">{sidebar}</nav>
    <main>{''.join(body_parts)}</main>
  </div>
</body>
</html>"""


# ─── Pipeline ───────────────────────────────────────────────────────────────

def _timed(label: str, fn):
    t0 = time.perf_counter()
    try:
        result = fn()
    except Exception as e:
        log.warning("%s crashed: %s", label, e)
        result = None
    return result, time.perf_counter() - t0


def run(ticker: str, curr_date: str, out_path: Path) -> Path:
    log.info("Starting full scrape for %s as of %s", ticker, curr_date)
    timing: dict[str, float] = {}
    sections: dict[str, pd.DataFrame] = {}

    start_date_3y = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=365*3)).strftime("%Y-%m-%d")
    start_date_6mo = (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=180)).strftime("%Y-%m-%d")

    # Map section anchor -> fetcher
    section_fns = {
        # 公司概况
        "company_profile":          lambda: ths.get_company_profile(ticker),
        "business_intro":           lambda: ths.get_business_intro(ticker),
        "revenue_breakdown":        lambda: ths.get_revenue_breakdown(ticker),
        "ipo_summary":              lambda: ths.get_ipo_summary(ticker),
        "individual_info":          lambda: ths.get_individual_info(ticker),
        # 概念
        "concept_tags":             lambda: ths.get_concept_tags(ticker),
        # 财务
        "financial_abstract":       lambda: ths.get_financial_abstract(ticker),
        "financial_indicators":     lambda: ths.get_financial_indicators(ticker, "2018"),
        "balance_sheet":            lambda: ths.get_balance_sheet(ticker),
        "income_statement":         lambda: ths.get_income_statement(ticker),
        "cash_flow":                lambda: ths.get_cash_flow(ticker),
        "profit_forecast":          lambda: ths.get_profit_forecast(ticker),
        # 业绩披露
        "earnings_forecast":        lambda: ths.get_earnings_forecast(ticker, curr_date),
        "earnings_express":         lambda: ths.get_earnings_express(ticker, curr_date),
        "earnings_report":          lambda: ths.get_earnings_report(ticker, curr_date),
        "performance_briefing":     lambda: ths.get_performance_briefing(ticker, curr_date),
        # 股东
        "top10_holders":            lambda: ths.get_top10_holders(ticker, curr_date),
        "top10_free_holders":       lambda: ths.get_top10_free_holders(ticker, curr_date),
        "main_holder_history":      lambda: ths.get_main_holder_history(ticker),
        "circulate_holder_history": lambda: ths.get_circulate_holder_history(ticker),
        "concerted_action":         lambda: ths.get_concerted_action(ticker, curr_date),
        "shareholder_count":        lambda: ths.get_shareholder_count(ticker),
        "shareholder_change":       lambda: ths.get_shareholder_change(ticker),
        "management_change":        lambda: ths.get_management_change(ticker),
        "share_change":             lambda: ths.get_share_change(ticker, start_date_3y, curr_date),
        # 大事
        "restricted_release":       lambda: ths.get_restricted_release(ticker),
        "dividend_em":              lambda: ths.get_dividend_history_em(ticker),
        "dividend_ths":             lambda: ths.get_dividend_history_ths(ticker),
        "pledge_ratio":             lambda: ths.get_pledge_ratio(ticker, curr_date),
        # 估值
        "valuation_daily":          lambda: ths.get_valuation_daily(ticker),
        "valuation_baidu_pe":       lambda: ths.get_valuation_baidu_pe(ticker),
        "valuation_baidu_pb":       lambda: ths.get_valuation_baidu_pb(ticker),
        # 行情
        "daily_kline":              lambda: ths.get_daily_kline(ticker, start_date_6mo, curr_date),
        "intraday_kline":           lambda: ths.get_intraday_kline(ticker, period="60"),
        # 资金
        "fund_flow":                lambda: ths.get_individual_fund_flow(ticker),
        "northbound":               lambda: ths.get_northbound_holding(ticker),
        # 评级/研报
        "stock_comment":            lambda: ths.get_stock_comment(ticker),
        "comment_focus":            lambda: ths.get_comment_focus_history(ticker),
        "comment_score":            lambda: ths.get_comment_score_history(ticker),
        "comment_inst_part":        lambda: ths.get_comment_institution_participation(ticker),
        "comment_desire":           lambda: ths.get_comment_participation_desire(ticker),
        "research_reports":         lambda: ths.get_research_reports(ticker),
        # 互动/情绪
        "investor_qa":              lambda: ths.get_investor_qa(ticker),
        "xueqiu_hot_follow":        lambda: ths.get_xueqiu_hot_follow(ticker),
        "xueqiu_hot_tweet":         lambda: ths.get_xueqiu_hot_tweet(ticker),
        "xueqiu_hot_deal":          lambda: ths.get_xueqiu_hot_deal(ticker),
        "institutional_visits":     lambda: ths.get_institutional_visits(ticker, curr_date, look_back_days=180),
        "block_trade":              lambda: ths.get_block_trade(ticker, curr_date, look_back_days=90),
        "stock_news":               lambda: ths.get_stock_news(ticker),
    }

    for key, fn in section_fns.items():
        log.info("→ fetching %s", key)
        df, dt = _timed(key, fn)
        timing[key] = dt
        sections[key] = df if df is not None else pd.DataFrame()
        n = 0 if sections[key] is None or sections[key].empty else len(sections[key])
        log.info("  %s: %d rows in %.2fs", key, n, dt)

    # Existing project dataflows
    existing: dict[str, str] = {}
    ticker_dotted = f"{ticker}.SH" if ticker.startswith(("60", "68", "9")) else f"{ticker}.SZ"

    existing_fns = {
        "hot_rank":      lambda: get_stock_hot_rank_akshare(ticker_dotted, curr_date),
        "lhb_detail":    lambda: get_lhb_detail_akshare(ticker_dotted, curr_date, 10),
        "lhb_inst":      lambda: get_lhb_institutional_akshare(ticker_dotted, curr_date, 30),
        "margin":        lambda: get_margin_trading_akshare(ticker_dotted, curr_date, 10),
        "north_indiv":   lambda: get_north_capital_individual_akshare(ticker_dotted, curr_date, 30),
        "announcements": lambda: get_announcements_akshare(
            ticker_dotted,
            (datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=14)).strftime("%Y-%m-%d"),
            curr_date,
        ),
        "global_news":   lambda: get_global_news_akshare(curr_date, 7, 30),
    }
    for key, fn in existing_fns.items():
        log.info("→ existing-dataflow %s", key)
        result, dt = _timed(f"existing:{key}", fn)
        timing[f"existing:{key}"] = dt
        existing[key] = result if isinstance(result, str) else (str(result) if result else "")
        log.info("  %s: %d chars in %.2fs", key, len(existing[key]), dt)

    # Derive name
    name = ticker
    cp = sections.get("company_profile", pd.DataFrame())
    if not cp.empty:
        col = next((c for c in cp.columns if "简称" in c and "A股" in c), None)
        if col:
            name = str(cp.iloc[0][col])
        else:
            col = next((c for c in cp.columns if "公司名称" in c), None)
            if col:
                name = str(cp.iloc[0][col])

    # Synthesis bullets — never fail the whole report on a formatting bug
    try:
        highlights = ths.synthesize_highlights(sections, ticker)
    except Exception as e:
        log.warning("synthesis failed: %s", e)
        highlights = [f"⚠️ 自动合成失败：{type(e).__name__}: {e}"]
    log.info("Synthesis: %d highlight bullets", len(highlights))

    html_str = build_html(ticker, name, curr_date, sections, existing, timing, highlights)
    out_path.write_text(html_str, encoding="utf-8")
    total = sum(timing.values())
    log.info("Done. HTML written to %s (%.1fs total, %d new sections, %d existing sections)",
             out_path, total, len(section_fns), len(existing_fns))
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="600031")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--out", default="reports/sany_full_report.html")
    args = ap.parse_args()

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run(args.ticker, args.date, out_path)
    print(f"\nReport written to: {out_path}\nOpen it in a browser: file://{out_path}")


if __name__ == "__main__":
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)
    main()
