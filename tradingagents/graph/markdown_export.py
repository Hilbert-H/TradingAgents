"""Render TradingAgents final_state into a single, uniformly-formatted markdown report.

Layout (every report uses exactly this skeleton, sections 一 → 九):

    # <name> (<ticker>) 投资分析报告 · 最终决策：<Rating>（<中文>）
    - 元数据 block (代码 / 名称 / 交易日 / 报告生成时间 / 最终评级)
    ## 一、技术面分析（Market Analyst）
    ## 二、市场情绪分析（Social Analyst）
    ## 三、新闻与公告分析（News Analyst）
    ## 四、基本面分析（Fundamentals Analyst）
    ## 五、A 股资金面分析（Capital Flow Analyst）
    ## 六、多空辩论（Bull / Bear Researchers）
        ### Bull 多方 / ### Bear 空方 / ### Research Manager 判决
    ## 七、交易员投资计划（Trader）
    ## 八、风险讨论（Risk Analysts）
        ### 激进派 / ### 保守派 / ### 中性派 / ### Risk Judge 判决
    ## 九、最终决策（Portfolio Manager）

Analyst free-text outputs may contain their own H1/H2 headers that would
clash with the report's section structure, so ``_normalize_subsection``
demotes any in-content ``# / ##`` headers to ``### / ####`` before the
content is spliced in.

Filename convention: ``<stock_name>_<bare_code>_<trade_date>.md``
- ``stock_name``: Chinese short name for A-shares (akshare lookup); ticker
  as-is for non-A-share
- ``bare_code``: the part before the suffix (``600487`` for ``600487.SS``)
- ``trade_date``: yyyy-mm-dd

Output directory: ``<output_dir>/`` (created if missing).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from tradingagents.agents.utils.rating import parse_rating
from tradingagents.dataflows.akshare_common import get_a_share_name, is_a_share

logger = logging.getLogger(__name__)


# ---- 评级中英对照 ----
# Used for both the H1 title and the metadata block; English is the
# canonical value (it's what the rating extractor produces), Chinese is the
# convenience translation for human readers.
_RATING_CN: Dict[str, str] = {
    "Buy": "买入",
    "Overweight": "增持",
    "Hold": "持有",
    "Underweight": "减持",
    "Sell": "卖出",
}


def _bare_code(ticker: str) -> str:
    return ticker.split(".", 1)[0] if "." in ticker else ticker


def _rating_label(rating: str) -> str:
    """Return ``"Buy（买入）"`` style display label for use in title/metadata."""
    cn = _RATING_CN.get(rating)
    return f"{rating}（{cn}）" if cn else rating


def _build_filename(ticker: str, trade_date: str) -> str:
    """Return '<stock_name>_<bare_code>_<trade_date>.md'."""
    name = get_a_share_name(ticker) if is_a_share(ticker) else ticker
    code = _bare_code(ticker)
    # Sanitize: strip whitespace, replace path separators
    name_clean = (name or code).strip().replace("/", "_").replace("\\", "_")
    return f"{name_clean}_{code}_{trade_date}.md"


# Match any markdown heading (``#``..``######``) at the start of a line that
# is *not* inside a fenced code block.  We use this to demote analyst-output
# H1/H2 headers so they don't collide with the report's outer structure.
_HEADING_RE = re.compile(r"^(#{1,6})(\s)", re.MULTILINE)


def _normalize_subsection(body: str) -> str:
    """Demote in-content H1/H2 headers by two levels so they sit under ``##``.

    Analyst LLMs frequently emit ``# 报告标题`` or ``## 一、...`` at the top
    of their output.  When we splice that body underneath the report's own
    ``## 一、技术面分析`` header, those headers create duplicate top-level
    sections and break the document outline.

    Rules:
    - ``#``  → ``###``        (H1 → H3)
    - ``##`` → ``####``       (H2 → H4)
    - ``###``+ are kept as-is (already deep enough)

    Fenced code blocks (``​```​...​```​``) are left untouched.
    """
    if not isinstance(body, str) or not body.strip():
        return body

    out_lines: list[str] = []
    in_fence = False
    fence_re = re.compile(r"^\s*```")

    for line in body.splitlines():
        if fence_re.match(line):
            in_fence = not in_fence
            out_lines.append(line)
            continue
        if in_fence:
            out_lines.append(line)
            continue

        m = re.match(r"^(#{1,6})(\s.*)$", line)
        if m:
            hashes, rest = m.group(1), m.group(2)
            if len(hashes) == 1:
                out_lines.append("###" + rest)
            elif len(hashes) == 2:
                out_lines.append("####" + rest)
            else:
                out_lines.append(line)
        else:
            out_lines.append(line)

    return "\n".join(out_lines)


def _safe_body(body: Any) -> str:
    """Coerce body to a stripped string; return ``_（无内容）_`` for empty input."""
    if isinstance(body, str) and body.strip():
        return _normalize_subsection(body.strip())
    return "_（无内容）_"


def render_state_as_markdown(state: Dict[str, Any], ticker: str, trade_date: str) -> str:
    """Render the full final_state into a human-readable markdown report."""
    name = get_a_share_name(ticker) if is_a_share(ticker) else ticker
    code = _bare_code(ticker)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    final_decision_text = state.get("final_trade_decision") or state.get("investment_plan") or ""
    rating = parse_rating(final_decision_text) if final_decision_text else "Hold"
    rating_label = _rating_label(rating)

    investment_debate = state.get("investment_debate_state") or {}
    risk_debate = state.get("risk_debate_state") or {}

    sections: list[str] = []

    # ---- H1 title + metadata block ----
    sections.append(f"# {name} ({ticker}) 投资分析报告 · 最终决策：{rating_label}")
    sections.append("")
    sections.append(f"- **股票代码**: {ticker}")
    sections.append(f"- **股票名称**: {name}")
    sections.append(f"- **分析交易日**: {trade_date}")
    sections.append(f"- **报告生成时间**: {generated_at}")
    sections.append(f"- **最终评级**: {rating_label}")
    sections.append("")
    sections.append("---")
    sections.append("")

    def add_section(title: str, body: Any) -> None:
        sections.append(f"## {title}")
        sections.append("")
        sections.append(_safe_body(body))
        sections.append("")
        sections.append("---")
        sections.append("")

    add_section("一、技术面分析（Market Analyst）", state.get("market_report", ""))
    add_section("二、市场情绪分析（Social Analyst）", state.get("sentiment_report", ""))
    add_section("三、新闻与公告分析（News Analyst）", state.get("news_report", ""))
    add_section("四、基本面分析（Fundamentals Analyst）", state.get("fundamentals_report", ""))
    add_section("五、A 股资金面分析（Capital Flow Analyst）", state.get("capital_flow_report", ""))

    sections.append("## 六、多空辩论（Bull / Bear Researchers）")
    sections.append("")
    sections.append("### Bull 多方")
    sections.append("")
    sections.append(_safe_body(investment_debate.get("bull_history")))
    sections.append("")
    sections.append("### Bear 空方")
    sections.append("")
    sections.append(_safe_body(investment_debate.get("bear_history")))
    sections.append("")
    sections.append("### Research Manager 判决")
    sections.append("")
    sections.append(_safe_body(investment_debate.get("judge_decision")))
    sections.append("")
    sections.append("---")
    sections.append("")

    add_section(
        "七、交易员投资计划（Trader）",
        state.get("trader_investment_decision") or state.get("trader_investment_plan", ""),
    )

    sections.append("## 八、风险讨论（Risk Analysts）")
    sections.append("")
    sections.append("### 激进派 Aggressive")
    sections.append("")
    sections.append(_safe_body(risk_debate.get("aggressive_history")))
    sections.append("")
    sections.append("### 保守派 Conservative")
    sections.append("")
    sections.append(_safe_body(risk_debate.get("conservative_history")))
    sections.append("")
    sections.append("### 中性派 Neutral")
    sections.append("")
    sections.append(_safe_body(risk_debate.get("neutral_history")))
    sections.append("")
    sections.append("### Risk Judge 判决")
    sections.append("")
    sections.append(_safe_body(risk_debate.get("judge_decision")))
    sections.append("")
    sections.append("---")
    sections.append("")

    add_section(
        "九、最终决策（Portfolio Manager）",
        final_decision_text,
    )

    return "\n".join(sections)


def save_analysis_markdown(
    state: Dict[str, Any],
    ticker: str,
    trade_date: str,
    output_dir: Path | str,
) -> Path:
    """Write the markdown report to <output_dir>/<name>_<code>_<date>.md and return the path."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    filename = _build_filename(ticker, trade_date)
    full_path = output_path / filename
    md = render_state_as_markdown(state, ticker, trade_date)
    full_path.write_text(md, encoding="utf-8")
    logger.info("Wrote analysis markdown to %s", full_path)
    return full_path
