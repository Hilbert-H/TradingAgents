from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_balance_sheet,
    get_cashflow,
    get_dividend_history,
    get_financial_indicators,
    get_fundamentals,
    get_income_statement,
    get_insider_transactions,
    get_language_instruction,
    get_profit_forecast,
    get_revenue_breakdown,
)
from tradingagents.dataflows.akshare_common import is_a_share
from tradingagents.dataflows.config import get_config


def create_fundamentals_analyst(llm):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = build_instrument_context(state["company_of_interest"])

        # A-share branch picks up four extra Tonghuashun-backed tools that
        # together replace ~5 pages of manual F10 reading: full 86-indicator
        # ratio history, revenue split by 行业 / 产品 / 地区, complete dividend
        # plan history, and 27-broker consensus EPS forecast.
        ticker = state["company_of_interest"]
        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]
        a_share_tools_blurb = ""
        if is_a_share(ticker):
            tools.extend([
                get_financial_indicators,
                get_revenue_breakdown,
                get_dividend_history,
                get_profit_forecast,
            ])
            a_share_tools_blurb = (
                " For A-share tickers you additionally have: "
                "`get_financial_indicators` (86-column ratio series since 2018 — "
                "ROE / margin / turnover / leverage at every reporting period), "
                "`get_revenue_breakdown` (revenue split by 行业 / 产品 / 地区 with "
                "per-segment gross margin — essential for mix-shift analysis), "
                "`get_dividend_history` (董事会日 / 登记日 / 除权日 / 分红总额 — "
                "shareholder-return track record), and "
                "`get_profit_forecast` (number of forecasting brokers + min / "
                "mean / max projected EPS for next 2-3 years + industry average — "
                "use the mean as a consensus anchor)."
            )

        system_message = (
            "You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders. Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions."
            + " Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
            + a_share_tools_blurb
            + get_language_instruction(),
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
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
