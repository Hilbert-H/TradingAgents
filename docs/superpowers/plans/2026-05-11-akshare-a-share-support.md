# A-Share Support via Akshare — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add A-share (Shanghai / Shenzhen) support to TradingAgents by adding `akshare` as a new vendor, registering 10 new tool methods (1 announcement + 3 sentiment + 6 capital-flow), introducing a new `capital_flow_analyst` as a 5th graph node, and making vendor dispatch ticker-aware so `.SS` / `.SZ` tickers auto-route to akshare.

**Architecture:** Akshare is wrapped as a new vendor alongside yfinance and alpha_vantage. `route_to_vendor` gains a ticker-suffix detection layer that forces A-share tickers to akshare. New data modules live next to existing `y_finance.py` and `alpha_vantage.py`. The new analyst plugs into the existing graph-setup loop the same way the 4 existing analysts do (selected_analysts opt-in).

**Tech Stack:** Python 3.11, akshare>=1.18, langgraph, langchain-core, pytest.

**Spec reference:** [`docs/superpowers/specs/2026-05-11-akshare-a-share-support-design.md`](../specs/2026-05-11-akshare-a-share-support-design.md)

**Critical pre-implementation notes:**
- Tests live flat in `tests/`; this plan creates a `tests/dataflows/` subdirectory for akshare-specific tests, following the existing pattern of grouping by feature where useful.
- Tool wrappers (the `@tool`-decorated functions analysts import) live in per-category files: `tradingagents/agents/utils/{core_stock_tools,technical_indicators_tools,fundamental_data_tools,news_data_tools}.py`. New methods get added there, re-exported via `agent_utils.py`.
- `agent_states.AgentState` uses `Annotated[str, "..."]` fields, not pydantic.
- `setup.py` already loops over `selected_analysts` and looks up `should_continue_<name>` via `getattr` — so adding `"capital_flow"` to that list "just works" once the analyst factory + conditional logic + tool node are wired in.

---

## File Structure

**New files (13):**

```
tradingagents/dataflows/
  akshare_common.py            # helpers, errors, retry, formatting
  akshare_market.py            # stock, indicator, insider
  akshare_news.py              # news, global_news, announcements
  akshare_sentiment.py         # hot_rank, shareholder_count, research_reports
  akshare_fundamentals.py      # fundamentals, balance_sheet, cashflow, income_stmt
  akshare_capital_flow.py      # 6 capital-flow methods

tradingagents/agents/analysts/
  capital_flow_analyst.py      # 5th analyst

tradingagents/agents/utils/
  capital_flow_tools.py        # 6 tool wrappers for capital_flow analyst

tests/dataflows/
  __init__.py                  # empty, makes it a package
  test_akshare_common.py       # pure unit (no network)
  test_akshare_dispatch.py     # pure unit (no network)
  test_akshare_integration.py  # network-bound, single file covering all akshare modules

tests/graph/
  __init__.py                  # empty
  test_capital_flow_integration.py  # mock-LLM end-to-end
```

**Modified files (12):**

```
pyproject.toml                                              # add akshare>=1.18
tradingagents/dataflows/__init__.py                         # exports
tradingagents/dataflows/interface.py                        # ticker-aware dispatch; terminal-failure-as-string; register akshare
tradingagents/agents/utils/news_data_tools.py               # +get_announcements +3 sentiment tools
tradingagents/agents/utils/agent_utils.py                   # re-export new tools
tradingagents/agents/utils/agent_states.py                  # +capital_flow_report
tradingagents/agents/analysts/social_media_analyst.py       # +3 sentiment tools; A-share-conditional prompt
tradingagents/agents/analysts/news_analyst.py               # +get_announcements; A-share prompt note
tradingagents/agents/__init__.py                            # export create_capital_flow_analyst
tradingagents/graph/conditional_logic.py                    # +should_continue_capital_flow
tradingagents/graph/setup.py                                # wire capital_flow analyst into loop
tradingagents/graph/propagation.py                          # default capital_flow_report=""
tradingagents/graph/trading_graph.py                        # tool nodes + log state
tradingagents/agents/researchers/{bull,bear}_researcher.py  # include capital_flow_report
tradingagents/agents/trader/trader.py                       # include capital_flow_report
tradingagents/agents/risk_mgmt/{aggressive,conservative,neutral}_debator.py
tradingagents/agents/managers/{research,portfolio}_manager.py
README.md                                                   # A-share section
run_deepseek.py                                             # selected_analysts="capital_flow"; comment
```

---

## Phase A — Foundations & dispatch

### Task 1: Add akshare dependency

**Files:**
- Modify: `pyproject.toml:11-33`

- [ ] **Step 1: Edit `pyproject.toml`** — append `"akshare>=1.18.0",` to the dependencies array (alphabetical fit: between `"parsel"` and `"pandas"` or wherever convenient; alphabetical isn't enforced in the existing file).

  ```toml
  dependencies = [
      "langchain-core>=0.3.81",
      ...existing...
      "akshare>=1.18.0",
      ...rest...
  ]
  ```

- [ ] **Step 2: Install into the existing venv**

  ```bash
  .venv/bin/pip install -e .
  ```

  Expected: `Successfully installed akshare-<version>` plus its transitive deps (mostly pandas/lxml — already present).

- [ ] **Step 3: Smoke-import akshare**

  ```bash
  .venv/bin/python -c "import akshare as ak; print(ak.__version__)"
  ```

  Expected: prints a version string like `1.18.x`. If it fails with a sub-dep error (akshare has many), pin the failing sub-dep and re-install.

- [ ] **Step 4: Commit**

  ```bash
  git add pyproject.toml
  git commit -m "deps: add akshare>=1.18.0 for A-share data support"
  ```

---

### Task 2: `akshare_common.py` — helpers, errors, retry

**Files:**
- Create: `tradingagents/dataflows/akshare_common.py`
- Create: `tests/dataflows/__init__.py` (empty)
- Create: `tests/dataflows/test_akshare_common.py`

- [ ] **Step 1: Create the empty package init**

  ```bash
  touch tests/dataflows/__init__.py
  ```

- [ ] **Step 2: Write the failing tests** — create `tests/dataflows/test_akshare_common.py`:

  ```python
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
  ```

- [ ] **Step 3: Run tests to confirm they fail**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_common.py -v
  ```

  Expected: ImportError / ModuleNotFoundError for `akshare_common`.

- [ ] **Step 4: Implement `akshare_common.py`** — create with:

  ```python
  """Shared helpers for the akshare vendor implementations."""

  import logging
  import time
  from functools import wraps
  from typing import Optional

  import pandas as pd

  logger = logging.getLogger(__name__)


  class NotApplicableError(Exception):
      """Raised when a vendor cannot serve a ticker (e.g. akshare for US stocks).

      Distinct from regular errors so the dispatch layer can route the call
      to the next vendor (or, when no other vendor implements the method,
      surface a clean "N/A" string to the agent).
      """


  A_SHARE_SUFFIXES = (".SS", ".SZ")


  def is_a_share(ticker: Optional[str]) -> bool:
      if not ticker:
          return False
      return ticker.upper().endswith(A_SHARE_SUFFIXES)


  def to_ak_symbol(ticker: str) -> str:
      """600487.SS -> '600487'. Most akshare endpoints take the bare 6-digit code."""
      if not is_a_share(ticker):
          raise NotApplicableError(f"{ticker!r} is not an A-share ticker")
      return ticker.split(".", 1)[0]


  def to_ak_symbol_with_market(ticker: str) -> str:
      """600487.SS -> 'SH600487'. Some akshare endpoints want the exchange prefix.

      Prefix rules: 6 -> SH (Shanghai main + STAR);
                    0 / 3 -> SZ (Shenzhen main + ChiNext);
                    4 / 8 -> Beijing Stock Exchange (out of scope, raises).
      """
      code = to_ak_symbol(ticker)
      if not code or not code[0].isdigit():
          raise NotApplicableError(f"{ticker!r} has no recognisable market prefix")
      first = code[0]
      if first == "6":
          return f"SH{code}"
      if first in ("0", "3"):
          return f"SZ{code}"
      if first in ("4", "8"):
          raise NotApplicableError(
              f"{ticker!r} appears to be a Beijing Stock Exchange ticker, "
              "which is out of scope for this vendor."
          )
      raise NotApplicableError(f"{ticker!r} has an unrecognised market prefix '{first}'")


  def ak_retry(max_attempts: int = 3, base_delay: float = 1.0):
      """Decorator: retry on transient errors with exponential backoff.

      `NotApplicableError` is never retried — that's a permanent classification
      error, not a transient failure.
      """
      def deco(fn):
          @wraps(fn)
          def wrapper(*args, **kwargs):
              last_exc = None
              for attempt in range(max_attempts):
                  try:
                      return fn(*args, **kwargs)
                  except NotApplicableError:
                      raise
                  except Exception as e:
                      last_exc = e
                      if attempt < max_attempts - 1:
                          delay = base_delay * (2 ** attempt)
                          logger.warning(
                              "akshare call %s failed (attempt %d/%d): %s; retrying in %.1fs",
                              fn.__name__, attempt + 1, max_attempts, e, delay,
                          )
                          time.sleep(delay)
              logger.error("akshare call %s exhausted retries: %s", fn.__name__, last_exc)
              raise last_exc
          return wrapper
      return deco


  def format_df_as_md(df: Optional[pd.DataFrame], title: str, max_rows: int = 30) -> str:
      """Render a DataFrame as a markdown section for LLM consumption.

      Returns a "No data" message if df is None or empty. Truncates rows past max_rows.
      """
      if df is None or df.empty:
          return f"## {title}\n\n_No data available._"
      truncated = df.head(max_rows)
      try:
          body = truncated.to_markdown(index=False)
      except ImportError:
          # to_markdown needs `tabulate`; fall back to to_string
          body = truncated.to_string(index=False)
      return f"## {title}\n\n{body}"
  ```

- [ ] **Step 5: Run tests to confirm pass**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_common.py -v
  ```

  Expected: all green. If `to_markdown` fails because `tabulate` isn't installed, install it: `.venv/bin/pip install tabulate` (it's typically installed transitively by pandas-related packages, but akshare may not pull it in).

- [ ] **Step 6: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_common.py tests/dataflows/__init__.py tests/dataflows/test_akshare_common.py
  git commit -m "feat(dataflows): akshare_common helpers (NotApplicableError, ticker utils, retry, formatter)"
  ```

---

### Task 3: Ticker-aware dispatch + terminal-failure-as-string in `interface.py`

**Files:**
- Modify: `tradingagents/dataflows/interface.py:134-162` (replace `route_to_vendor`)
- Create: `tests/dataflows/test_akshare_dispatch.py`

- [ ] **Step 1: Write the failing tests** — create `tests/dataflows/test_akshare_dispatch.py`:

  ```python
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
      monkeypatch.setitem(interface.VENDOR_METHODS["get_news"],
                          "akshare", _failing_vendor(NotApplicableError("nope")))
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
  ```

- [ ] **Step 2: Run tests to confirm they fail**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_dispatch.py -v
  ```

  Expected: AttributeError on `_detect_market`, or assertion errors because route_to_vendor doesn't handle these cases yet.

- [ ] **Step 3: Patch `tradingagents/dataflows/interface.py`** — replace the `route_to_vendor` function (current lines 134-162) and add a `_detect_market` helper. Final shape:

  ```python
  # ... existing imports ...
  import logging
  from .akshare_common import NotApplicableError

  logger = logging.getLogger(__name__)

  A_SHARE_SUFFIXES = (".SS", ".SZ")


  def _detect_market(ticker) -> str:
      """Return 'a_share' if ticker has Shanghai/Shenzhen suffix, else 'global'."""
      if not ticker or not isinstance(ticker, str):
          return "global"
      return "a_share" if ticker.upper().endswith(A_SHARE_SUFFIXES) else "global"


  def route_to_vendor(method: str, *args, **kwargs):
      """Route method calls to appropriate vendor with fallback support.

      Resolution order:
        1. If the first positional arg / `ticker` / `symbol` kwarg looks like
           an A-share ticker (`.SS` / `.SZ` suffix), force akshare as the
           primary vendor — unless the user has set a method-level
           `tool_vendors` override, which always wins.
        2. Otherwise, use the user-configured vendor for the category.
        3. Build a fallback chain: primary + every other available vendor.
        4. Walk the chain. Skip on AlphaVantageRateLimitError (existing
           behaviour). Skip on NotApplicableError (new). Skip on any other
           Exception with a warning log.
        5. If chain exhausted and every failure was NotApplicableError →
           return an "N/A: ..." string.
        6. If chain exhausted with at least one real error → return a
           "Data unavailable: ..." string.
      """
      if method not in VENDOR_METHODS:
          raise ValueError(f"Method '{method}' not supported")

      category = get_category_for_method(method)
      config = get_config()

      # Determine ticker (used both for A-share routing and for the N/A message)
      ticker = args[0] if args else (kwargs.get("ticker") or kwargs.get("symbol"))
      market = _detect_market(ticker)

      tool_override = config.get("tool_vendors", {}).get(method)
      if tool_override:
          primary_vendors = [tool_override]
      elif market == "a_share":
          primary_vendors = ["akshare"]
          logger.info("Ticker %s detected as A-share, routing %s to akshare", ticker, method)
      else:
          vendor_config = config.get("data_vendors", {}).get(category, "default")
          primary_vendors = [v.strip() for v in vendor_config.split(",")]

      # Build fallback chain
      all_available = list(VENDOR_METHODS[method].keys())
      fallback_vendors = list(primary_vendors)
      for vendor in all_available:
          if vendor not in fallback_vendors:
              fallback_vendors.append(vendor)

      seen_only_not_applicable = True
      last_error: Exception = None

      for vendor in fallback_vendors:
          if vendor not in VENDOR_METHODS[method]:
              continue
          vendor_impl = VENDOR_METHODS[method][vendor]
          impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl
          try:
              return impl_func(*args, **kwargs)
          except AlphaVantageRateLimitError as e:
              seen_only_not_applicable = False
              last_error = e
              continue
          except NotApplicableError as e:
              last_error = e
              continue
          except Exception as e:
              seen_only_not_applicable = False
              last_error = e
              logger.warning("vendor %s failed for method %s: %s", vendor, method, e)
              continue

      # Chain exhausted
      if seen_only_not_applicable:
          return (
              f"N/A: {method} is not supported for ticker {ticker!r}. "
              f"(All available vendors raised NotApplicableError; "
              f"this method typically requires an A-share ticker.)"
          )
      return f"Data unavailable: {method} failed across all vendors. Last error: {last_error}"
  ```

  The original `RuntimeError("No available vendor")` line is gone. The `get_vendor` helper is now inlined since its only caller is `route_to_vendor`; leave the standalone `get_vendor` function untouched in case other code paths use it (verify with `grep -rn "get_vendor" tradingagents/` — if no other callers, optionally delete; if any, leave alone).

- [ ] **Step 4: Run dispatch tests**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_dispatch.py -v
  ```

  Expected: all green.

- [ ] **Step 5: Run the full existing test suite to ensure no regression**

  ```bash
  .venv/bin/python -m pytest tests/ -v -m "not integration" 2>&1 | tail -40
  ```

  Expected: all green. If `test_dataflows_config.py` or anything else relies on the old `RuntimeError("No available vendor")` behaviour, fix it to expect the new string return (the spec change is intentional — terminal failures should not crash the graph).

- [ ] **Step 6: Commit**

  ```bash
  git add tradingagents/dataflows/interface.py tests/dataflows/test_akshare_dispatch.py
  git commit -m "feat(dataflows): ticker-aware vendor dispatch + terminal-failure-as-string

  - .SS/.SZ tickers auto-route to akshare unless user overrides via tool_vendors
  - chain exhaustion now returns a string instead of raising RuntimeError
  - NotApplicableError is distinct from other failures (drives the N/A message)"
  ```

---

### Task 4: Register `akshare` vendor + `capital_flow` category in `interface.py`

**Files:**
- Modify: `tradingagents/dataflows/interface.py:31-110` (TOOLS_CATEGORIES, VENDOR_LIST, VENDOR_METHODS)

This task only registers names — actual akshare function implementations come in Phase B. To keep dispatch testable now, we stub each akshare entry with a function that raises `NotImplementedError`; Phase B tasks replace each stub with the real implementation.

- [ ] **Step 1: Edit `tradingagents/dataflows/interface.py`** — add at top of file:

  ```python
  from .akshare_market import (
      get_stock_akshare,
      get_indicator_akshare,
      get_insider_transactions_akshare,
  )
  from .akshare_news import (
      get_news_akshare,
      get_global_news_akshare,
      get_announcements_akshare,
  )
  from .akshare_sentiment import (
      get_stock_hot_rank_akshare,
      get_shareholder_count_akshare,
      get_research_reports_akshare,
  )
  from .akshare_fundamentals import (
      get_fundamentals_akshare,
      get_balance_sheet_akshare,
      get_cashflow_akshare,
      get_income_statement_akshare,
  )
  from .akshare_capital_flow import (
      get_lhb_detail_akshare,
      get_lhb_institutional_akshare,
      get_north_capital_individual_akshare,
      get_north_capital_overall_akshare,
      get_margin_trading_akshare,
      get_fund_flow_akshare,
  )
  ```

  These modules don't exist yet — see Step 2.

- [ ] **Step 2: Create stub modules** — for each of the 5 akshare modules below, create a placeholder that exposes the function names but raises NotImplementedError. Phase B replaces these with real bodies. Example for `akshare_market.py`:

  ```python
  """Akshare implementations for the market data category. Phase B fills in bodies."""

  def get_stock_akshare(*_a, **_k):
      raise NotImplementedError("akshare_market.get_stock_akshare — Phase B Task 5")

  def get_indicator_akshare(*_a, **_k):
      raise NotImplementedError("akshare_market.get_indicator_akshare — Phase B Task 6")

  def get_insider_transactions_akshare(*_a, **_k):
      raise NotImplementedError("akshare_market.get_insider_transactions_akshare — Phase B Task 7")
  ```

  Create equivalent stub files for `akshare_news.py`, `akshare_sentiment.py`, `akshare_fundamentals.py`, `akshare_capital_flow.py` with their respective function names from Step 1.

- [ ] **Step 3: Add `capital_flow` to `TOOLS_CATEGORIES`** and extend `news_data`:

  ```python
  TOOLS_CATEGORIES = {
      "core_stock_apis": {
          "description": "OHLCV stock price data",
          "tools": ["get_stock_data"],
      },
      "technical_indicators": {
          "description": "Technical analysis indicators",
          "tools": ["get_indicators"],
      },
      "fundamental_data": {
          "description": "Company fundamentals",
          "tools": ["get_fundamentals", "get_balance_sheet",
                    "get_cashflow", "get_income_statement"],
      },
      "news_data": {
          "description": "News, announcements, insider, and A-share sentiment proxies",
          "tools": [
              "get_news", "get_global_news", "get_insider_transactions",
              "get_announcements",
              "get_stock_hot_rank", "get_shareholder_count", "get_research_reports",
          ],
      },
      "capital_flow": {
          "description": "A-share capital flow signals",
          "tools": [
              "get_lhb_detail", "get_lhb_institutional",
              "get_north_capital_individual", "get_north_capital_overall",
              "get_margin_trading", "get_fund_flow",
          ],
      },
  }
  ```

- [ ] **Step 4: Update `VENDOR_LIST`**

  ```python
  VENDOR_LIST = ["yfinance", "alpha_vantage", "akshare"]
  ```

- [ ] **Step 5: Update `VENDOR_METHODS`** — add `"akshare"` entries to every existing method, and add the 10 new methods:

  ```python
  VENDOR_METHODS = {
      "get_stock_data": {
          "alpha_vantage": get_alpha_vantage_stock,
          "yfinance": get_YFin_data_online,
          "akshare": get_stock_akshare,
      },
      "get_indicators": {
          "alpha_vantage": get_alpha_vantage_indicator,
          "yfinance": get_stock_stats_indicators_window,
          "akshare": get_indicator_akshare,
      },
      "get_fundamentals": {
          "alpha_vantage": get_alpha_vantage_fundamentals,
          "yfinance": get_yfinance_fundamentals,
          "akshare": get_fundamentals_akshare,
      },
      "get_balance_sheet": {
          "alpha_vantage": get_alpha_vantage_balance_sheet,
          "yfinance": get_yfinance_balance_sheet,
          "akshare": get_balance_sheet_akshare,
      },
      "get_cashflow": {
          "alpha_vantage": get_alpha_vantage_cashflow,
          "yfinance": get_yfinance_cashflow,
          "akshare": get_cashflow_akshare,
      },
      "get_income_statement": {
          "alpha_vantage": get_alpha_vantage_income_statement,
          "yfinance": get_yfinance_income_statement,
          "akshare": get_income_statement_akshare,
      },
      "get_news": {
          "alpha_vantage": get_alpha_vantage_news,
          "yfinance": get_news_yfinance,
          "akshare": get_news_akshare,
      },
      "get_global_news": {
          "yfinance": get_global_news_yfinance,
          "alpha_vantage": get_alpha_vantage_global_news,
          "akshare": get_global_news_akshare,
      },
      "get_insider_transactions": {
          "alpha_vantage": get_alpha_vantage_insider_transactions,
          "yfinance": get_yfinance_insider_transactions,
          "akshare": get_insider_transactions_akshare,
      },
      # New methods (akshare-only)
      "get_announcements":      {"akshare": get_announcements_akshare},
      "get_stock_hot_rank":     {"akshare": get_stock_hot_rank_akshare},
      "get_shareholder_count":  {"akshare": get_shareholder_count_akshare},
      "get_research_reports":   {"akshare": get_research_reports_akshare},
      "get_lhb_detail":              {"akshare": get_lhb_detail_akshare},
      "get_lhb_institutional":       {"akshare": get_lhb_institutional_akshare},
      "get_north_capital_individual":{"akshare": get_north_capital_individual_akshare},
      "get_north_capital_overall":   {"akshare": get_north_capital_overall_akshare},
      "get_margin_trading":          {"akshare": get_margin_trading_akshare},
      "get_fund_flow":               {"akshare": get_fund_flow_akshare},
  }
  ```

- [ ] **Step 6: Run dispatch tests + smoke imports**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_dispatch.py -v
  .venv/bin/python -c "from tradingagents.dataflows import interface; print(len(interface.VENDOR_METHODS))"
  ```

  Expected: tests pass; method count is 19 (9 existing + 10 new).

- [ ] **Step 7: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_market.py tradingagents/dataflows/akshare_news.py \
          tradingagents/dataflows/akshare_sentiment.py tradingagents/dataflows/akshare_fundamentals.py \
          tradingagents/dataflows/akshare_capital_flow.py tradingagents/dataflows/interface.py
  git commit -m "feat(dataflows): register akshare vendor + capital_flow category (stubs)"
  ```

---

## Phase B — Akshare data implementations

**Pattern for every task in this phase:**

1. Write a network-bound integration test in `tests/dataflows/test_akshare_integration.py` (one file accumulates all of them; mark them `@pytest.mark.integration`).
2. Run with `-m integration` and confirm it currently fails (NotImplementedError from the stub).
3. Replace the stub in the relevant `akshare_*.py` file with the real body.
4. Run the test again, confirm pass; verify the output contains expected field markers.
5. Commit.

**Test-file scaffold** — create `tests/dataflows/test_akshare_integration.py` once with this header (Task 5 creates it, every subsequent Phase B task appends a test):

```python
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
```

### Task 5: `akshare_market.get_stock_akshare`

**Files:**
- Modify: `tradingagents/dataflows/akshare_market.py`
- Modify: `tests/dataflows/test_akshare_integration.py`

- [ ] **Step 1: Append failing integration test**

  ```python
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
  ```

- [ ] **Step 2: Run to confirm fail**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_integration.py::test_get_stock_akshare_returns_markdown_with_ohlcv tests/dataflows/test_akshare_integration.py::test_get_stock_akshare_raises_for_non_a_share -v -m integration
  ```

  Expected: `NotImplementedError`.

- [ ] **Step 3: Replace stub in `akshare_market.py`** — full file becomes:

  ```python
  """Akshare implementations for market data: stock OHLCV, indicators, insider."""

  import logging
  from datetime import datetime

  import akshare as ak
  import pandas as pd

  from .akshare_common import (
      NotApplicableError,
      ak_retry,
      format_df_as_md,
      is_a_share,
      to_ak_symbol,
      to_ak_symbol_with_market,
  )

  logger = logging.getLogger(__name__)


  # Akshare returns Chinese column names by default; map to English for
  # downstream consistency.
  _STOCK_HIST_RENAME = {
      "日期": "Date", "开盘": "Open", "收盘": "Close",
      "最高": "High", "最低": "Low", "成交量": "Volume",
      "成交额": "Turnover", "振幅": "Amplitude",
      "涨跌幅": "ChgPct", "涨跌额": "Chg", "换手率": "TurnoverRate",
  }


  @ak_retry()
  def get_stock_akshare(ticker: str, start_date: str, end_date: str) -> str:
      """Daily OHLCV with forward-adjusted prices for an A-share ticker."""
      symbol = to_ak_symbol(ticker)
      # akshare expects yyyymmdd for these args
      start_compact = start_date.replace("-", "")
      end_compact = end_date.replace("-", "")
      df = ak.stock_zh_a_hist(
          symbol=symbol, period="daily",
          start_date=start_compact, end_date=end_compact,
          adjust="qfq",
      )
      if df is not None and not df.empty:
          df = df.rename(columns=_STOCK_HIST_RENAME)
      return format_df_as_md(df, f"{ticker} OHLCV {start_date} → {end_date}", max_rows=60)


  def get_indicator_akshare(*_a, **_k):
      raise NotImplementedError("akshare_market.get_indicator_akshare — Task 6")


  def get_insider_transactions_akshare(*_a, **_k):
      raise NotImplementedError("akshare_market.get_insider_transactions_akshare — Task 7")
  ```

- [ ] **Step 4: Run integration tests for this function**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_integration.py::test_get_stock_akshare_returns_markdown_with_ohlcv tests/dataflows/test_akshare_integration.py::test_get_stock_akshare_raises_for_non_a_share -v -m integration
  ```

  Expected: both pass.

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_market.py tests/dataflows/test_akshare_integration.py
  git commit -m "feat(dataflows): akshare get_stock OHLCV"
  ```

---

### Task 6: `akshare_market.get_indicator_akshare`

**Files:**
- Modify: `tradingagents/dataflows/akshare_market.py:get_indicator_akshare`
- Modify: `tests/dataflows/test_akshare_integration.py`

The existing `stockstats_utils` is pure Python and works on any DataFrame with OHLCV columns. We feed it the akshare hist DataFrame.

- [ ] **Step 1: Inspect the existing helper signature**

  ```bash
  .venv/bin/python -c "from tradingagents.dataflows import stockstats_utils as s; help(s.get_stock_stats_indicators_window)" 2>&1 | head -25
  ```

  Note its signature. Adapt the akshare wrapper to feed it consistent inputs (DataFrame with capitalised OHLCV columns).

- [ ] **Step 2: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_market import get_indicator_akshare


  def test_get_indicator_akshare_returns_indicator_values():
      out = get_indicator_akshare(TEST_TICKER_SH, "close_50_sma", "2026-05-08", 30)
      assert isinstance(out, str)
      assert "close_50_sma" in out or "50 SMA" in out
      assert "2026-" in out
  ```

- [ ] **Step 3: Run to confirm fail**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_integration.py::test_get_indicator_akshare_returns_indicator_values -v -m integration
  ```

- [ ] **Step 4: Implement** — replace the stub with:

  ```python
  from . import stockstats_utils
  from datetime import datetime, timedelta


  @ak_retry()
  def get_indicator_akshare(
      ticker: str, indicator: str, curr_date: str, look_back_days: int = 30,
  ) -> str:
      """Compute technical indicator over a recent window from akshare daily data."""
      symbol = to_ak_symbol(ticker)
      end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
      # Pull enough history for the indicator to stabilise (e.g. 50 SMA needs >=50)
      buffer_days = max(look_back_days, 200)
      start_dt = end_dt - timedelta(days=buffer_days + look_back_days)

      df = ak.stock_zh_a_hist(
          symbol=symbol, period="daily",
          start_date=start_dt.strftime("%Y%m%d"),
          end_date=end_dt.strftime("%Y%m%d"),
          adjust="qfq",
      )
      if df is None or df.empty:
          return f"## {ticker} {indicator}\n\n_No data available._"

      df = df.rename(columns=_STOCK_HIST_RENAME)
      # stockstats_utils expects lowercase column names typically
      df.columns = [c.lower() for c in df.columns]

      return stockstats_utils.get_indicator_from_df(
          df, indicator, curr_date, look_back_days
      ) if hasattr(stockstats_utils, "get_indicator_from_df") else _fallback_indicator(df, indicator, look_back_days)


  def _fallback_indicator(df: pd.DataFrame, indicator: str, look_back_days: int) -> str:
      """If stockstats_utils doesn't expose a DataFrame-based helper, compute inline."""
      from stockstats import wrap as _wrap
      sdf = _wrap(df)
      sdf[indicator]  # trigger computation
      result = sdf[["date", indicator]].tail(look_back_days)
      return format_df_as_md(result, f"{indicator} (last {look_back_days} days)", max_rows=look_back_days)
  ```

  **Inspection note (Step 1):** `stockstats_utils` may not have a `get_indicator_from_df` helper — `_fallback_indicator` covers that case using stockstats directly (already a transitive dep). Adjust the call once you've seen the real signature.

- [ ] **Step 5: Run integration test**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_integration.py::test_get_indicator_akshare_returns_indicator_values -v -m integration
  ```

- [ ] **Step 6: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_market.py tests/dataflows/test_akshare_integration.py
  git commit -m "feat(dataflows): akshare get_indicator (via stockstats)"
  ```

---

### Task 7: `akshare_market.get_insider_transactions_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_market import get_insider_transactions_akshare


  def test_get_insider_transactions_akshare_returns_markdown():
      out = get_insider_transactions_akshare(TEST_TICKER_SH, TEST_DATE)
      assert isinstance(out, str)
      assert "##" in out
      # Either there's recent insider activity (table with rows) or a "No data" note
      assert "600487" in out or "No data" in out
  ```

- [ ] **Step 2: Run, confirm fail**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_integration.py::test_get_insider_transactions_akshare_returns_markdown -v -m integration
  ```

- [ ] **Step 3: Implement** — replace the stub:

  ```python
  @ak_retry()
  def get_insider_transactions_akshare(ticker: str, curr_date: str) -> str:
      """Combined executive + 5%+ shareholder transactions for an A-share."""
      symbol = to_ak_symbol(ticker)
      market_symbol = to_ak_symbol_with_market(ticker)

      sections = []

      # 1) Executive transactions (高管增减持)
      try:
          execs = ak.stock_ggcg_em(symbol=symbol)
          sections.append(format_df_as_md(execs, "Executive (高管) Transactions", max_rows=30))
      except Exception as e:
          logger.warning("stock_ggcg_em failed for %s: %s", symbol, e)
          sections.append("## Executive (高管) Transactions\n\n_Source unavailable._")

      # 2) Major shareholder (5%+) transactions — endpoint depends on exchange
      try:
          if market_symbol.startswith("SH"):
              major = ak.stock_share_hold_change_sse(symbol=symbol)
          else:
              major = ak.stock_share_hold_change_szse(symbol=symbol)
          sections.append(format_df_as_md(major, "Major Shareholder (>=5%) Transactions", max_rows=30))
      except Exception as e:
          logger.warning("stock_share_hold_change failed for %s: %s", market_symbol, e)
          sections.append("## Major Shareholder Transactions\n\n_Source unavailable._")

      return f"# Insider transactions for {ticker} (as of {curr_date})\n\n" + "\n\n".join(sections)
  ```

- [ ] **Step 4: Run, confirm pass**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_integration.py::test_get_insider_transactions_akshare_returns_markdown -v -m integration
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_market.py tests/dataflows/test_akshare_integration.py
  git commit -m "feat(dataflows): akshare get_insider_transactions (exec + 5%+ shareholder)"
  ```

---

### Task 8: `akshare_news.get_news_akshare`

**Files:** `tradingagents/dataflows/akshare_news.py`, `tests/dataflows/test_akshare_integration.py`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_news import get_news_akshare


  def test_get_news_akshare_returns_articles():
      out = get_news_akshare(TEST_TICKER_SH, "2026-04-15", TEST_DATE)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_integration.py::test_get_news_akshare_returns_articles -v -m integration
  ```

- [ ] **Step 3: Replace stub in `akshare_news.py`**

  ```python
  """Akshare implementations for news / announcements."""

  import logging
  from datetime import datetime

  import akshare as ak
  import pandas as pd

  from .akshare_common import (
      ak_retry, format_df_as_md, is_a_share,
      to_ak_symbol, NotApplicableError,
  )

  logger = logging.getLogger(__name__)


  @ak_retry()
  def get_news_akshare(ticker: str, start_date: str, end_date: str) -> str:
      """Per-stock news for an A-share ticker, filtered to [start_date, end_date]."""
      symbol = to_ak_symbol(ticker)
      df = ak.stock_news_em(symbol=symbol)
      if df is None or df.empty:
          return f"## News for {ticker} {start_date} → {end_date}\n\n_No news found._"

      # Akshare returns publish times as strings like '2026-05-07 09:15:00'
      time_col = next((c for c in df.columns if "时间" in c or "publish" in c.lower()), None)
      if time_col:
          df["_dt"] = pd.to_datetime(df[time_col], errors="coerce")
          start = pd.to_datetime(start_date)
          end = pd.to_datetime(end_date) + pd.Timedelta(days=1)  # inclusive of end_date
          df = df[(df["_dt"] >= start) & (df["_dt"] < end)].drop(columns=["_dt"])

      if df.empty:
          return f"## News for {ticker} {start_date} → {end_date}\n\n_No news in window._"

      return format_df_as_md(df, f"News for {ticker} {start_date} → {end_date}", max_rows=20)


  def get_global_news_akshare(*_a, **_k):
      raise NotImplementedError("akshare_news.get_global_news_akshare — Task 9")


  def get_announcements_akshare(*_a, **_k):
      raise NotImplementedError("akshare_news.get_announcements_akshare — Task 10")
  ```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_news.py tests/dataflows/test_akshare_integration.py
  git commit -m "feat(dataflows): akshare get_news (per-stock, date-window filtered)"
  ```

---

### Task 9: `akshare_news.get_global_news_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_news import get_global_news_akshare


  def test_get_global_news_akshare_returns_articles():
      out = get_global_news_akshare(TEST_DATE, look_back_days=2, limit=10)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Replace stub**

  ```python
  @ak_retry()
  def get_global_news_akshare(curr_date: str, look_back_days: int = 7, limit: int = 30) -> str:
      """Macro/global financial news from akshare's east-money aggregator."""
      df = ak.stock_info_global_em()
      if df is None or df.empty:
          return f"## Global news as of {curr_date}\n\n_No data._"

      # Filter to recent window if a time column exists
      time_col = next((c for c in df.columns if "时间" in c), None)
      if time_col:
          df["_dt"] = pd.to_datetime(df[time_col], errors="coerce")
          end = pd.to_datetime(curr_date) + pd.Timedelta(days=1)
          start = end - pd.Timedelta(days=look_back_days + 1)
          df = df[(df["_dt"] >= start) & (df["_dt"] < end)].drop(columns=["_dt"])

      df = df.head(limit)
      return format_df_as_md(df, f"Global news as of {curr_date} (last {look_back_days}d)", max_rows=limit)
  ```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_news.py tests/dataflows/test_akshare_integration.py
  git commit -m "feat(dataflows): akshare get_global_news"
  ```

---

### Task 10: `akshare_news.get_announcements_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_news import get_announcements_akshare


  def test_get_announcements_akshare_returns_markdown():
      out = get_announcements_akshare(TEST_TICKER_SH, "2026-04-01", TEST_DATE)
      assert isinstance(out, str)
      assert "##" in out


  def test_get_announcements_akshare_raises_for_non_a_share():
      from tradingagents.dataflows.akshare_common import NotApplicableError
      with pytest.raises(NotApplicableError):
          get_announcements_akshare("NVDA", "2026-04-01", TEST_DATE)
  ```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Replace stub**

  ```python
  @ak_retry()
  def get_announcements_akshare(ticker: str, start_date: str, end_date: str) -> str:
      """Legal disclosure announcements (法定信披) from CNINFO/EastMoney."""
      symbol = to_ak_symbol(ticker)

      # stock_notice_report returns a daily snapshot; walk the date window
      from datetime import datetime, timedelta
      start = datetime.strptime(start_date, "%Y-%m-%d")
      end = datetime.strptime(end_date, "%Y-%m-%d")
      frames = []
      cursor = start
      while cursor <= end:
          try:
              daily = ak.stock_notice_report(symbol="all", date=cursor.strftime("%Y%m%d"))
              if daily is not None and not daily.empty:
                  # Filter rows that reference this ticker
                  code_col = next((c for c in daily.columns if "代码" in c), None)
                  if code_col:
                      hit = daily[daily[code_col].astype(str).str.zfill(6) == symbol]
                      if not hit.empty:
                          frames.append(hit)
          except Exception as e:
              logger.warning("stock_notice_report on %s failed: %s", cursor, e)
          cursor += timedelta(days=1)

      if not frames:
          return f"## Announcements for {ticker} {start_date} → {end_date}\n\n_No filings._"

      combined = pd.concat(frames, ignore_index=True)
      return format_df_as_md(combined, f"Announcements for {ticker} {start_date} → {end_date}", max_rows=40)
  ```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_news.py tests/dataflows/test_akshare_integration.py
  git commit -m "feat(dataflows): akshare get_announcements (legal-disclosure filings)"
  ```

---

### Task 11: `akshare_sentiment.get_stock_hot_rank_akshare`

**Files:** `tradingagents/dataflows/akshare_sentiment.py`, `tests/dataflows/test_akshare_integration.py`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_sentiment import get_stock_hot_rank_akshare


  def test_get_stock_hot_rank_akshare_returns_rank_info():
      out = get_stock_hot_rank_akshare(TEST_TICKER_SH, TEST_DATE)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Replace stub in `akshare_sentiment.py`**

  ```python
  """Akshare implementations for sentiment proxies (A-share)."""

  import logging
  import akshare as ak
  import pandas as pd

  from .akshare_common import ak_retry, format_df_as_md, to_ak_symbol

  logger = logging.getLogger(__name__)


  @ak_retry()
  def get_stock_hot_rank_akshare(ticker: str, curr_date: str) -> str:
      """Combined east-money + 同花顺 attention rank for an A-share."""
      symbol = to_ak_symbol(ticker)
      sections = []

      try:
          em = ak.stock_hot_rank_em()       # full board snapshot
          if em is not None and not em.empty:
              code_col = next((c for c in em.columns if "代码" in c), None)
              if code_col:
                  em = em[em[code_col].astype(str).str.zfill(6) == symbol]
          sections.append(format_df_as_md(em, "East-Money Hot Rank", max_rows=10))
      except Exception as e:
          logger.warning("stock_hot_rank_em failed: %s", e)
          sections.append("## East-Money Hot Rank\n\n_Source unavailable._")

      try:
          wc = ak.stock_hot_rank_wc()       # 同花顺
          if wc is not None and not wc.empty:
              code_col = next((c for c in wc.columns if "代码" in c), None)
              if code_col:
                  wc = wc[wc[code_col].astype(str).str.zfill(6) == symbol]
          sections.append(format_df_as_md(wc, "Tonghuashun Hot Rank", max_rows=10))
      except Exception as e:
          logger.warning("stock_hot_rank_wc failed: %s", e)
          sections.append("## Tonghuashun Hot Rank\n\n_Source unavailable._")

      return f"# Attention rank for {ticker} (as of {curr_date})\n\n" + "\n\n".join(sections)


  def get_shareholder_count_akshare(*_a, **_k):
      raise NotImplementedError("akshare_sentiment.get_shareholder_count_akshare — Task 12")


  def get_research_reports_akshare(*_a, **_k):
      raise NotImplementedError("akshare_sentiment.get_research_reports_akshare — Task 13")
  ```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_sentiment.py tests/dataflows/test_akshare_integration.py
  git commit -m "feat(dataflows): akshare get_stock_hot_rank (attention proxy)"
  ```

---

### Task 12: `akshare_sentiment.get_shareholder_count_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_sentiment import get_shareholder_count_akshare


  def test_get_shareholder_count_akshare_returns_history():
      out = get_shareholder_count_akshare(TEST_TICKER_SH, TEST_DATE)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Replace stub**

  ```python
  @ak_retry()
  def get_shareholder_count_akshare(ticker: str, curr_date: str) -> str:
      """Quarterly shareholder count history — chip-concentration proxy."""
      symbol = to_ak_symbol(ticker)
      try:
          df = ak.stock_zh_a_gdhs(symbol=symbol)
      except Exception as e:
          logger.warning("stock_zh_a_gdhs failed for %s: %s", symbol, e)
          return f"## Shareholder count for {ticker}\n\n_Source unavailable: {e}_"
      return format_df_as_md(df, f"Shareholder count history for {ticker} (as of {curr_date})", max_rows=20)
  ```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_sentiment.py tests/dataflows/test_akshare_integration.py
  git commit -m "feat(dataflows): akshare get_shareholder_count (chip concentration)"
  ```

---

### Task 13: `akshare_sentiment.get_research_reports_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_sentiment import get_research_reports_akshare


  def test_get_research_reports_akshare_returns_recent():
      out = get_research_reports_akshare(TEST_TICKER_SH, "2026-01-01", TEST_DATE)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Replace stub**

  ```python
  @ak_retry()
  def get_research_reports_akshare(ticker: str, start_date: str, end_date: str) -> str:
      """Analyst research reports (target prices, ratings) filtered to a date window."""
      symbol = to_ak_symbol(ticker)
      try:
          df = ak.stock_research_report_em(symbol=symbol)
      except Exception as e:
          logger.warning("stock_research_report_em failed for %s: %s", symbol, e)
          return f"## Research reports for {ticker}\n\n_Source unavailable: {e}_"

      if df is None or df.empty:
          return f"## Research reports for {ticker} {start_date} → {end_date}\n\n_No reports._"

      date_col = next((c for c in df.columns if "日期" in c or "date" in c.lower()), None)
      if date_col:
          df["_dt"] = pd.to_datetime(df[date_col], errors="coerce")
          start = pd.to_datetime(start_date)
          end = pd.to_datetime(end_date) + pd.Timedelta(days=1)
          df = df[(df["_dt"] >= start) & (df["_dt"] < end)].drop(columns=["_dt"])

      return format_df_as_md(df, f"Research reports for {ticker} {start_date} → {end_date}", max_rows=20)
  ```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_sentiment.py tests/dataflows/test_akshare_integration.py
  git commit -m "feat(dataflows): akshare get_research_reports (analyst consensus)"
  ```

---

### Tasks 14-17: `akshare_fundamentals` — 4 financial statements

These four functions all follow the same pattern: call `ak.stock_<sheet>_by_report_em(symbol="SH600487")`, slice to 5 most-recent annuals + 4 most-recent quarters, format. Sharing a private helper.

**Files:** `tradingagents/dataflows/akshare_fundamentals.py`, `tests/dataflows/test_akshare_integration.py`

#### Task 14: `get_fundamentals_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_fundamentals import get_fundamentals_akshare


  def test_get_fundamentals_akshare_returns_summary():
      out = get_fundamentals_akshare(TEST_TICKER_SH, TEST_DATE)
      assert isinstance(out, str)
      assert "##" in out
      assert "600487" in out or "亨通" in out
  ```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Replace stub in `akshare_fundamentals.py`** — full file:

  ```python
  """Akshare implementations for fundamentals: summary + 3 financial statements."""

  import logging
  import pandas as pd
  import akshare as ak

  from .akshare_common import (
      ak_retry, format_df_as_md, to_ak_symbol, to_ak_symbol_with_market,
  )

  logger = logging.getLogger(__name__)


  def _select_periods(df: pd.DataFrame, period_col: str) -> pd.DataFrame:
      """Keep 5 most-recent annual reports + 4 most-recent quarterly reports.

      Annual reports end with '1231'; quarterly with '0331', '0630', '0930'.
      """
      if df is None or df.empty or period_col not in df.columns:
          return df
      periods = pd.to_datetime(df[period_col], errors="coerce")
      df = df.assign(_period=periods).sort_values("_period", ascending=False)
      annual = df[df["_period"].dt.strftime("%m%d") == "1231"].head(5)
      quarterly = df[df["_period"].dt.strftime("%m%d") != "1231"].head(4)
      result = pd.concat([annual, quarterly]).sort_values("_period", ascending=False).drop(columns=["_period"])
      return result


  @ak_retry()
  def get_fundamentals_akshare(ticker: str, curr_date: str) -> str:
      """High-level fundamentals snapshot from 同花顺."""
      symbol = to_ak_symbol(ticker)
      try:
          df = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按报告期")
      except Exception as e:
          logger.warning("stock_financial_abstract_ths failed for %s: %s", symbol, e)
          return f"## Fundamentals for {ticker}\n\n_Source unavailable: {e}_"

      # Pick the period column (column name varies by version)
      period_col = next((c for c in df.columns if "报告" in c or "period" in c.lower()), None)
      if period_col:
          df = _select_periods(df, period_col)
      return format_df_as_md(df, f"Fundamentals summary for {ticker} (as of {curr_date})", max_rows=20)


  def get_balance_sheet_akshare(*_a, **_k):
      raise NotImplementedError("akshare_fundamentals.get_balance_sheet_akshare — Task 15")


  def get_cashflow_akshare(*_a, **_k):
      raise NotImplementedError("akshare_fundamentals.get_cashflow_akshare — Task 16")


  def get_income_statement_akshare(*_a, **_k):
      raise NotImplementedError("akshare_fundamentals.get_income_statement_akshare — Task 17")
  ```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/dataflows/akshare_fundamentals.py tests/dataflows/test_akshare_integration.py
  git commit -m "feat(dataflows): akshare get_fundamentals (THS summary)"
  ```

#### Task 15: `get_balance_sheet_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_fundamentals import get_balance_sheet_akshare


  def test_get_balance_sheet_akshare_returns_table():
      out = get_balance_sheet_akshare(TEST_TICKER_SH, TEST_DATE)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Replace stub**

  ```python
  @ak_retry()
  def get_balance_sheet_akshare(ticker: str, curr_date: str) -> str:
      market_symbol = to_ak_symbol_with_market(ticker)
      try:
          df = ak.stock_balance_sheet_by_report_em(symbol=market_symbol)
      except Exception as e:
          logger.warning("stock_balance_sheet_by_report_em failed for %s: %s", market_symbol, e)
          return f"## Balance sheet for {ticker}\n\n_Source unavailable: {e}_"
      period_col = next((c for c in df.columns if "报告" in c or "REPORT" in c.upper()), None)
      if period_col:
          df = _select_periods(df, period_col)
      return format_df_as_md(df, f"Balance sheet for {ticker} (as of {curr_date})", max_rows=20)
  ```

- [ ] **Step 4: Run, pass**

- [ ] **Step 5: Commit**

  ```bash
  git commit -am "feat(dataflows): akshare get_balance_sheet"
  ```

#### Task 16: `get_cashflow_akshare`

- [ ] **Step 1: Append failing test** (same shape as Task 15 with `get_cashflow_akshare`)
- [ ] **Step 2: Run, fail**
- [ ] **Step 3: Replace stub** — identical body to Task 15, swapping `stock_balance_sheet_by_report_em` for `stock_cash_flow_sheet_by_report_em`:

  ```python
  @ak_retry()
  def get_cashflow_akshare(ticker: str, curr_date: str) -> str:
      market_symbol = to_ak_symbol_with_market(ticker)
      try:
          df = ak.stock_cash_flow_sheet_by_report_em(symbol=market_symbol)
      except Exception as e:
          logger.warning("stock_cash_flow_sheet_by_report_em failed for %s: %s", market_symbol, e)
          return f"## Cash flow for {ticker}\n\n_Source unavailable: {e}_"
      period_col = next((c for c in df.columns if "报告" in c or "REPORT" in c.upper()), None)
      if period_col:
          df = _select_periods(df, period_col)
      return format_df_as_md(df, f"Cash flow for {ticker} (as of {curr_date})", max_rows=20)
  ```

- [ ] **Step 4: Run, pass**
- [ ] **Step 5: Commit:** `git commit -am "feat(dataflows): akshare get_cashflow"`

#### Task 17: `get_income_statement_akshare`

- [ ] **Step 1: Append failing test** (same shape, `get_income_statement_akshare`)
- [ ] **Step 2: Run, fail**
- [ ] **Step 3: Replace stub**:

  ```python
  @ak_retry()
  def get_income_statement_akshare(ticker: str, curr_date: str) -> str:
      market_symbol = to_ak_symbol_with_market(ticker)
      try:
          df = ak.stock_profit_sheet_by_report_em(symbol=market_symbol)
      except Exception as e:
          logger.warning("stock_profit_sheet_by_report_em failed for %s: %s", market_symbol, e)
          return f"## Income statement for {ticker}\n\n_Source unavailable: {e}_"
      period_col = next((c for c in df.columns if "报告" in c or "REPORT" in c.upper()), None)
      if period_col:
          df = _select_periods(df, period_col)
      return format_df_as_md(df, f"Income statement for {ticker} (as of {curr_date})", max_rows=20)
  ```

- [ ] **Step 4: Run, pass**
- [ ] **Step 5: Commit:** `git commit -am "feat(dataflows): akshare get_income_statement"`

---

### Tasks 18-23: `akshare_capital_flow` — 6 capital-flow methods

**Files:** `tradingagents/dataflows/akshare_capital_flow.py`, `tests/dataflows/test_akshare_integration.py`

#### Task 18: `get_lhb_detail_akshare` (龙虎榜 individual)

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_capital_flow import get_lhb_detail_akshare


  def test_get_lhb_detail_akshare_returns_markdown():
      out = get_lhb_detail_akshare(TEST_TICKER_SH, TEST_DATE, look_back_days=10)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**

- [ ] **Step 3: Replace stub in `akshare_capital_flow.py`** — full file:

  ```python
  """Akshare implementations for A-share capital-flow signals."""

  import logging
  from datetime import datetime, timedelta
  import akshare as ak
  import pandas as pd

  from .akshare_common import (
      ak_retry, format_df_as_md, to_ak_symbol, to_ak_symbol_with_market,
  )

  logger = logging.getLogger(__name__)


  def _date_range(curr_date: str, look_back_days: int):
      end = datetime.strptime(curr_date, "%Y-%m-%d")
      start = end - timedelta(days=look_back_days)
      return start, end


  @ak_retry()
  def get_lhb_detail_akshare(ticker: str, curr_date: str, look_back_days: int = 5) -> str:
      """Dragon-Tiger seat detail for a single ticker over a recent window."""
      symbol = to_ak_symbol(ticker)
      start, end = _date_range(curr_date, look_back_days)
      frames = []
      cursor = start
      while cursor <= end:
          try:
              daily = ak.stock_lhb_stock_detail_em(symbol=symbol, date=cursor.strftime("%Y%m%d"))
              if daily is not None and not daily.empty:
                  daily = daily.assign(_dt=cursor.strftime("%Y-%m-%d"))
                  frames.append(daily)
          except Exception as e:
              logger.debug("no LHB data for %s on %s: %s", symbol, cursor, e)
          cursor += timedelta(days=1)

      if not frames:
          return f"## Dragon-Tiger seats for {ticker} (last {look_back_days}d)\n\n_No 龙虎榜 hits._"
      combined = pd.concat(frames, ignore_index=True)
      return format_df_as_md(combined, f"Dragon-Tiger seats for {ticker} (last {look_back_days}d)", max_rows=30)


  def get_lhb_institutional_akshare(*_a, **_k):
      raise NotImplementedError("Task 19")


  def get_north_capital_individual_akshare(*_a, **_k):
      raise NotImplementedError("Task 20")


  def get_north_capital_overall_akshare(*_a, **_k):
      raise NotImplementedError("Task 21")


  def get_margin_trading_akshare(*_a, **_k):
      raise NotImplementedError("Task 22")


  def get_fund_flow_akshare(*_a, **_k):
      raise NotImplementedError("Task 23")
  ```

- [ ] **Step 4: Run, pass**
- [ ] **Step 5: Commit:** `git commit -am "feat(dataflows): akshare get_lhb_detail"`

#### Task 19: `get_lhb_institutional_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_capital_flow import get_lhb_institutional_akshare


  def test_get_lhb_institutional_akshare_returns_markdown():
      out = get_lhb_institutional_akshare(TEST_TICKER_SH, TEST_DATE, look_back_days=10)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**
- [ ] **Step 3: Replace stub**

  ```python
  @ak_retry()
  def get_lhb_institutional_akshare(ticker: str, curr_date: str, look_back_days: int = 10) -> str:
      """Institutional-seat-only Dragon-Tiger flow for a ticker over a recent window."""
      symbol = to_ak_symbol(ticker)
      start, end = _date_range(curr_date, look_back_days)
      try:
          df = ak.stock_lhb_jgmmtj_em(start_date=start.strftime("%Y%m%d"),
                                       end_date=end.strftime("%Y%m%d"))
      except Exception as e:
          logger.warning("stock_lhb_jgmmtj_em failed: %s", e)
          return f"## Institutional LHB for {ticker}\n\n_Source unavailable: {e}_"
      if df is not None and not df.empty:
          code_col = next((c for c in df.columns if "代码" in c), None)
          if code_col:
              df = df[df[code_col].astype(str).str.zfill(6) == symbol]
      return format_df_as_md(df, f"Institutional LHB for {ticker} (last {look_back_days}d)", max_rows=20)
  ```

- [ ] **Step 4: Run, pass**
- [ ] **Step 5: Commit:** `git commit -am "feat(dataflows): akshare get_lhb_institutional"`

#### Task 20: `get_north_capital_individual_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_capital_flow import get_north_capital_individual_akshare


  def test_get_north_capital_individual_akshare_returns_markdown():
      out = get_north_capital_individual_akshare(TEST_TICKER_SH, TEST_DATE, look_back_days=10)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**
- [ ] **Step 3: Replace stub**

  ```python
  @ak_retry()
  def get_north_capital_individual_akshare(ticker: str, curr_date: str, look_back_days: int = 10) -> str:
      """Northbound (Stock Connect) holding changes for a ticker."""
      market_symbol = to_ak_symbol_with_market(ticker)
      try:
          df = ak.stock_hsgt_individual_em(stock=market_symbol)
      except Exception as e:
          logger.warning("stock_hsgt_individual_em failed for %s: %s", market_symbol, e)
          return f"## Northbound holdings for {ticker}\n\n_Source unavailable: {e}_"
      if df is not None and not df.empty:
          date_col = next((c for c in df.columns if "日期" in c), None)
          if date_col:
              df["_dt"] = pd.to_datetime(df[date_col], errors="coerce")
              start, end = _date_range(curr_date, look_back_days)
              df = df[(df["_dt"] >= pd.Timestamp(start)) & (df["_dt"] <= pd.Timestamp(end))].drop(columns=["_dt"])
      return format_df_as_md(df, f"Northbound holdings for {ticker} (last {look_back_days}d)", max_rows=20)
  ```

- [ ] **Step 4: Run, pass**
- [ ] **Step 5: Commit:** `git commit -am "feat(dataflows): akshare get_north_capital_individual"`

#### Task 21: `get_north_capital_overall_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_capital_flow import get_north_capital_overall_akshare


  def test_get_north_capital_overall_akshare_returns_markdown():
      out = get_north_capital_overall_akshare(TEST_DATE, look_back_days=10)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**
- [ ] **Step 3: Replace stub** (this method does not take a ticker — it's a market-wide signal):

  ```python
  @ak_retry()
  def get_north_capital_overall_akshare(curr_date: str, look_back_days: int = 10) -> str:
      """Daily net inflow of northbound capital — market-wide mood proxy."""
      try:
          df = ak.stock_hsgt_north_net_flow_in_em(symbol="北上")
      except Exception as e:
          logger.warning("stock_hsgt_north_net_flow_in_em failed: %s", e)
          return f"## Northbound overall flow\n\n_Source unavailable: {e}_"
      if df is not None and not df.empty:
          date_col = next((c for c in df.columns if "日期" in c), None)
          if date_col:
              df["_dt"] = pd.to_datetime(df[date_col], errors="coerce")
              start, end = _date_range(curr_date, look_back_days)
              df = df[(df["_dt"] >= pd.Timestamp(start)) & (df["_dt"] <= pd.Timestamp(end))].drop(columns=["_dt"])
      return format_df_as_md(df, f"Northbound net flow (last {look_back_days}d)", max_rows=look_back_days)
  ```

- [ ] **Step 4: Run, pass**
- [ ] **Step 5: Commit:** `git commit -am "feat(dataflows): akshare get_north_capital_overall"`

  > **Note:** This function takes `(curr_date, look_back_days)` — no ticker arg. The `route_to_vendor` dispatch passes `curr_date` as the first positional arg, which `_detect_market` will classify as "global". That's fine because this method has only one vendor (akshare) — `tool_vendors` override can force it, otherwise it always picks akshare.

#### Task 22: `get_margin_trading_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_capital_flow import get_margin_trading_akshare


  def test_get_margin_trading_akshare_returns_markdown():
      out = get_margin_trading_akshare(TEST_TICKER_SH, TEST_DATE, look_back_days=10)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**
- [ ] **Step 3: Replace stub**

  ```python
  @ak_retry()
  def get_margin_trading_akshare(ticker: str, curr_date: str, look_back_days: int = 10) -> str:
      """Per-ticker margin (融资) + securities-lending (融券) balances over a window."""
      symbol = to_ak_symbol(ticker)
      market_symbol = to_ak_symbol_with_market(ticker)
      start, end = _date_range(curr_date, look_back_days)
      try:
          if market_symbol.startswith("SH"):
              df = ak.stock_margin_detail_sse(symbol=symbol,
                                              start_date=start.strftime("%Y%m%d"),
                                              end_date=end.strftime("%Y%m%d"))
          else:
              df = ak.stock_margin_detail_szse(symbol=symbol,
                                                date=end.strftime("%Y%m%d"))
              # _szse takes a single date and returns the daily snapshot;
              # walk the window manually for SZ tickers
              frames = [df] if df is not None and not df.empty else []
              cursor = start
              while cursor < end:
                  try:
                      daily = ak.stock_margin_detail_szse(symbol=symbol, date=cursor.strftime("%Y%m%d"))
                      if daily is not None and not daily.empty:
                          frames.append(daily)
                  except Exception:
                      pass
                  cursor += timedelta(days=1)
              df = pd.concat(frames, ignore_index=True) if frames else df
      except Exception as e:
          logger.warning("margin endpoint failed for %s: %s", ticker, e)
          return f"## Margin trading for {ticker}\n\n_Source unavailable: {e}_"
      return format_df_as_md(df, f"Margin trading for {ticker} (last {look_back_days}d)", max_rows=look_back_days)
  ```

- [ ] **Step 4: Run, pass**
- [ ] **Step 5: Commit:** `git commit -am "feat(dataflows): akshare get_margin_trading"`

#### Task 23: `get_fund_flow_akshare`

- [ ] **Step 1: Append failing test**

  ```python
  from tradingagents.dataflows.akshare_capital_flow import get_fund_flow_akshare


  def test_get_fund_flow_akshare_returns_markdown():
      out = get_fund_flow_akshare(TEST_TICKER_SH, TEST_DATE)
      assert isinstance(out, str)
      assert "##" in out
  ```

- [ ] **Step 2: Run, fail**
- [ ] **Step 3: Replace stub**

  ```python
  @ak_retry()
  def get_fund_flow_akshare(ticker: str, curr_date: str) -> str:
      """Today's smart-money flow breakdown (super-large / large / medium / small)."""
      symbol = to_ak_symbol(ticker)
      market_symbol = to_ak_symbol_with_market(ticker)
      market = "sh" if market_symbol.startswith("SH") else "sz"
      try:
          df = ak.stock_individual_fund_flow(stock=symbol, market=market)
      except Exception as e:
          logger.warning("stock_individual_fund_flow failed for %s: %s", symbol, e)
          return f"## Smart-money flow for {ticker}\n\n_Source unavailable: {e}_"
      # Keep just the rows around curr_date (typically last ~30 days)
      return format_df_as_md(df, f"Smart-money flow for {ticker} (as of {curr_date})", max_rows=10)
  ```

- [ ] **Step 4: Run, pass**
- [ ] **Step 5: Commit:** `git commit -am "feat(dataflows): akshare get_fund_flow"`

---

### Task 24: Full Phase B integration sweep

- [ ] **Step 1: Run the entire integration suite**

  ```bash
  .venv/bin/python -m pytest tests/dataflows/test_akshare_integration.py -v -m integration 2>&1 | tail -40
  ```

  Expected: all green. If a function returns "Source unavailable" because akshare's endpoint changed naming, fix that function (akshare versions evolve; the implementation's `try/except` already prevents hard failures, but a successful test should hit real data).

- [ ] **Step 2: Run the non-integration suite to confirm no regression**

  ```bash
  .venv/bin/python -m pytest tests/ -v -m "not integration" 2>&1 | tail -20
  ```

- [ ] **Step 3: If any test failed, fix and recommit** (no commit if everything was already green)

---

## Phase C — Abstract tool wrappers (@tool functions)

### Task 25: Extend `news_data_tools.py` with 4 new tools

**Files:**
- Modify: `tradingagents/agents/utils/news_data_tools.py`

These are thin `@tool`-decorated wrappers around `route_to_vendor`, matching the pattern of the existing `get_news` / `get_global_news` / `get_insider_transactions`.

- [ ] **Step 1: Append to `tradingagents/agents/utils/news_data_tools.py`**

  ```python
  @tool
  def get_announcements(
      ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
      start_date: Annotated[str, "Start date yyyy-mm-dd"],
      end_date: Annotated[str, "End date yyyy-mm-dd"],
  ) -> str:
      """A-share legal-disclosure announcements (法定信披) for a ticker.

      Returns a formatted markdown list. Only supports A-share tickers
      (`.SS` / `.SZ`). Calling with a US ticker returns an N/A string.
      """
      return route_to_vendor("get_announcements", ticker, start_date, end_date)


  @tool
  def get_stock_hot_rank(
      ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
      curr_date: Annotated[str, "Current date yyyy-mm-dd"],
  ) -> str:
      """Attention rank for an A-share ticker (East-Money + Tonghuashun).

      Use this as a market-attention proxy for sentiment analysis on A-shares.
      Non-A-share tickers return an N/A string.
      """
      return route_to_vendor("get_stock_hot_rank", ticker, curr_date)


  @tool
  def get_shareholder_count(
      ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
      curr_date: Annotated[str, "Current date yyyy-mm-dd"],
  ) -> str:
      """Historical shareholder count for an A-share — chip-concentration proxy.

      A falling count typically indicates institutional accumulation;
      a rising count indicates retail dispersion. Non-A-share returns N/A.
      """
      return route_to_vendor("get_shareholder_count", ticker, curr_date)


  @tool
  def get_research_reports(
      ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
      start_date: Annotated[str, "Start date yyyy-mm-dd"],
      end_date: Annotated[str, "End date yyyy-mm-dd"],
  ) -> str:
      """Analyst research reports (target prices, ratings) for an A-share.

      Use as auxiliary sentiment signal. Non-A-share returns N/A.
      """
      return route_to_vendor("get_research_reports", ticker, start_date, end_date)
  ```

- [ ] **Step 2: Re-export from `agent_utils.py`** — edit `tradingagents/agents/utils/agent_utils.py:16-20`:

  ```python
  from tradingagents.agents.utils.news_data_tools import (
      get_news,
      get_insider_transactions,
      get_global_news,
      get_announcements,
      get_stock_hot_rank,
      get_shareholder_count,
      get_research_reports,
  )
  ```

- [ ] **Step 3: Smoke import**

  ```bash
  .venv/bin/python -c "from tradingagents.agents.utils.agent_utils import get_announcements, get_stock_hot_rank, get_shareholder_count, get_research_reports; print('imported')"
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add tradingagents/agents/utils/news_data_tools.py tradingagents/agents/utils/agent_utils.py
  git commit -m "feat(tools): get_announcements + 3 A-share sentiment proxy tools"
  ```

---

### Task 26: Create `capital_flow_tools.py` with 6 capital-flow tools

**Files:**
- Create: `tradingagents/agents/utils/capital_flow_tools.py`
- Modify: `tradingagents/agents/utils/agent_utils.py` (add re-export block)

- [ ] **Step 1: Create `tradingagents/agents/utils/capital_flow_tools.py`**

  ```python
  """Tool wrappers for the Capital Flow Analyst (A-share-specific)."""

  from langchain_core.tools import tool
  from typing import Annotated

  from tradingagents.dataflows.interface import route_to_vendor


  @tool
  def get_lhb_detail(
      ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
      curr_date: Annotated[str, "Current date yyyy-mm-dd"],
      look_back_days: Annotated[int, "Days to look back"] = 5,
  ) -> str:
      """Dragon-Tiger List (龙虎榜) seat-level buy/sell detail for an A-share."""
      return route_to_vendor("get_lhb_detail", ticker, curr_date, look_back_days)


  @tool
  def get_lhb_institutional(
      ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
      curr_date: Annotated[str, "Current date yyyy-mm-dd"],
      look_back_days: Annotated[int, "Days to look back"] = 10,
  ) -> str:
      """Dragon-Tiger institutional-seat net flow over a recent window."""
      return route_to_vendor("get_lhb_institutional", ticker, curr_date, look_back_days)


  @tool
  def get_north_capital_individual(
      ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
      curr_date: Annotated[str, "Current date yyyy-mm-dd"],
      look_back_days: Annotated[int, "Days to look back"] = 10,
  ) -> str:
      """Northbound (Stock Connect / 北上资金) holding changes for one A-share."""
      return route_to_vendor("get_north_capital_individual", ticker, curr_date, look_back_days)


  @tool
  def get_north_capital_overall(
      curr_date: Annotated[str, "Current date yyyy-mm-dd"],
      look_back_days: Annotated[int, "Days to look back"] = 10,
  ) -> str:
      """Daily net inflow of northbound capital — market-wide mood proxy."""
      return route_to_vendor("get_north_capital_overall", curr_date, look_back_days)


  @tool
  def get_margin_trading(
      ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
      curr_date: Annotated[str, "Current date yyyy-mm-dd"],
      look_back_days: Annotated[int, "Days to look back"] = 10,
  ) -> str:
      """Per-ticker margin balance (融资) and securities lending (融券) over a window."""
      return route_to_vendor("get_margin_trading", ticker, curr_date, look_back_days)


  @tool
  def get_fund_flow(
      ticker: Annotated[str, "Ticker symbol (e.g. 600487.SS)"],
      curr_date: Annotated[str, "Current date yyyy-mm-dd"],
  ) -> str:
      """Smart-money flow breakdown (super-large/large/medium/small orders)."""
      return route_to_vendor("get_fund_flow", ticker, curr_date)
  ```

- [ ] **Step 2: Re-export in `agent_utils.py`** — append after the existing imports:

  ```python
  from tradingagents.agents.utils.capital_flow_tools import (
      get_lhb_detail,
      get_lhb_institutional,
      get_north_capital_individual,
      get_north_capital_overall,
      get_margin_trading,
      get_fund_flow,
  )
  ```

- [ ] **Step 3: Smoke import**

  ```bash
  .venv/bin/python -c "from tradingagents.agents.utils.agent_utils import get_lhb_detail, get_lhb_institutional, get_north_capital_individual, get_north_capital_overall, get_margin_trading, get_fund_flow; print('imported')"
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add tradingagents/agents/utils/capital_flow_tools.py tradingagents/agents/utils/agent_utils.py
  git commit -m "feat(tools): 6 capital-flow tools for A-share analysis"
  ```

---

## Phase D — Capital Flow Analyst + graph wiring

### Task 27: Add `capital_flow_report` to AgentState

**Files:**
- Modify: `tradingagents/agents/utils/agent_states.py`

- [ ] **Step 1: Append to the `AgentState` class** — insert between `fundamentals_report` and `investment_debate_state`:

  ```python
      # A-share specific: capital flow analysis (empty for non-A-share tickers)
      capital_flow_report: Annotated[
          str, "Report from the Capital Flow Analyst (A-share-only; empty otherwise)"
      ]
  ```

- [ ] **Step 2: Smoke check**

  ```bash
  .venv/bin/python -c "from tradingagents.agents.utils.agent_states import AgentState; print(AgentState.__annotations__.get('capital_flow_report'))"
  ```

  Expected: not None.

- [ ] **Step 3: Commit**

  ```bash
  git add tradingagents/agents/utils/agent_states.py
  git commit -m "feat(graph): add capital_flow_report field to AgentState"
  ```

---

### Task 28: Initialise `capital_flow_report=""` in propagation

**Files:**
- Modify: `tradingagents/graph/propagation.py`

- [ ] **Step 1: Find the field defaults** — read the file to locate `create_initial_state`:

  ```bash
  grep -n "fundamentals_report\|market_report" /Users/jiezihao/Desktop/TradingAgents/tradingagents/graph/propagation.py
  ```

- [ ] **Step 2: Add the field default** — alongside other `_report: ""` entries in `create_initial_state`, add:

  ```python
          "capital_flow_report": "",
  ```

  Maintain the existing comma/format style of the surrounding lines.

- [ ] **Step 3: Smoke import**

  ```bash
  .venv/bin/python -c "from tradingagents.graph.propagation import Propagator; p = Propagator(); s = p.create_initial_state('TEST', '2026-05-08', past_context=''); print(repr(s.get('capital_flow_report')))"
  ```

  Expected: `''`.

- [ ] **Step 4: Commit**

  ```bash
  git add tradingagents/graph/propagation.py
  git commit -m "feat(graph): default capital_flow_report to empty string in initial state"
  ```

---

### Task 29: Create `capital_flow_analyst.py`

**Files:**
- Create: `tradingagents/agents/analysts/capital_flow_analyst.py`
- Modify: `tradingagents/agents/__init__.py` (re-export)

- [ ] **Step 1: Inspect an existing analyst for the exact ReAct pattern**

  ```bash
  cat /Users/jiezihao/Desktop/TradingAgents/tradingagents/agents/analysts/fundamentals_analyst.py
  ```

  Note: the analyst is a closure factory returning a node function; the node binds tools, builds the prompt, invokes the chain, then returns a dict with the report and messages.

- [ ] **Step 2: Create `tradingagents/agents/analysts/capital_flow_analyst.py`**

  ```python
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
              get_lhb_detail,
              get_lhb_institutional,
              get_north_capital_individual,
              get_north_capital_overall,
              get_margin_trading,
              get_fund_flow,
          ]

          system_message = (
              "You are the Capital Flow Analyst for A-share equity {ticker} on {current_date}.\n\n"
              "Read short-term capital signals that are UNIQUE to the Chinese A-share market:\n"
              "1. Dragon-Tiger List (龙虎榜): which institutional / hot-money seats bought or sold;\n"
              "   institutional net flow trend over recent days.\n"
              "2. Northbound Capital (北上资金): foreign Stock-Connect holding changes for this\n"
              "   ticker; overall market net flow as a market-mood proxy.\n"
              "3. Margin Trading (融资融券): financing balance = retail leverage sentiment;\n"
              "   securities-lending balance = short-interest proxy.\n"
              "4. Smart-Money Flow (主力资金流向): today's super-large / large / medium / small\n"
              "   order net flows — who is accumulating vs distributing.\n\n"
              "Produce a structured report with:\n"
              "- One-line capital posture (accumulating / distributing / neutral)\n"
              "- Per-signal section with concrete numbers and 1-week trend\n"
              "- A capital-flow confidence rating: Strong Bullish / Bullish / Neutral / Bearish / Strong Bearish\n"
              "- Key risks visible in the data (e.g. retail leverage at multi-year high → squeeze risk)\n\n"
              "This is short-term flow analysis (1-10 day horizon). DO NOT make long-term valuation\n"
              "calls — that is the fundamentals analyst's job."
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
  ```

- [ ] **Step 3: Re-export from `tradingagents/agents/__init__.py`** — append:

  ```python
  from .analysts.capital_flow_analyst import create_capital_flow_analyst
  ```

  Confirm the file already has similar lines for the other analysts; if it uses a wildcard import pattern, the function must be exposed at the analysts subpackage level too. Adjust accordingly.

- [ ] **Step 4: Smoke import**

  ```bash
  .venv/bin/python -c "from tradingagents.agents import create_capital_flow_analyst; print(create_capital_flow_analyst)"
  ```

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/agents/analysts/capital_flow_analyst.py tradingagents/agents/__init__.py
  git commit -m "feat(analyst): capital_flow_analyst (A-share short-horizon flow signals)"
  ```

---

### Task 30: Add `should_continue_capital_flow` to ConditionalLogic

**Files:**
- Modify: `tradingagents/graph/conditional_logic.py`

- [ ] **Step 1: Inspect the file**

  ```bash
  cat /Users/jiezihao/Desktop/TradingAgents/tradingagents/graph/conditional_logic.py
  ```

- [ ] **Step 2: Add a parallel method** — alongside `should_continue_market`, `_social`, `_news`, `_fundamentals`, add:

  ```python
      def should_continue_capital_flow(self, state):
          messages = state["messages"]
          last_message = messages[-1]
          if last_message.tool_calls:
              return "tools_capital_flow"
          return "Msg Clear Capital_flow"
  ```

  Match the casing convention used by the existing methods exactly (look at one of the existing branches before writing this; the node name format from setup.py uses `analyst_type.capitalize()` which produces "Capital_flow" — verify against the loop in setup.py).

- [ ] **Step 3: Smoke check**

  ```bash
  .venv/bin/python -c "from tradingagents.graph.conditional_logic import ConditionalLogic; c = ConditionalLogic(1, 1); print(hasattr(c, 'should_continue_capital_flow'))"
  ```

- [ ] **Step 4: Commit**

  ```bash
  git add tradingagents/graph/conditional_logic.py
  git commit -m "feat(graph): should_continue_capital_flow conditional"
  ```

---

### Task 31: Wire `capital_flow` into `graph/setup.py`

**Files:**
- Modify: `tradingagents/graph/setup.py:49-75`

The existing setup loop handles analyst-list expansion automatically via the `analyst_nodes` dict; we add an `if "capital_flow" in selected_analysts` block plus a docstring update.

- [ ] **Step 1: Edit `setup.py`** — after the existing `if "fundamentals" in selected_analysts:` block, add:

  ```python
          if "capital_flow" in selected_analysts:
              analyst_nodes["capital_flow"] = create_capital_flow_analyst(
                  self.quick_thinking_llm
              )
              delete_nodes["capital_flow"] = create_msg_delete()
              tool_nodes["capital_flow"] = self.tool_nodes["capital_flow"]
  ```

- [ ] **Step 2: Update the docstring** of `setup_graph` to mention `"capital_flow": Capital Flow Analyst (A-share-only)`.

- [ ] **Step 3: Smoke check** — verify graph construction with capital_flow

  ```bash
  .venv/bin/python -c "
  from tradingagents.default_config import DEFAULT_CONFIG
  from tradingagents.graph.trading_graph import TradingAgentsGraph
  cfg = DEFAULT_CONFIG.copy()
  cfg['llm_provider']='deepseek'
  cfg['deep_think_llm']='deepseek-v4-pro'
  cfg['quick_think_llm']='deepseek-v4-flash'
  ta = TradingAgentsGraph(
      debug=False,
      selected_analysts=['market','social','news','fundamentals','capital_flow'],
      config=cfg,
  )
  print('graph compiled OK')
  " 2>&1 | tail -5
  ```

  Expected: prints `graph compiled OK`. **This will likely fail at Step 4-style issues** (tool_nodes["capital_flow"] doesn't exist yet) — that's expected. Proceed to Task 32, then re-run.

- [ ] **Step 4: Commit** (even if smoke fails — the wiring is correct; Task 32 supplies the missing tool node):

  ```bash
  git add tradingagents/graph/setup.py
  git commit -m "feat(graph): wire capital_flow analyst into setup_graph"
  ```

---

### Task 32: Add `capital_flow` tool node + log field in `trading_graph.py`

**Files:**
- Modify: `tradingagents/graph/trading_graph.py:157-191` (`_create_tool_nodes`)
- Modify: `tradingagents/graph/trading_graph.py:356-396` (`_log_state`)

- [ ] **Step 1: Update imports** — at the top of `trading_graph.py`, add to the existing `from tradingagents.agents.utils.agent_utils import ...` block:

  ```python
  from tradingagents.agents.utils.agent_utils import (
      # ... existing imports ...
      get_announcements,
      get_stock_hot_rank,
      get_shareholder_count,
      get_research_reports,
      get_lhb_detail,
      get_lhb_institutional,
      get_north_capital_individual,
      get_north_capital_overall,
      get_margin_trading,
      get_fund_flow,
  )
  ```

- [ ] **Step 2: Update `_create_tool_nodes`** — add the new "capital_flow" entry and extend social/news:

  ```python
      def _create_tool_nodes(self) -> Dict[str, ToolNode]:
          return {
              "market": ToolNode([
                  get_stock_data,
                  get_indicators,
              ]),
              "social": ToolNode([
                  get_news,
                  get_stock_hot_rank,
                  get_shareholder_count,
                  get_research_reports,
              ]),
              "news": ToolNode([
                  get_news,
                  get_global_news,
                  get_insider_transactions,
                  get_announcements,
              ]),
              "fundamentals": ToolNode([
                  get_fundamentals,
                  get_balance_sheet,
                  get_cashflow,
                  get_income_statement,
              ]),
              "capital_flow": ToolNode([
                  get_lhb_detail,
                  get_lhb_institutional,
                  get_north_capital_individual,
                  get_north_capital_overall,
                  get_margin_trading,
                  get_fund_flow,
              ]),
          }
  ```

- [ ] **Step 3: Update `_log_state`** — add `capital_flow_report` to the dict written to JSON (line ~358):

  ```python
              "market_report": final_state["market_report"],
              "sentiment_report": final_state["sentiment_report"],
              "news_report": final_state["news_report"],
              "fundamentals_report": final_state["fundamentals_report"],
              "capital_flow_report": final_state.get("capital_flow_report", ""),
  ```

  Use `.get(..., "")` so old runs without the field don't crash if loaded later.

- [ ] **Step 4: Re-run the smoke compile from Task 31 Step 3** — should pass now.

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/graph/trading_graph.py
  git commit -m "feat(graph): capital_flow tool node + extended social/news toolkits + log field"
  ```

---

### Task 33: Update `social_media_analyst` and `news_analyst` to use new tools

**Files:**
- Modify: `tradingagents/agents/analysts/social_media_analyst.py`
- Modify: `tradingagents/agents/analysts/news_analyst.py`

- [ ] **Step 1: Read social_media_analyst.py to find the tools list**

  ```bash
  cat /Users/jiezihao/Desktop/TradingAgents/tradingagents/agents/analysts/social_media_analyst.py
  ```

  Note the existing `tools = [get_news]` line.

- [ ] **Step 2: Edit `social_media_analyst.py`** — extend the tools list and add an A-share-conditional prompt section:

  ```python
  from tradingagents.agents.utils.agent_utils import (
      build_instrument_context,
      get_news,
      get_stock_hot_rank,
      get_shareholder_count,
      get_research_reports,
      get_language_instruction,
  )
  from tradingagents.dataflows.akshare_common import is_a_share
  ```

  Inside the node function, replace the tools list:

  ```python
      tools = [get_news, get_stock_hot_rank, get_shareholder_count, get_research_reports]
  ```

  And update the system_message to include an A-share-conditional paragraph:

  ```python
      a_share_note = ""
      if is_a_share(state["company_of_interest"]):
          a_share_note = (
              "\n\nIMPORTANT — A-share sentiment guidance:\n"
              "- Primary signals: get_stock_hot_rank (attention/heat) and get_shareholder_count "
              "(falling = institutional accumulation; rising = retail dispersion).\n"
              "- Auxiliary: get_research_reports (analyst consensus and target prices).\n"
              "- Standard get_news still applies but A-share news flow is sparser; weight the "
              "above proxies more heavily.\n"
          )

      system_message = (
          "<existing prompt body>"
          + a_share_note
          + get_language_instruction()
      )
  ```

  Replace `<existing prompt body>` with the verbatim existing prompt text — do not rewrite, just splice in `a_share_note` before `get_language_instruction()`.

- [ ] **Step 3: Edit `news_analyst.py` similarly** — add `get_announcements`, update tools list, add A-share note in prompt:

  ```python
  from tradingagents.agents.utils.agent_utils import (
      build_instrument_context,
      get_news,
      get_global_news,
      get_insider_transactions,
      get_announcements,
      get_language_instruction,
  )
  from tradingagents.dataflows.akshare_common import is_a_share
  ```

  Tools list:

  ```python
      tools = [get_news, get_global_news, get_insider_transactions, get_announcements]
  ```

  A-share prompt note (splice into existing system_message):

  ```python
      a_share_note = ""
      if is_a_share(state["company_of_interest"]):
          a_share_note = (
              "\n\nIMPORTANT — A-share news guidance:\n"
              "- For A-share tickers, get_announcements returns legal disclosure filings "
              "(法定信披) which are the AUTHORITATIVE source — weight these higher than\n"
              "  general get_news.\n"
              "- Get_global_news covers macro / policy signals — important for A-share due\n"
              "  to policy-driven price action.\n"
          )
  ```

- [ ] **Step 4: Smoke** — re-run the compile check from Task 31 Step 3.

- [ ] **Step 5: Commit**

  ```bash
  git add tradingagents/agents/analysts/social_media_analyst.py tradingagents/agents/analysts/news_analyst.py
  git commit -m "feat(analysts): social/news analysts use A-share-specific tools when ticker is A-share"
  ```

---

### Task 34: Inject `capital_flow_report` into downstream prompts

**Files (read each, splice `capital_flow_report` into the existing report bundle):**
- Modify: `tradingagents/agents/researchers/bull_researcher.py`
- Modify: `tradingagents/agents/researchers/bear_researcher.py`
- Modify: `tradingagents/agents/trader/trader.py`
- Modify: `tradingagents/agents/risk_mgmt/aggressive_debator.py`
- Modify: `tradingagents/agents/risk_mgmt/conservative_debator.py`
- Modify: `tradingagents/agents/risk_mgmt/neutral_debator.py`
- Modify: `tradingagents/agents/managers/research_manager.py`
- Modify: `tradingagents/agents/managers/portfolio_manager.py`

For each of the 8 files, follow this pattern:

- [ ] **Step 1: Find the existing report bundle** — these files all have a section that builds a string containing the 4 existing reports:

  ```python
  reports = (
      f"Market report: {market_report}\n"
      f"Sentiment report: {sentiment_report}\n"
      f"News report: {news_report}\n"
      f"Fundamentals report: {fundamentals_report}"
  )
  ```

  Locate the exact lines with `grep -n "fundamentals_report" path/to/file.py`.

- [ ] **Step 2: Add capital_flow_report to that bundle and pull it from state** — for each file:

  ```python
  capital_flow_report = state.get("capital_flow_report", "")
  # ...
  reports = (
      f"Market report: {market_report}\n"
      f"Sentiment report: {sentiment_report}\n"
      f"News report: {news_report}\n"
      f"Fundamentals report: {fundamentals_report}\n"
      f"Capital flow report (A-share only): {capital_flow_report}"
  )
  ```

  And, if the prompt template has an instructional paragraph about how to use the reports, append:

  ```
  For A-share tickers, the capital_flow_report is a critical short-term signal; weight
  it heavily when the holding horizon is short. For non-A-share tickers it will be
  "N/A: ..."; ignore it.
  ```

- [ ] **Step 3: After editing all 8 files, do one final smoke compile**

  ```bash
  .venv/bin/python -c "
  from tradingagents.default_config import DEFAULT_CONFIG
  from tradingagents.graph.trading_graph import TradingAgentsGraph
  cfg = DEFAULT_CONFIG.copy()
  cfg['llm_provider']='deepseek'
  cfg['deep_think_llm']='deepseek-v4-pro'
  cfg['quick_think_llm']='deepseek-v4-flash'
  ta = TradingAgentsGraph(debug=False, selected_analysts=['market','social','news','fundamentals','capital_flow'], config=cfg)
  print('graph compiled OK')
  " 2>&1 | tail -5
  ```

- [ ] **Step 4: Commit each file separately** (or one bundled commit if all edits are mechanically identical):

  ```bash
  git add tradingagents/agents/researchers/ tradingagents/agents/trader/ tradingagents/agents/risk_mgmt/ tradingagents/agents/managers/
  git commit -m "feat(prompts): inject capital_flow_report into bull/bear/trader/risk/PM prompts"
  ```

---

## Phase E — Graph integration test, end-to-end, docs

### Task 35: Graph-level integration test (mock LLM)

**Files:**
- Create: `tests/graph/__init__.py` (empty)
- Create: `tests/graph/test_capital_flow_integration.py`

- [ ] **Step 1: Inspect `tests/conftest.py` for any mock-LLM fixture conventions**

  ```bash
  cat /Users/jiezihao/Desktop/TradingAgents/tests/conftest.py
  ```

- [ ] **Step 2: Create `tests/graph/__init__.py`** (empty)

- [ ] **Step 3: Create `tests/graph/test_capital_flow_integration.py`**

  ```python
  """Graph-level integration test for the capital_flow analyst.

  Uses a mock LLM that emits deterministic tool calls then a final report,
  so the test is hermetic (no network for the LLM) but does exercise the
  full graph wiring. Tool calls themselves are mocked via patching the
  tool wrappers, so this test does not hit akshare.
  """

  import pytest
  from unittest.mock import patch, MagicMock
  from langchain_core.messages import AIMessage

  from tradingagents.default_config import DEFAULT_CONFIG
  from tradingagents.graph.trading_graph import TradingAgentsGraph


  class _FakeLLM:
      """Stand-in for a real LLM. Returns a fixed AIMessage on .invoke()."""

      def __init__(self, content="capital posture: neutral. Rating: Neutral."):
          self.content = content
          self._bound_tools = []

      def bind_tools(self, tools):
          self._bound_tools = tools
          return self

      def invoke(self, _messages):
          # Return an AIMessage with no tool_calls (terminates the ReAct loop)
          return AIMessage(content=self.content, tool_calls=[])


  @pytest.fixture
  def fake_llm():
      return _FakeLLM()


  def _build_graph(fake_llm, selected_analysts):
      cfg = DEFAULT_CONFIG.copy()
      cfg["llm_provider"] = "deepseek"          # any provider; the LLM is mocked
      cfg["deep_think_llm"] = "deepseek-v4-pro"
      cfg["quick_think_llm"] = "deepseek-v4-flash"
      # Patch create_llm_client so TradingAgentsGraph doesn't call deepseek
      with patch("tradingagents.graph.trading_graph.create_llm_client") as mk:
          client = MagicMock()
          client.get_llm.return_value = fake_llm
          mk.return_value = client
          return TradingAgentsGraph(debug=False,
                                     selected_analysts=selected_analysts,
                                     config=cfg)


  def test_capital_flow_analyst_a_share_produces_report(fake_llm):
      ta = _build_graph(fake_llm, ["capital_flow"])
      # Run a single propagation
      final_state, _decision = ta.propagate("600487.SS", "2026-05-08")
      assert "capital_flow_report" in final_state
      assert final_state["capital_flow_report"]    # non-empty


  def test_capital_flow_analyst_non_a_share_returns_na(fake_llm):
      ta = _build_graph(fake_llm, ["capital_flow"])
      final_state, _decision = ta.propagate("NVDA", "2026-05-08")
      assert final_state["capital_flow_report"].startswith("N/A:")
  ```

- [ ] **Step 4: Run the graph integration test**

  ```bash
  .venv/bin/python -m pytest tests/graph/test_capital_flow_integration.py -v
  ```

  Expected: both tests pass. If `propagate` fails for unrelated reasons (e.g. memory log path, checkpointer), patch those out the same way (`patch("tradingagents.graph.trading_graph.TradingMemoryLog")`).

- [ ] **Step 5: Commit**

  ```bash
  git add tests/graph/__init__.py tests/graph/test_capital_flow_integration.py
  git commit -m "test(graph): capital_flow analyst end-to-end with mock LLM"
  ```

---

### Task 36: Update `run_deepseek.py` and README

**Files:**
- Modify: `run_deepseek.py`
- Modify: `README.md`

- [ ] **Step 1: Edit `run_deepseek.py`** — replace the `ta = TradingAgentsGraph(...)` line:

  ```python
  # 注意：跑 A 股时把 "capital_flow" 加进 selected_analysts，框架会自动启用 A 股资金面分析师
  ta = TradingAgentsGraph(
      debug=True,
      selected_analysts=["market", "social", "news", "fundamentals", "capital_flow"],
      config=config,
  )
  ```

- [ ] **Step 2: Add an A-share section to README.md** — append before the existing "Installation and CLI" section, or after "TradingAgents Framework":

  ```markdown
  ## A-Share Support

  TradingAgents supports Shanghai / Shenzhen A-shares via the `akshare` vendor.

  ### Ticker format

  Use the yfinance-style exchange suffix:
  - `600487.SS` — Shanghai (codes starting with `6`)
  - `000001.SZ` — Shenzhen (codes starting with `0` / `3`)

  Beijing Stock Exchange (`4` / `8` codes) is not yet supported.

  ### Configuration

  No vendor configuration is needed — `.SS` / `.SZ` tickers automatically route
  to akshare. Add `"capital_flow"` to `selected_analysts` to enable the A-share
  Capital Flow Analyst (Dragon-Tiger List, Northbound Capital, Margin Trading,
  smart-money flow):

  ```python
  ta = TradingAgentsGraph(
      selected_analysts=["market", "social", "news", "fundamentals", "capital_flow"],
      config=config,
  )
  ```

  ### Agent coverage on A-shares

  | Agent | A-share signal |
  |---|---|
  | market_analyst | Daily OHLCV + technical indicators (full coverage) |
  | social_analyst | Attention rank + shareholder count + analyst research reports |
  | news_analyst | Individual stock news + legal-disclosure announcements + macro news |
  | fundamentals_analyst | 5y annual + 4Q quarterly statements (full coverage) |
  | capital_flow_analyst | 龙虎榜 / 北上资金 / 融资融券 / 主力资金流向 |

  ### Known limitations

  - Beijing Stock Exchange tickers (codes 4xxxxx / 8xxxxx) are out of scope.
  - Akshare hits public endpoints; occasional rate-limit failures are retried
    with exponential backoff. Persistent failures degrade to a "Data unavailable"
    string the agent reads as missing input rather than crashing the graph.
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add run_deepseek.py README.md
  git commit -m "docs: A-share support section in README + capital_flow in run_deepseek example"
  ```

---

### Task 37: End-to-end smoke run

- [ ] **Step 1: Run the full pipeline on the canonical A-share ticker**

  ```bash
  .venv/bin/python run_deepseek.py 2>&1 | tee /tmp/run_deepseek_a_share_smoke.log
  ```

  Expected: ends with `========== FINAL DECISION ==========\n<Buy|Hold|Sell>`.

- [ ] **Step 2: Validate output completeness** — grep for each analyst's signature in the log:

  ```bash
  for s in "Market Analyst" "Social Analyst" "News Analyst" "Fundamentals Analyst" "Capital_flow Analyst"; do
    grep -c "$s" /tmp/run_deepseek_a_share_smoke.log | xargs -I{} echo "$s: {} hits"
  done
  ```

  Expected: each prints at least 1 hit.

- [ ] **Step 3: Verify capital_flow_report is in the state-log JSON**

  ```bash
  ls ~/.tradingagents/logs/600487_SS/TradingAgentsStrategy_logs/full_states_log_*.json | tail -1 | xargs -I{} .venv/bin/python -c "import json; d = json.load(open('{}')); print('capital_flow_report length:', len(d.get('capital_flow_report', '')))"
  ```

  Expected: non-zero length.

- [ ] **Step 4: If the smoke completes cleanly, no commit needed** (this is verification, not a code change). If smoke uncovers a bug, fix it and commit the fix.

---

## Self-Review

After writing all tasks above, this checklist verifies completeness against the spec.

### Spec coverage map

| Spec section | Plan task(s) |
|---|---|
| 2: scope — 8 existing methods × akshare | Tasks 5-9, 14-17 |
| 2: get_announcements | Task 10 |
| 2: 3 sentiment methods | Tasks 11-13 |
| 2: 6 capital-flow methods | Tasks 18-23 |
| 2: new analyst | Task 29 |
| 2: ticker-suffix routing | Task 3 |
| 3.1 dispatch changes | Task 3 |
| 3.2 vendor registry | Task 4 |
| 3.3 module layout | Tasks 2-4, 5, 8, 11, 14, 18 (file creations) |
| 3.4 API mapping | Embedded in Tasks 5-23 |
| 3.5 fundamentals depth | Task 14 (`_select_periods` helper) |
| 3.6 sentiment routing | Tasks 25, 32, 33 |
| 3.7 error handling | Tasks 2 (`ak_retry`, `NotApplicableError`), 3 (terminal failures) |
| 4.1 graph integration | Tasks 31, 32 |
| 4.2 analyst node | Task 29 |
| 4.3 prompt | Task 29 |
| 4.4 downstream prompts | Task 34 |
| 5.1 unit tests | Tasks 2, 3 |
| 5.2 integration tests | Tasks 5-23 (interleaved) |
| 5.3 graph integration test | Task 35 |
| 6 dependencies | Task 1 |
| 7 documentation | Task 36 |
| 8 milestones | M1 = Tasks 1-4; M2 = 5-17, 24; M3 = 18-23; M4 = 25-34; M5 = 36, 37 |

No gaps.

### Type / name consistency

- `NotApplicableError` defined in Task 2, used in Tasks 3, 5, 7, 10, 26, 33.
- `is_a_share`, `to_ak_symbol`, `to_ak_symbol_with_market` defined Task 2, used Tasks 5-23, 29, 33.
- `format_df_as_md` defined Task 2, used all Phase B tasks.
- `_select_periods` defined Task 14, used Tasks 14-17. ✅
- `_date_range` defined Task 18, used Tasks 18-22. ✅
- Tool names registered in Task 4 (`get_lhb_detail`, etc.) match wrapper names in Task 26 and tool nodes in Task 32. ✅
- `capital_flow_report` state field consistent: defined Task 27, defaulted Task 28, read Tasks 32 (`_log_state`) and 34 (downstream prompts).

### No placeholders

Scanned for "TBD" / "TODO" / "implement later" / vague "add appropriate" — none found. Every step has either exact code or an exact command with expected output.

---

## Execution Handoff

Plan complete and saved to [`docs/superpowers/plans/2026-05-11-akshare-a-share-support.md`](docs/superpowers/plans/2026-05-11-akshare-a-share-support.md). Two execution options:

**1. Subagent-Driven (recommended)** — Fresh subagent per task with two-stage review between tasks. Best for a plan this long (37 tasks); insulates my context from each task's noise and catches drift between tasks. Total wall-clock: similar to inline, but more parallelisable for phase-internal independent tasks.

**2. Inline Execution** — Run tasks in this session with batch checkpoints (e.g. after Phase A, after Phase B, after Phase C, etc.). More natural turn-taking with you but my context fills up faster.

Which approach?
