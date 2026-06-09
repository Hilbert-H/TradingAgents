from langchain_core.messages import HumanMessage, RemoveMessage

# Import tools from separate utility files
from tradingagents.agents.utils.core_stock_tools import (
    get_stock_data
)
from tradingagents.agents.utils.technical_indicators_tools import (
    get_indicators
)
from tradingagents.agents.utils.fundamental_data_tools import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
    # THS additions (A-share comprehensive)
    get_financial_indicators,
    get_revenue_breakdown,
    get_dividend_history,
    get_profit_forecast,
)
from tradingagents.agents.utils.news_data_tools import (
    get_news,
    get_insider_transactions,
    get_global_news,
    get_announcements,
    get_stock_hot_rank,
    get_shareholder_count,
    get_research_reports,
    # THS additions (A-share comprehensive)
    get_concept_tags,
    get_stock_comment,
    get_xueqiu_hot,
    get_investor_qa,
    get_performance_briefing,
    get_restricted_release,
    get_pledge_ratio,
)
from tradingagents.agents.utils.capital_flow_tools import (
    get_lhb_detail,
    get_lhb_institutional,
    get_north_capital_individual,
    get_north_capital_overall,
    get_margin_trading,
    get_fund_flow,
    # THS additions (A-share comprehensive)
    get_top10_holders,
    get_top10_free_holders,
    get_concerted_action,
    get_block_trade,
    get_shareholder_change,
    get_management_change,
)


def get_language_instruction() -> str:
    """Return a prompt instruction for the configured output language.

    Returns empty string when English (default), so no extra tokens are used.
    Applied to every agent whose output reaches the saved report —
    analysts, researchers, debaters, research manager, trader, and
    portfolio manager — so a non-English run produces a fully localized
    report rather than a mix of languages.
    """
    from tradingagents.dataflows.config import get_config
    lang = get_config().get("output_language", "English")
    if lang.strip().lower() == "english":
        return ""
    return f" Write your entire response in {lang}."


def build_instrument_context(ticker: str) -> str:
    """Describe the exact instrument so agents preserve exchange-qualified tickers."""
    return (
        f"The instrument to analyze is `{ticker}`. "
        "Use this exact ticker in every tool call, report, and recommendation, "
        "preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`)."
    )

def create_msg_delete():
    def delete_messages(state):
        """Clear messages and add placeholder for Anthropic compatibility"""
        messages = state["messages"]

        # Remove all messages
        removal_operations = [RemoveMessage(id=m.id) for m in messages]

        # Add a minimal placeholder message
        placeholder = HumanMessage(content="Continue")

        return {"messages": removal_operations + [placeholder]}

    return delete_messages


        
