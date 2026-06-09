from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_lhb_detail,
    get_lhb_institutional,
    get_north_capital_individual,
    get_north_capital_overall,
    get_margin_trading,
    get_fund_flow,
    # THS holder additions
    get_top10_holders,
    get_top10_free_holders,
    get_concerted_action,
    get_block_trade,
    get_shareholder_change,
    get_management_change,
)
from tradingagents.dataflows.akshare_common import is_a_share


def create_capital_flow_analyst(llm):
    def capital_flow_analyst_node(state):
        ticker = state["company_of_interest"]
        current_date = state["trade_date"]

        # Short-circuit for non-A-share tickers: do not call tools, do not spend tokens
        if not is_a_share(ticker):
            return {
                "capital_flow_report": (
                    f"N/A: {ticker} is not an A-share ticker; "
                    "capital_flow analysis is skipped."
                ),
                "messages": [],
            }

        instrument_context = build_instrument_context(ticker)
        tools = [
            # Flow-based (turnover-side) signals
            get_lhb_detail,
            get_lhb_institutional,
            get_north_capital_individual,
            get_north_capital_overall,
            get_margin_trading,
            get_fund_flow,
            # Stock-based (positioning-side) signals — THS additions
            get_top10_holders,
            get_top10_free_holders,
            get_concerted_action,
            get_block_trade,
            get_shareholder_change,
            get_management_change,
        ]

        system_message = (
            "You are the Capital Flow Analyst for A-share equity {ticker} on {current_date}.\n\n"
            "Read both the FLOW signals (who is trading right now) and the STOCK signals\n"
            "(who is sitting on the shares) for A-share equity {ticker}.\n\n"
            "FLOW signals (short-term, 1-10 days):\n"
            "1. Dragon-Tiger List (龙虎榜): which institutional / hot-money seats bought or\n"
            "   sold; institutional net flow trend over recent days.\n"
            "2. Northbound Capital (北上资金): foreign Stock-Connect holding changes for\n"
            "   this ticker; overall market net flow as a market-mood proxy.\n"
            "3. Margin Trading (融资融券): financing balance = retail leverage sentiment;\n"
            "   securities-lending balance = short-interest proxy.\n"
            "4. Smart-Money Flow (主力资金流向): today's super-large / large / medium /\n"
            "   small order net flows — who is accumulating vs distributing.\n"
            "5. Block Trades (大宗交易): recent off-exchange institutional trades with\n"
            "   premium/discount — directional signal from buyer/seller seats.\n\n"
            "STOCK signals (medium-term, positioning context):\n"
            "6. Top-10 Holders (十大股东): latest quarter-end control structure.\n"
            "7. Top-10 Free Holders (十大流通股东): the actual tradable-float holders.\n"
            "8. Concerted Action (一致行动人): combined control %. > 30% indicates\n"
            "   strong control; high concentration limits free-float liquidity.\n"
            "9. Shareholder Change (大股东增减持): recent transactions by large holders\n"
            "   — directional signal from informationally-edged participants.\n"
            "10. Management Change (高管增减持): insider equivalent for A-share.\n\n"
            "Required output:\n"
            "- One-line capital posture (accumulating / distributing / neutral).\n"
            "- Per-signal section with concrete numbers and 1-week trend (for flow signals)\n"
            "  or latest snapshot + delta vs prior period (for stock signals).\n"
            "- A capital-flow confidence rating: Strong Bullish / Bullish / Neutral /\n"
            "  Bearish / Strong Bearish.\n"
            "- Key risks visible in the data (e.g. retail leverage at multi-year high →\n"
            "  squeeze risk; concentration with high pledge ratio → forced-sale risk).\n\n"
            "This is short/medium-term flow + positioning analysis (1-30 day horizon).\n"
            "DO NOT make long-term valuation calls — that is the fundamentals analyst's job."
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
                    " You have access to the following tools: {tool_names}.\n{system_message}"
                    " For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([t.name for t in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)
        prompt = prompt.partial(ticker=ticker)

        chain = prompt | llm.bind_tools(tools)
        result = chain.invoke(state["messages"])

        report = ""
        if len(result.tool_calls) == 0:
            report = result.content

        return {"capital_flow_report": report, "messages": [result]}

    return capital_flow_analyst_node
