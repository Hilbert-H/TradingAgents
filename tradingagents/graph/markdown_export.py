"""Render TradingAgents final_state into a single markdown report file.

Filename convention: <stock_name>_<bare_code>_<trade_date>.md
- stock_name: Chinese short name for A-shares (via akshare lookup);
              ticker as-is for non-A-share
- bare_code:  the part before the suffix (600487 for 600487.SS; NVDA for NVDA)
- trade_date: yyyy-mm-dd

Output directory: <results_dir>/analyses/ (created if missing)
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from tradingagents.dataflows.akshare_common import get_a_share_name, is_a_share

logger = logging.getLogger(__name__)


def _bare_code(ticker: str) -> str:
    return ticker.split(".", 1)[0] if "." in ticker else ticker


def _build_filename(ticker: str, trade_date: str) -> str:
    """Return '<stock_name>_<bare_code>_<trade_date>.md'."""
    name = get_a_share_name(ticker) if is_a_share(ticker) else ticker
    code = _bare_code(ticker)
    # Sanitize: strip whitespace, replace path separators
    name_clean = (name or code).strip().replace("/", "_").replace("\\", "_")
    return f"{name_clean}_{code}_{trade_date}.md"


def render_state_as_markdown(state: Dict[str, Any], ticker: str, trade_date: str) -> str:
    """Render the full final_state into a human-readable markdown report."""
    name = get_a_share_name(ticker) if is_a_share(ticker) else ticker
    code = _bare_code(ticker)
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    investment_debate = state.get("investment_debate_state") or {}
    risk_debate = state.get("risk_debate_state") or {}

    sections: list[str] = []

    sections.append(f"# {name} ({ticker}) 投资分析报告")
    sections.append("")
    sections.append(f"- **股票代码**: {ticker}")
    sections.append(f"- **股票名称**: {name}")
    sections.append(f"- **分析交易日**: {trade_date}")
    sections.append(f"- **报告生成时间**: {generated_at}")
    sections.append("")
    sections.append("---")
    sections.append("")

    def add_section(title: str, body: Any) -> None:
        body_str = body if isinstance(body, str) and body.strip() else "_（无内容）_"
        sections.append(f"## {title}")
        sections.append("")
        sections.append(body_str.strip() if isinstance(body_str, str) else str(body_str))
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
    sections.append((investment_debate.get("bull_history") or "_（无内容）_").strip())
    sections.append("")
    sections.append("### Bear 空方")
    sections.append("")
    sections.append((investment_debate.get("bear_history") or "_（无内容）_").strip())
    sections.append("")
    sections.append("### Research Manager 判决")
    sections.append("")
    sections.append((investment_debate.get("judge_decision") or "_（无内容）_").strip())
    sections.append("")
    sections.append("---")
    sections.append("")

    add_section("七、交易员投资计划（Trader）", state.get("trader_investment_decision") or state.get("trader_investment_plan", ""))

    sections.append("## 八、风险讨论（Risk Analysts）")
    sections.append("")
    sections.append("### 激进派 Aggressive")
    sections.append("")
    sections.append((risk_debate.get("aggressive_history") or "_（无内容）_").strip())
    sections.append("")
    sections.append("### 保守派 Conservative")
    sections.append("")
    sections.append((risk_debate.get("conservative_history") or "_（无内容）_").strip())
    sections.append("")
    sections.append("### 中性派 Neutral")
    sections.append("")
    sections.append((risk_debate.get("neutral_history") or "_（无内容）_").strip())
    sections.append("")
    sections.append("### Risk Judge 判决")
    sections.append("")
    sections.append((risk_debate.get("judge_decision") or "_（无内容）_").strip())
    sections.append("")
    sections.append("---")
    sections.append("")

    add_section("九、最终决策（Portfolio Manager）", state.get("final_trade_decision") or state.get("investment_plan", ""))

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
