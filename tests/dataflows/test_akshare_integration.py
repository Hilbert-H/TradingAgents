"""Network-bound integration tests for the akshare vendor.

Run with: pytest tests/dataflows/test_akshare_integration.py -m integration -v
Skip with: pytest tests/dataflows/test_akshare_integration.py -m "not integration"
"""

import pytest

from tradingagents.dataflows.akshare_common import NotApplicableError

# Conventions:
# - 600487.SS (Hengtong Optic-Electric) is the canonical Shanghai test ticker
# - 000001.SZ (Ping An Bank) is the canonical Shenzhen test ticker
# - "2026-05-08" (a Friday) is the canonical recent trade date
# - "NVDA" is the canonical non-A-share ticker for NotApplicable checks
TEST_TICKER_SH = "600487.SS"
TEST_TICKER_SZ = "000001.SZ"
TEST_DATE = "2026-05-08"

pytestmark = pytest.mark.integration


from tradingagents.dataflows.akshare_market import get_stock_akshare


def test_get_stock_akshare_returns_markdown_with_ohlcv():
    out = get_stock_akshare(TEST_TICKER_SH, "2026-04-01", TEST_DATE)
    assert isinstance(out, str)
    assert "##" in out                  # markdown heading
    assert "600487" in out
    # OHLCV columns should be present (akshare uses Chinese headers; we
    # normalise to English in the implementation)
    assert any(col in out for col in ("Open", "open", "开盘"))


def test_get_stock_akshare_raises_for_non_a_share():
    with pytest.raises(NotApplicableError):
        get_stock_akshare("NVDA", "2026-04-01", TEST_DATE)


from tradingagents.dataflows.akshare_market import get_indicator_akshare


def test_get_indicator_akshare_returns_indicator_values():
    out = get_indicator_akshare(TEST_TICKER_SH, "close_50_sma", "2026-05-08", 30)
    assert isinstance(out, str)
    assert "close_50_sma" in out or "50 SMA" in out
    assert "2026-" in out


from tradingagents.dataflows.akshare_market import get_insider_transactions_akshare


def test_get_insider_transactions_akshare_returns_markdown():
    out = get_insider_transactions_akshare(TEST_TICKER_SH, TEST_DATE)
    assert isinstance(out, str)
    assert "##" in out
    # Either there's recent insider activity (table with rows) or a "No data" note
    assert "600487" in out or "No data" in out


from tradingagents.dataflows.akshare_news import get_news_akshare


def test_get_news_akshare_returns_articles():
    out = get_news_akshare(TEST_TICKER_SH, "2026-04-15", TEST_DATE)
    assert isinstance(out, str)
    assert "##" in out


from tradingagents.dataflows.akshare_news import get_global_news_akshare


def test_get_global_news_akshare_returns_articles():
    out = get_global_news_akshare(TEST_DATE, look_back_days=2, limit=10)
    assert isinstance(out, str)
    assert "##" in out


from tradingagents.dataflows.akshare_news import get_announcements_akshare


def test_get_announcements_akshare_returns_markdown():
    out = get_announcements_akshare(TEST_TICKER_SH, "2026-04-01", TEST_DATE)
    assert isinstance(out, str)
    assert "##" in out


def test_get_announcements_akshare_raises_for_non_a_share():
    from tradingagents.dataflows.akshare_common import NotApplicableError
    with pytest.raises(NotApplicableError):
        get_announcements_akshare("NVDA", "2026-04-01", TEST_DATE)


from tradingagents.dataflows.akshare_sentiment import get_stock_hot_rank_akshare


def test_get_stock_hot_rank_akshare_returns_rank_info():
    out = get_stock_hot_rank_akshare(TEST_TICKER_SH, TEST_DATE)
    assert isinstance(out, str)
    assert "##" in out


from tradingagents.dataflows.akshare_sentiment import get_shareholder_count_akshare


def test_get_shareholder_count_akshare_returns_history():
    out = get_shareholder_count_akshare(TEST_TICKER_SH, TEST_DATE)
    assert isinstance(out, str)
    assert "##" in out


from tradingagents.dataflows.akshare_sentiment import get_research_reports_akshare


def test_get_research_reports_akshare_returns_recent():
    out = get_research_reports_akshare(TEST_TICKER_SH, "2026-01-01", TEST_DATE)
    assert isinstance(out, str)
    assert "##" in out


from tradingagents.dataflows.akshare_fundamentals import get_fundamentals_akshare


def test_get_fundamentals_akshare_returns_summary():
    out = get_fundamentals_akshare(TEST_TICKER_SH, TEST_DATE)
    assert isinstance(out, str)
    assert "##" in out
    assert "600487" in out or "亨通" in out
