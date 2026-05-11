# A-Share Support via Akshare — Design

**Status:** Approved (brainstorming complete, 2026-05-11)
**Author:** Hilbert-H + Claude
**Scope:** Add full A-share (Shanghai / Shenzhen) data support to TradingAgents by introducing `akshare` as a new vendor and a new "Capital Flow Analyst" specialised in A-share short-term capital signals.

---

## 1. Motivation

The framework currently relies on yfinance and alpha_vantage. Both vendors:

- Provide acceptable price + fundamentals for A-share tickers via the `.SS` / `.SZ` suffix
- But return **empty results** for A-share news, social sentiment, and insider transactions
- Have **no coverage** of A-share-specific signals such as 龙虎榜 (Dragon-Tiger List), 北上资金 (Northbound Capital), 融资融券 (Margin Trading), 主力资金流向 (Smart Money Flow)

This makes A-share analyses lopsided: technical + fundamentals carry the entire decision while the news / social / insider analysts contribute "No data" reports. A-share markets are also heavily policy- and capital-flow-driven, so the missing dimensions are exactly the most important ones.

Akshare is an open-source Python library that wraps the public endpoints of 东方财富 / 新浪财经 / 同花顺 / 巨潮 / 沪深交易所. It covers every signal listed above with zero API key and active maintenance.

## 2. Scope

**In scope (v1):**

- New `akshare` vendor implementing all 8 existing abstract methods (`get_stock_data`, `get_indicators`, `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement`, `get_news`, `get_global_news`, `get_insider_transactions`)
- One new abstract method for news: `get_announcements` (法定信披公告, A-share-only)
- Three new abstract methods for sentiment, fed to the existing `social_analyst` on A-share tickers: `get_stock_hot_rank`, `get_shareholder_count`, `get_research_reports`
- Six new capital-flow methods, exposed as a new tools category: `get_lhb_detail`, `get_lhb_institutional`, `get_north_capital_individual`, `get_north_capital_overall`, `get_margin_trading`, `get_fund_flow`
- New analyst: `capital_flow_analyst` — sits parallel to the existing 4 analysts in the graph
- Ticker-suffix-aware vendor routing: `.SS` / `.SZ` tickers automatically route to akshare
- Backward compatibility: US tickers continue to use yfinance/alpha_vantage exactly as before
- Tests: unit (dispatch logic, no network) + integration (real akshare calls, behind pytest marker)
- Documentation: README A-share section + updated `run_deepseek.py` example

**Out of scope (deferred to v2 or rejected):**

- 北交所 tickers (codes starting 4 / 8) — akshare coverage is uneven
- Hong Kong / Taiwan markets via akshare — yfinance is adequate for `.HK`
- On-disk caching of akshare responses — in-process LRU is sufficient for v1
- 大宗交易 (block trades) — high noise, low value for 1-10 day horizon
- 概念板块资金流 (sector fund flow) — overlaps with 主力资金流向, defer
- Tushare Pro as paid fallback — akshare failures surface as "data unavailable" to the agent; no paid dependency added
- A-share ticker validity check (whether `600487` actually exists) — let the akshare call fail naturally
- New unit tests for downstream agent prompt edits (bull/bear/trader/risk/PM) — integration test coverage is sufficient

## 3. Architecture

### 3.1 Vendor dispatch (modified)

`tradingagents/dataflows/interface.py` adds a ticker-suffix detection layer in front of the existing `route_to_vendor`:

```python
A_SHARE_SUFFIXES = (".SS", ".SZ")

def _detect_market(ticker: str) -> str:
    if not ticker:
        return "global"
    return "a_share" if ticker.upper().endswith(A_SHARE_SUFFIXES) else "global"

def route_to_vendor(method, *args, **kwargs):
    category = get_category_for_method(method)
    ticker = args[0] if args else kwargs.get("ticker") or kwargs.get("symbol")
    market = _detect_market(ticker)

    if market == "a_share":
        tool_override = config.get("tool_vendors", {}).get(method)
        primary_vendors = [tool_override] if tool_override else ["akshare"]
        logger.info("Ticker %s detected as A-share, routing %s to akshare", ticker, method)
    else:
        vendor_config = get_vendor(category, method)
        primary_vendors = [v.strip() for v in vendor_config.split(",")]

    # existing fallback-chain logic unchanged
    ...
```

The user's `tool_vendors` override (method-level) still wins — a user can force alpha_vantage for a specific A-share method if needed.

### 3.2 Vendor registry (extended)

`VENDOR_LIST` gains `"akshare"`. Every existing entry in `VENDOR_METHODS` gets an `"akshare": get_*_akshare` line. New entries are added for the announcement method and the six capital-flow methods. A new `capital_flow` category is added to `TOOLS_CATEGORIES`:

```python
TOOLS_CATEGORIES["capital_flow"] = {
    "description": "A-share capital flow signals",
    "tools": ["get_lhb_detail", "get_lhb_institutional",
              "get_north_capital_individual", "get_north_capital_overall",
              "get_margin_trading", "get_fund_flow"],
}

# The 3 new sentiment methods go into the existing news_data category
# (the category is just a routing bucket, not a semantic claim, and avoids
# multiplying configuration surface).
TOOLS_CATEGORIES["news_data"]["tools"].extend(
    ["get_announcements", "get_stock_hot_rank",
     "get_shareholder_count", "get_research_reports"])
```

`VENDOR_METHODS` additions for the 4 new news_data methods (akshare-only):

```python
"get_announcements":      {"akshare": get_announcements_akshare},
"get_stock_hot_rank":     {"akshare": get_stock_hot_rank_akshare},
"get_shareholder_count":  {"akshare": get_shareholder_count_akshare},
"get_research_reports":   {"akshare": get_research_reports_akshare},
```

### 3.3 Akshare module layout

```
tradingagents/dataflows/
├── akshare_common.py        # NotApplicableError, is_a_share, to_ak_symbol,
│                              to_ak_symbol_with_market, ak_retry, format_df_as_md
├── akshare_market.py        # get_stock, get_indicator (via stockstats_utils),
│                              get_insider_transactions
├── akshare_news.py          # get_news, get_global_news, get_announcements
├── akshare_sentiment.py     # get_stock_hot_rank, get_shareholder_count,
│                              get_research_reports
├── akshare_fundamentals.py  # get_fundamentals, get_balance_sheet,
│                              get_cashflow, get_income_statement
└── akshare_capital_flow.py  # six capital-flow methods
```

Function signatures match yfinance counterparts: first positional arg is the ticker (with suffix), return type is a markdown-formatted `str`.

### 3.4 Akshare-to-public-API mapping

| Our method | Akshare call |
|---|---|
| `get_stock_akshare` | `ak.stock_zh_a_hist(symbol, period="daily", start_date, end_date, adjust="qfq")` |
| `get_indicator_akshare` | reuse existing `stockstats_utils` (pure-Python) on the hist DataFrame |
| `get_insider_transactions_akshare` | `ak.stock_ggcg_em(symbol)` + `ak.stock_share_hold_change_szse` / `_sse` |
| `get_news_akshare` | `ak.stock_news_em(symbol)`, filter by `publish_time` |
| `get_global_news_akshare` | `ak.stock_info_global_em()` |
| `get_announcements_akshare` | `ak.stock_notice_report(symbol="all", date)`, filter by ticker |
| `get_fundamentals_akshare` | `ak.stock_financial_abstract_ths(symbol, indicator="按报告期")` |
| `get_balance_sheet_akshare` | `ak.stock_balance_sheet_by_report_em(symbol="SH600487")` |
| `get_cashflow_akshare` | `ak.stock_cash_flow_sheet_by_report_em(symbol="SH600487")` |
| `get_income_statement_akshare` | `ak.stock_profit_sheet_by_report_em(symbol="SH600487")` |
| `get_lhb_detail_akshare` | `ak.stock_lhb_stock_detail_em(symbol, date)` |
| `get_lhb_institutional_akshare` | `ak.stock_lhb_jgmmtj_em(start_date, end_date)` filtered by ticker |
| `get_north_capital_individual_akshare` | `ak.stock_hsgt_individual_em(stock)` |
| `get_north_capital_overall_akshare` | `ak.stock_hsgt_north_net_flow_in_em(symbol="北上")` |
| `get_margin_trading_akshare` | `ak.stock_margin_detail_szse` / `_sse` filtered by ticker + date window |
| `get_fund_flow_akshare` | `ak.stock_individual_fund_flow(stock, market)` |
| `get_stock_hot_rank_akshare` | `ak.stock_hot_rank_em()` + `ak.stock_hot_rank_wc()`, filter by ticker |
| `get_shareholder_count_akshare` | `ak.stock_zh_a_gdhs(symbol)` |
| `get_research_reports_akshare` | `ak.stock_research_report_em(symbol)`, filter by date window |

Ticker normalisation:
- `to_ak_symbol("600487.SS")` → `"600487"` for endpoints taking the bare code
- `to_ak_symbol_with_market("600487.SS")` → `"SH600487"` (codes starting `6` → SH; `0` / `3` → SZ; `4` / `8` raise `NotApplicableError` — 北交所 out of scope)

### 3.5 Fundamentals depth

A-share companies report 4 periods per year (年报 + 三季报 + 中报 + 一季报). To keep prompt size aligned with US tickers, akshare fundamentals return:

- 5 most recent annual reports
- 4 most recent quarters

Total 9 periods per statement, matching yfinance's depth. A future `fundamentals_history_depth` config knob can be added if 5 years proves insufficient.

### 3.6 Sentiment data routing

The existing `social_analyst` continues to call `get_news` (which routes to akshare for A-shares and surfaces 东财个股新闻). On top of that, three additional tools are wired into `social_analyst`'s toolkit:

- `get_stock_hot_rank` — relative attention rank (primary signal)
- `get_shareholder_count` — chip concentration proxy (primary signal)
- `get_research_reports` — analyst consensus snippets (auxiliary signal)

These methods are registered in `VENDOR_METHODS` (akshare-only) and raise `NotApplicableError` for non-A-share tickers, so on US tickers the fallback chain returns the `N/A: ...` string (see section 3.7) and `social_analyst` treats them as missing inputs. The methods live in the `news_data` category to avoid multiplying configuration surface (the category is a routing bucket, not a semantic claim).

`trading_graph._create_tool_nodes` is updated so the `social` ToolNode includes these 3 additional tools in addition to the existing `get_news`.

The `social_analyst` prompt gains an A-share-conditional paragraph (templated, only inserted when the ticker is A-share) emphasising heat + shareholder concentration as primary signals and research reports as auxiliary.

### 3.7 Error handling

`route_to_vendor` is extended so the graph never crashes on a vendor failure:

```
For each method call:
  1. NotApplicableError raised by a vendor implementation
     → fallback chain skips that vendor, tries next vendor in chain
     → if chain exhausted with all NotApplicableError, return a string:
       "N/A: {method} is only available for A-share tickers (got: {ticker})"
       (DO NOT raise RuntimeError as the current implementation does for
       "No available vendor" — that would crash the graph)
  2. Transient errors (network timeout, rate limit) inside akshare functions
     → ak_retry decorator: 3 attempts with exponential backoff
     → if all retries fail, re-raise as a regular Exception
  3. Other Exception caught at fallback-chain level
     → log warning, try next vendor in chain
     → if chain exhausted, return: "Data unavailable: {last error reason}"
```

The change to `route_to_vendor` is small: wrap the existing chain loop so terminal failures return a string instead of raising. This is needed both for akshare-only methods (e.g. `get_announcements` on a US ticker) and for the broader robustness goal — agents currently can crash the whole graph if every vendor in the chain errors.

## 4. Capital Flow Analyst

### 4.1 Graph integration

Sits parallel to the existing 4 analysts. Enabled by including `"capital_flow"` in `selected_analysts` (default config does NOT include it; users opt in for A-share runs).

```
START → market_analyst       → ...
      → social_analyst       → ...
      → news_analyst         → ...
      → fundamentals_analyst → ...
      → capital_flow_analyst → ...   (only if "capital_flow" in selected_analysts)
                              ↓
                          research_manager → bull/bear → trader → risk → PM
```

`tradingagents/graph/setup.py` adds the conditional wiring (mirroring the existing 4 analysts). `conditional_logic.py` gains `should_continue_capital_flow`. `agent_states.AgentState` gains a `capital_flow_report: str` field, with `""` as the default in `propagation.create_initial_state`. `trading_graph._log_state` writes the new field into the JSON log.

### 4.2 Analyst node

```python
def create_capital_flow_analyst(llm, toolkit):
    def capital_flow_analyst_node(state):
        ticker = state["company_of_interest"]
        if not is_a_share(ticker):
            return {"capital_flow_report":
                    f"N/A: {ticker} is not an A-share; capital_flow analysis skipped."}
        tools = [toolkit.get_lhb_detail, toolkit.get_lhb_institutional,
                 toolkit.get_north_capital_individual, toolkit.get_north_capital_overall,
                 toolkit.get_margin_trading, toolkit.get_fund_flow]
        # ReAct loop with system_prompt; returns capital_flow_report + messages
        ...
    return capital_flow_analyst_node
```

### 4.3 Prompt skeleton (English, with `output_language_hint` suffix as elsewhere)

```
You are the Capital Flow Analyst for A-share equity {ticker} on {trade_date}.

Read short-term capital signals that are UNIQUE to the Chinese A-share market:

1. 龙虎榜 (Dragon-Tiger List): which institutional / hot-money seats
   bought or sold today; institutional net flow trend over recent days
2. 北上资金 (Northbound Capital): foreign (Stock Connect) holding
   changes for this ticker; overall market net flow as a market-mood proxy
3. 融资融券 (Margin Trading): financing balance trend = retail leverage
   sentiment; securities-lending balance = short interest proxy
4. 主力资金流向 (Smart Money Flow): today's super-large / large / medium /
   small order net flows — who is accumulating vs distributing

Produce a structured report with:
- One-line capital posture (accumulating / distributing / neutral)
- Per-signal section with concrete numbers and 1-week trend
- A capital-flow confidence rating (Strong Bullish / Bullish / Neutral / Bearish / Strong Bearish)
- Key risks visible in the data (e.g. retail leverage at multi-year high → squeeze risk)

This is short-term flow analysis (1-10 day horizon). DO NOT make long-term
valuation calls — that's the fundamentals analyst's job.

{output_language_hint}
```

### 4.4 Downstream prompt adjustments

The `capital_flow_report` field is added to the report bundle visible to bull / bear / trader. Risk and PM also receive it as auxiliary. Template insertion:

```
Reports available:
- Market technical:     {market_report}
- Social sentiment:     {sentiment_report}
- News:                 {news_report}
- Fundamentals:         {fundamentals_report}
- Capital flow (A-share only): {capital_flow_report}

For A-share tickers, capital_flow is a critical short-term signal — weight
it heavily when the holding horizon is short. For non-A-share tickers it
will be "N/A"; ignore it.
```

This insertion is added to the following files wherever the existing 4 reports are referenced:

- `tradingagents/agents/researchers/bull_researcher.py`
- `tradingagents/agents/researchers/bear_researcher.py`
- `tradingagents/agents/trader/trader.py`
- `tradingagents/agents/risk_mgmt/aggressive_debator.py`
- `tradingagents/agents/risk_mgmt/conservative_debator.py`
- `tradingagents/agents/risk_mgmt/neutral_debator.py`
- `tradingagents/agents/managers/research_manager.py`
- `tradingagents/agents/managers/portfolio_manager.py`

## 5. Tests

### 5.1 Unit (no network, always run)

```
tests/dataflows/test_akshare_dispatch.py
  - _detect_market: "600487.SS"/"000001.SZ" → "a_share"; "NVDA"/""/None → "global"
  - is_a_share boundary cases (case, whitespace)
  - to_ak_symbol: "600487.SS" → "600487"; raises on non-A-share
  - to_ak_symbol_with_market: "600487.SS" → "SH600487"; "000001.SZ" → "SZ000001"; "830000.SS" raises NotApplicableError
  - route_to_vendor swaps primary to akshare for A-share ticker
  - tool_vendors override beats auto-routing
  - akshare get_* functions raise NotApplicableError for non-A-share tickers
  - route_to_vendor returns "N/A: ..." string (not raise) when an
    akshare-only method is called with a non-A-share ticker
  - route_to_vendor returns "Data unavailable: ..." string when every vendor
    in the fallback chain raises a non-NotApplicableError exception
```

### 5.2 Integration (real akshare calls, marker `@pytest.mark.integration`)

```
tests/dataflows/test_akshare_market.py
tests/dataflows/test_akshare_news.py
tests/dataflows/test_akshare_sentiment.py
tests/dataflows/test_akshare_fundamentals.py
tests/dataflows/test_akshare_capital_flow.py
  - Each akshare function called with 600487.SS / recent date
  - Assert: no exception, return is non-empty string, contains expected
    field markers (e.g. dragon-tiger output contains "营业部" or "机构专用";
    hot_rank output contains a numeric rank field)
```

Run defaults: `pytest -m "not integration"` skips them; explicit `pytest -m integration` opt-in.

### 5.3 Graph integration

```
tests/graph/test_capital_flow_integration.py
  - Mock LLM that returns fixed tool-call plans + a fixed final report
  - selected_analysts=["capital_flow"], ticker="600487.SS"
    → final_state["capital_flow_report"] non-empty, contains rating keyword
  - Same config, ticker="NVDA"
    → final_state["capital_flow_report"] starts with "N/A:"
```

No new unit tests for the prompt edits in bull / bear / trader / risk / PM — covered by the integration test above + the existing end-to-end smoke run.

## 6. Dependencies

```toml
# pyproject.toml dependencies (append)
"akshare>=1.18.0",
```

No version pin; matches existing dependency style.

## 7. Documentation

- `README.md` — new "A-share support" section: ticker format, `selected_analysts` configuration, agent coverage matrix (which agent works on what), akshare data source disclosure, known limitations (北交所 / akshare rate-limit behaviour)
- `run_deepseek.py` — comment block explaining A-share-specific config (already partially in place from the previous edit); update `selected_analysts` to include `"capital_flow"`
- `docs/superpowers/specs/2026-05-11-akshare-a-share-support-design.md` — this document

## 8. Milestones

| Milestone | Content | Estimate |
|---|---|---|
| M1 | `akshare_common` + dispatch changes + unit tests | 0.5 d |
| M2 | `akshare_market` + `akshare_news` + `akshare_sentiment` + `akshare_fundamentals` + integration tests | 1.0 d |
| M3 | `akshare_capital_flow` + integration tests | 0.5 d |
| M4 | `capital_flow_analyst` + graph wiring + state field + downstream prompt edits | 0.5 d |
| M5 | `run_deepseek.py` update + README + end-to-end smoke run on `600487.SS` | 0.25 d |
| **Total** | | **~2.75 days** |

Each milestone is independently committable and independently verifiable.

## 9. Open Questions

None at this time. All decisions made during brainstorming (sections 1–5).

## 10. Decision Log (from brainstorming)

| # | Question | Decision |
|---|---|---|
| 1 | Scope | C — Complete vendor + A-share specialty signals |
| 2 | Capital-flow integration approach | A — New independent analyst |
| 2b | Activation mode | ii — Explicit `selected_analysts` config |
| 3 | Vendor routing | i — Auto by ticker suffix `.SS` / `.SZ` |
| 4 | Sentiment data | iv with priority — heat + 户数 main, research reports auxiliary |
| 5 | Capital-flow tools | 6 tools: 龙虎榜 detail + institutional, 北上 individual + overall, 融资融券, 主力资金流向 |
| 6 | News coverage | 个股新闻 + 公告 (new method) + 财经要闻 |
| 7 | Insider scope | 高管 + 重要股东 (5%+) |
| 8 | Fundamentals depth | 5 yr annual + 4Q, aligned with yfinance |
