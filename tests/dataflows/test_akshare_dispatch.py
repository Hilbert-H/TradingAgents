import pytest
from unittest.mock import patch
from tradingagents.dataflows.akshare_common import NotApplicableError
from tradingagents.dataflows import interface


def _stub_vendor(payload):
    def fn(*_a, **_k):
        return payload
    return fn


def _failing_vendor(exc):
    def fn(*_a, **_k):
        raise exc
    return fn


def test_detect_market_a_share():
    assert interface._detect_market("600487.SS") == "a_share"
    assert interface._detect_market("000001.SZ") == "a_share"
    assert interface._detect_market("600487.ss") == "a_share"


def test_detect_market_global():
    assert interface._detect_market("NVDA") == "global"
    assert interface._detect_market("") == "global"
    assert interface._detect_market(None) == "global"


def test_route_to_vendor_a_share_picks_akshare_first(monkeypatch):
    """For A-share tickers, akshare is the primary vendor regardless of config."""
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"],
                        "akshare", _stub_vendor("akshare_payload"))
    monkeypatch.setattr(interface, "get_config",
                        lambda: {"data_vendors": {"news_data": "yfinance"},
                                 "tool_vendors": {}})
    result = interface.route_to_vendor("get_news", "600487.SS", "2026-05-01", "2026-05-08")
    assert result == "akshare_payload"


def test_route_to_vendor_global_ticker_honours_config(monkeypatch):
    monkeypatch.setattr(interface, "get_config",
                        lambda: {"data_vendors": {"news_data": "yfinance"},
                                 "tool_vendors": {}})
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"],
                        "yfinance", _stub_vendor("yfinance_payload"))
    result = interface.route_to_vendor("get_news", "NVDA", "2026-05-01", "2026-05-08")
    assert result == "yfinance_payload"


def test_route_to_vendor_tool_override_beats_auto_routing(monkeypatch):
    """User's `tool_vendors` override still wins even for A-share tickers."""
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"],
                        "yfinance", _stub_vendor("yf_forced"))
    monkeypatch.setattr(interface, "get_config",
                        lambda: {"data_vendors": {},
                                 "tool_vendors": {"get_news": "yfinance"}})
    result = interface.route_to_vendor("get_news", "600487.SS", "2026-05-01", "2026-05-08")
    assert result == "yf_forced"


def test_route_to_vendor_not_applicable_falls_back_to_next(monkeypatch):
    """NotApplicableError skips a vendor and continues the chain.

    alpha_vantage is also stubbed (to NotApplicableError) because the real
    alpha_vantage implementation returns an error STRING rather than raising
    on API failure — without this stub the chain would 'succeed' at
    alpha_vantage and never reach the yfinance fallback this test verifies.
    """
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"],
                        "akshare", _failing_vendor(NotApplicableError("nope")))
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"],
                        "alpha_vantage", _failing_vendor(NotApplicableError("not applicable")))
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"],
                        "yfinance", _stub_vendor("yf_fallback"))
    monkeypatch.setattr(interface, "get_config",
                        lambda: {"data_vendors": {"news_data": "yfinance"},
                                 "tool_vendors": {}})
    # Forces an A-share ticker -> akshare first, but akshare raises NotApplicable
    result = interface.route_to_vendor("get_news", "600487.SS", "2026-05-01", "2026-05-08")
    assert result == "yf_fallback"


def test_route_to_vendor_returns_na_string_when_all_not_applicable(monkeypatch):
    """If every vendor in chain raises NotApplicableError, return 'N/A: ...' string."""
    monkeypatch.setitem(interface.VENDOR_METHODS, "get_announcements_test",
                        {"akshare": _failing_vendor(NotApplicableError("only A-share"))})
    monkeypatch.setitem(interface.TOOLS_CATEGORIES, "news_data",
                        {**interface.TOOLS_CATEGORIES["news_data"],
                         "tools": interface.TOOLS_CATEGORIES["news_data"]["tools"] + ["get_announcements_test"]})
    monkeypatch.setattr(interface, "get_config", lambda: {"data_vendors": {}, "tool_vendors": {}})

    result = interface.route_to_vendor("get_announcements_test", "NVDA")
    assert isinstance(result, str)
    assert result.startswith("N/A:")
    assert "NVDA" in result


def test_route_to_vendor_returns_data_unavailable_when_all_error(monkeypatch):
    """If every vendor raises non-NotApplicable errors, return 'Data unavailable: ...'."""
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"],
                        "akshare", _failing_vendor(RuntimeError("timeout")))
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"],
                        "yfinance", _failing_vendor(RuntimeError("rate limit")))
    # alpha_vantage also present in real registry; stub it failing too
    monkeypatch.setitem(interface.VENDOR_METHODS["get_news"],
                        "alpha_vantage", _failing_vendor(RuntimeError("api err")))
    monkeypatch.setattr(interface, "get_config", lambda: {"data_vendors": {"news_data": "yfinance"}, "tool_vendors": {}})

    result = interface.route_to_vendor("get_news", "NVDA", "2026-05-01", "2026-05-08")
    assert isinstance(result, str)
    assert result.startswith("Data unavailable")


def test_route_to_vendor_no_vendor_registered_returns_data_unavailable(monkeypatch):
    """If a method is registered but has no usable vendor entry, return a clear
    'no vendor registered' message rather than the misleading 'N/A' string."""
    monkeypatch.setitem(interface.VENDOR_METHODS, "phantom_method", {})
    monkeypatch.setitem(
        interface.TOOLS_CATEGORIES, "news_data",
        {**interface.TOOLS_CATEGORIES["news_data"],
         "tools": interface.TOOLS_CATEGORIES["news_data"]["tools"] + ["phantom_method"]},
    )
    monkeypatch.setattr(interface, "get_config", lambda: {"data_vendors": {}, "tool_vendors": {}})
    result = interface.route_to_vendor("phantom_method", "NVDA")
    assert isinstance(result, str)
    assert "no vendor registered" in result.lower()
    assert "phantom_method" in result
