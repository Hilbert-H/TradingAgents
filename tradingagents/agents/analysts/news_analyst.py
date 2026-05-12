from datetime import datetime, timedelta

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_news,
    get_global_news,
    get_insider_transactions,
    get_announcements,
    get_language_instruction,
)
from tradingagents.dataflows.akshare_common import is_a_share
from tradingagents.dataflows.config import get_config


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_news_analyst(llm):
    def news_analyst_node(state):
        current_date = state["trade_date"]
        ticker = state["company_of_interest"]
        instrument_context = build_instrument_context(ticker)

        tools = [get_news, get_global_news, get_insider_transactions, get_announcements]

        if is_a_share(ticker):
            # A-share branch — pre-fetch all four sources and inject directly into
            # the prompt so the LLM doesn't have to wrestle tool-calling under
            # rate-limit pressure, and so it can't skip a required section
            # because "the tool returned nothing useful".
            start_date = _seven_days_back(current_date)
            announcements_block = get_announcements.func(ticker, start_date, current_date)
            news_block          = get_news.func(ticker, start_date, current_date)
            insider_block       = get_insider_transactions.func(ticker)
            global_news_block   = get_global_news.func(current_date)

            system_message = _build_a_share_system_message(
                ticker=ticker,
                start_date=start_date,
                end_date=current_date,
                announcements_block=announcements_block,
                news_block=news_block,
                insider_block=insider_block,
                global_news_block=global_news_block,
            )

            # No tool-binding for A-share — everything is in the prompt.
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a helpful AI assistant, collaborating with other assistants."
                        " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                        " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                        "\n{system_message}\n"
                        "For your reference, the current date is {current_date}. {instrument_context}",
                    ),
                    MessagesPlaceholder(variable_name="messages"),
                ]
            )
            prompt = prompt.partial(system_message=system_message)
            prompt = prompt.partial(current_date=current_date)
            prompt = prompt.partial(instrument_context=instrument_context)

            chain = prompt | llm
            result = chain.invoke(state["messages"])
            return {
                "messages": [result],
                "news_report": result.content,
            }

        # Non-A-share — keep the original tool-calling design
        system_message = (
            "You are a news researcher tasked with analyzing recent news and trends over the past week. Please write a comprehensive report of the current state of the world that is relevant for trading and macroeconomics. Use the available tools: get_news(query, start_date, end_date) for company-specific or targeted news searches, and get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + """ Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."""
            + get_language_instruction()
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""

        if len(result.tool_calls) == 0:
            report = result.content

        return {
            "messages": [result],
            "news_report": report,
        }

    return news_analyst_node


# ---------------------------------------------------------------------------
# A-share system-message builder — enforces independent subsection structure
# ---------------------------------------------------------------------------
def _build_a_share_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    announcements_block: str,
    news_block: str,
    insider_block: str,
    global_news_block: str,
) -> str:
    """Pre-fetched-data prompt that mandates four independent subsections.

    Critical: each of 法定信披 / 近期新闻 / 内部交易 / 宏观与行业 must be
    its own ``###`` section in the output. The old prompt let the LLM
    collapse them into a single narrative table, which lost signal —
    particularly insider-transaction data.
    """
    return f"""You are an A-share news & corporate-action analyst. Produce a comprehensive news report for {ticker} covering {start_date} → {end_date}. Four data sources have already been collected for you from akshare; use only this data plus your prior knowledge of the company / sector — do not invent specifics.

## Data sources (pre-fetched, in this prompt)

### 1) 法定信息披露公告 (announcements, 法定信披) — AUTHORITATIVE
Filed with the exchange (上交所 / 深交所). These are the highest-trust source — they cover material events that the company is legally required to disclose.

<start_of_announcements>
{announcements_block}
<end_of_announcements>

### 2) 近期新闻 — 东方财富个股新闻流 (past 7 days)
Lower-trust than announcements but useful for sector-/theme-level context, sell-side commentary, and event timeline reconstruction.

<start_of_news>
{news_block}
<end_of_news>

### 3) 内部交易 / 高管增减持 — recent insider activity
Director / officer / major shareholder trades. Recent **buys** by insiders are bullish (skin-in-the-game); concentrated **sells** are a meaningful negative signal that often precedes price weakness.

<start_of_insider>
{insider_block}
<end_of_insider>

### 4) 宏观与行业新闻 — 多源 (East-Money / 财联社 / 新浪 / 富途 / 同花顺)
Macro / policy / cross-asset headlines. Use to identify policy tailwinds or headwinds for the stock's sector.

<start_of_global>
{global_news_block}
<end_of_global>

## Required output structure (MUST follow — top-level sections are H3 ``###`` to nest cleanly)

### 一、综合研判
Headline summary: 2–4 sentences. What's the dominant news-flow signal? Major events in window? Overall lean (利好 / 利空 / 中性).

### 二、法定信息披露公告分析
**MANDATORY independent section.** Walk through each公告 from the announcements block above. For each, give: 公告日期 | 公告类型 | 核心内容（一句话）| 影响方向. If no公告 in window, say so explicitly — but still keep this section header.

### 三、近期新闻动态分析
**MANDATORY independent section.** Group the news headlines by theme (公司专属 / 板块联动 / 概念催化 / 风险事件). Cite specific dates and quote standout headlines.

### 四、内部交易 / 高管增减持分析
**MANDATORY independent section** — do NOT merge this into the announcements section or relegate it to the summary table.
Quote the actual insider records from the data block. For each: 时间 | 人物 | 职务 | 动作（买入/卖出/大宗交易）| 数量 | 均价 | 信号方向. If no recent activity, state "近一周无新增内部交易" plus the most recent historical entry as context.

### 五、宏观与行业背景
**MANDATORY independent section.** Pull 3–5 macro / policy / industry headlines from the global news block that are relevant to this stock's sector. State which policy axis (e.g. 半导体国产替代 / 新能源补贴 / 央行流动性) is most relevant.

### 六、综合分析及交易建议依据
Tie the four sources together. What's the dominant theme? What contradicts? What catalysts in the next 1–5 days?

### 七、关键信息汇总表
| 类别 | 关键信息 | 时间 | 影响方向 | 重要性 (⭐) |
Cover at least one row per data source so all four sources appear.

## Rules
1. **Independence of sections 二–五 is non-negotiable.** Each MUST appear with its ``###`` header even if the data block says "_No filings._" or "_Source unavailable._". In that case, write one short sentence acknowledging the gap, then move on.
2. **Cite the data.** Quote actual dates, numbers, names from the pre-fetched blocks. Don't paraphrase into vagueness.
3. **No web search, no fabrication.** Everything you say must trace back to either a pre-fetched data block or general knowledge clearly labelled as such.

{get_language_instruction()}"""
