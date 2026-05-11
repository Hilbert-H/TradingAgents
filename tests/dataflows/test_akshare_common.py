import pytest
import pandas as pd
from tradingagents.dataflows.akshare_common import (
    NotApplicableError,
    is_a_share,
    to_ak_symbol,
    to_ak_symbol_with_market,
    ak_retry,
    format_df_as_md,
)


@pytest.mark.parametrize("ticker,expected", [
    ("600487.SS", True),
    ("000001.SZ", True),
    ("600487.ss", True),     # case-insensitive
    ("  600487.SS ", False), # whitespace not stripped — strict
    ("NVDA", False),
    ("", False),
    (None, False),
])
def test_is_a_share(ticker, expected):
    assert is_a_share(ticker) is expected


def test_to_ak_symbol_strips_suffix():
    assert to_ak_symbol("600487.SS") == "600487"
    assert to_ak_symbol("000001.SZ") == "000001"


def test_to_ak_symbol_rejects_non_a_share():
    with pytest.raises(NotApplicableError):
        to_ak_symbol("NVDA")


@pytest.mark.parametrize("ticker,expected", [
    ("600487.SS", "SH600487"),
    ("601318.SS", "SH601318"),
    ("000001.SZ", "SZ000001"),
    ("300750.SZ", "SZ300750"),
])
def test_to_ak_symbol_with_market_main_boards(ticker, expected):
    assert to_ak_symbol_with_market(ticker) == expected


@pytest.mark.parametrize("ticker", ["830000.SS", "899050.SZ", "430000.SZ"])
def test_to_ak_symbol_with_market_rejects_bse(ticker):
    """北交所 (4 / 8 prefix) is out of scope per spec."""
    with pytest.raises(NotApplicableError):
        to_ak_symbol_with_market(ticker)


def test_ak_retry_succeeds_eventually():
    calls = {"n": 0}

    @ak_retry(max_attempts=3, base_delay=0.01)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("network blip")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_ak_retry_exhausts_and_reraises():
    @ak_retry(max_attempts=2, base_delay=0.01)
    def always_fails():
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        always_fails()


def test_ak_retry_does_not_retry_not_applicable():
    calls = {"n": 0}

    @ak_retry(max_attempts=3, base_delay=0.01)
    def not_applicable():
        calls["n"] += 1
        raise NotApplicableError("wrong market")

    with pytest.raises(NotApplicableError):
        not_applicable()
    assert calls["n"] == 1   # not retried


def test_format_df_as_md_empty():
    assert "No data" in format_df_as_md(None, "Title")
    assert "No data" in format_df_as_md(pd.DataFrame(), "Title")


def test_format_df_as_md_truncates():
    df = pd.DataFrame({"a": list(range(100))})
    out = format_df_as_md(df, "Top", max_rows=5)
    assert "## Top" in out
    assert "0" in out
    assert "4" in out
    assert "5" not in out.split("|")[-1]  # row 5 absent
