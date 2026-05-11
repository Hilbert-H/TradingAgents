"""Graph-level integration test for the capital_flow analyst.

Uses a mock LLM that emits a deterministic AIMessage (no tool calls), so the
graph's ReAct loop terminates immediately and we can verify state shape.
This test is hermetic — no network calls to real LLM APIs.
The capital_flow analyst's non-A-share short-circuit doesn't even hit the LLM.
"""

import pytest
from unittest.mock import patch, MagicMock
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable

from tradingagents.default_config import DEFAULT_CONFIG


class _FakeLLM(Runnable):
    """Stand-in for a real LLM.

    Inherits from Runnable so LCEL ``prompt | llm`` chains work.
    Returns a fixed AIMessage with no tool_calls on .invoke(), causing the
    ReAct conditional-edge logic to route to the Msg Clear node (loop exit).
    """

    def __init__(self, content="capital posture: neutral. **Rating**: Hold."):
        self.content = content
        self._bound_tools = []

    # --- LangChain Runnable interface ---

    def invoke(self, input, config=None, **kwargs):
        return AIMessage(content=self.content, tool_calls=[])

    # --- LLM-specific helpers used by agents ---

    def bind_tools(self, tools, **kwargs):
        """Return self; the bound-tools stub still returns a no-tool-call AIMessage."""
        clone = _FakeLLM(content=self.content)
        clone._bound_tools = list(tools)
        return clone

    def with_structured_output(self, schema, **kwargs):
        """Return a stub whose .invoke() raises so the fallback free-text path fires."""
        raise NotImplementedError("_FakeLLM does not support structured output")


def _build_graph(selected_analysts):
    """Build a TradingAgentsGraph with the LLM client and memory log patched out."""
    cfg = DEFAULT_CONFIG.copy()
    cfg["llm_provider"] = "deepseek"
    cfg["deep_think_llm"] = "deepseek-v4-pro"
    cfg["quick_think_llm"] = "deepseek-v4-flash"
    # Keep debate rounds to the minimum so the graph finishes fast
    cfg["max_debate_rounds"] = 1
    cfg["max_risk_discuss_rounds"] = 1

    fake_llm = _FakeLLM()
    fake_client = MagicMock()
    fake_client.get_llm.return_value = fake_llm

    with patch(
        "tradingagents.graph.trading_graph.create_llm_client",
        return_value=fake_client,
    ), patch(
        "tradingagents.graph.trading_graph.TradingMemoryLog"
    ) as mock_mem_cls:
        mock_mem = mock_mem_cls.return_value
        mock_mem.get_pending_entries.return_value = []
        mock_mem.get_past_context.return_value = ""
        mock_mem.store_decision = lambda **_k: None
        mock_mem.batch_update_with_outcomes = lambda updates: None

        from tradingagents.graph.trading_graph import TradingAgentsGraph

        ta = TradingAgentsGraph(
            debug=False,
            selected_analysts=selected_analysts,
            config=cfg,
        )
        return ta


def test_capital_flow_analyst_non_a_share_returns_na():
    """For a US ticker, capital_flow analyst short-circuits with N/A — no LLM call."""
    ta = _build_graph(["capital_flow"])
    final_state, _decision = ta.propagate("NVDA", "2026-05-08")
    assert "capital_flow_report" in final_state
    assert final_state["capital_flow_report"].startswith("N/A:")


def test_capital_flow_analyst_a_share_produces_report():
    """For an A-share ticker, capital_flow analyst writes a non-empty report.

    The fake LLM returns a single AIMessage with no tool calls, so the report
    is just the LLM's content.  We only verify the field is populated and
    does NOT start with the N/A placeholder.
    """
    ta = _build_graph(["capital_flow"])
    final_state, _decision = ta.propagate("600487.SS", "2026-05-08")
    assert "capital_flow_report" in final_state
    assert final_state["capital_flow_report"]
    assert not final_state["capital_flow_report"].startswith("N/A:")
