"""TradingAgents DeepSeek runner — A-share aware.

For A-share tickers use yfinance-style suffixes:
  - 600487.SS  Shanghai (codes starting 6)
  - 000001.SZ  Shenzhen (codes starting 0/3)
  - 北交所 (4xxxxx, 8xxxxx) are out of scope

Include "capital_flow" in selected_analysts to enable the A-share-specific
Capital Flow Analyst. The analyst auto-skips for non-A-share tickers.
"""

import os

# IMPORTANT: clear proxy env BEFORE importing any library that initializes
# HTTP connection pools (akshare → requests → urllib3). The akshare/eastmoney
# endpoints are Chinese-domain hosts reachable directly without a proxy; cross-
# border proxies (Clash etc.) often cause SSL/connection failures with these
# hosts. ak_retry also clears env at call time, but some libraries cache the
# proxy state at import time, so we strip env first thing here.
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
           "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

import logging
from dotenv import load_dotenv
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG

# .env loaded from the project root (or the current working directory)
load_dotenv()

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
# Surface the dataflow router's vendor-routing INFO logs (akshare detection)
logging.getLogger("tradingagents.dataflows").setLevel(logging.INFO)

# Configure DeepSeek + A-share-friendly output
config = DEFAULT_CONFIG.copy()
config["llm_provider"]            = "deepseek"
config["deep_think_llm"]          = "deepseek-v4-pro"   # 深度推理用 pro
config["quick_think_llm"]         = "deepseek-v4-flash" # 快速任务用 flash
config["max_debate_rounds"]       = 1
config["max_risk_discuss_rounds"] = 1
config["online_tools"]            = True
config["output_language"]         = "Chinese"  # 分析师/PM 输出中文（agent 内部辩论仍英文）

# Capital Flow Analyst is A-share-only; for non-A-share tickers it short-circuits.
ta = TradingAgentsGraph(
    debug=True,
    selected_analysts=["market", "social", "news", "fundamentals", "capital_flow"],
    config=config,
)

# 亨通光电（上交所 600487）
ticker = "600487.SS"
date   = "2026-05-08"   # YYYY-MM-DD, must be a past trading day
_, decision = ta.propagate(ticker, date)

print("\n========== FINAL DECISION ==========")
print(decision)
