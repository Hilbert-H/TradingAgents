"""Batch runner — analyze multiple tickers in parallel (process pool), write markdown for each.

Edit the TICKERS list and TRADE_DATE below to control what's analyzed.
Output: `analyses/<stock_name>_<code>_<date>.md` (same convention as run_deepseek.py).

Parallelism: WORKERS workers run simultaneously. Each worker is a separate
process with its own LLM client + memory log path (avoids race on shared
state). Keep WORKERS small — DeepSeek API tier rate-limits cap it.

Run with: .venv/bin/python run_batch.py
"""

import os

# Strip proxy BEFORE importing requests/urllib3 (see run_deepseek.py for rationale)
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
           "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path


# ----- batch config -----
TICKERS = [
    # ---- 沪市主板（用户列表 8 只）----
    "600100.SH", "603019.SH", "603106.SH", "603496.SH", "603508.SH",
    "603516.SH", "603660.SH", "605118.SH",
    # ---- 深市主板（19 只，剔除 ST英飞拓 002528）----
    "000066.SZ", "000977.SZ", "000997.SZ", "001229.SZ", "001339.SZ",
    "002152.SZ", "002177.SZ", "002180.SZ", "002197.SZ", "002236.SZ",
    "002268.SZ", "002376.SZ", "002415.SZ", "002577.SZ", "002835.SZ",
    "002869.SZ", "002912.SZ", "002970.SZ", "002990.SZ",
    # ---- 通信设备-沪（14 只，剔除已分析 600487 亨通光电 + ST 600734 *ST实达）----
    "600105.SH", "600198.SH", "600345.SH", "600498.SH", "600522.SH",
    "600775.SH", "600776.SH", "601869.SH", "603042.SH", "603083.SH",
    "603118.SH", "603236.SH", "603421.SH", "603803.SH",
    # ---- 通信设备-深（18 只，剔除已分析 002281 光迅科技）----
    "000063.SZ", "000070.SZ", "000586.SZ", "002017.SZ", "002104.SZ",
    "002194.SZ", "002296.SZ", "002313.SZ", "002396.SZ", "002491.SZ",
    "002583.SZ", "002792.SZ", "002796.SZ", "002881.SZ", "002897.SZ",
    "002902.SZ", "003031.SZ", "003040.SZ",
]
TRADE_DATE = "2026-05-11"
OUTPUT_DIR = Path(__file__).parent / "analyses"
WORKERS = 4
# ------------------------


def analyze_one(ticker: str, trade_date: str, output_dir_str: str) -> tuple:
    """Worker — runs in a child process. Returns (ticker, decision, md_path|None, error|None)."""
    # Re-strip proxy in child process (env is inherited but be defensive)
    import os
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
              "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)

    import logging
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s [pid=%(process)d]: %(message)s",
    )
    logging.getLogger("tradingagents.dataflows").setLevel(logging.INFO)

    from pathlib import Path
    from dotenv import load_dotenv
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.graph.markdown_export import save_analysis_markdown
    from tradingagents.graph.opus_spawn import maybe_spawn_opus
    from tradingagents.default_config import DEFAULT_CONFIG

    load_dotenv()
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"]            = "deepseek"
    config["deep_think_llm"]          = "deepseek-v4-pro"
    config["quick_think_llm"]         = "deepseek-v4-flash"
    config["max_debate_rounds"]       = 1
    config["max_risk_discuss_rounds"] = 1
    config["online_tools"]            = True
    config["output_language"]         = "Chinese"
    # Per-worker memory log to avoid race when two workers finish near-simultaneously
    config["memory_log_path"] = os.path.expanduser(
        f"~/.tradingagents/memory/trading_memory_pid_{os.getpid()}.md"
    )

    try:
        ta = TradingAgentsGraph(
            debug=False,  # quieter for parallel workers — full output is in JSON state log + markdown
            selected_analysts=["market", "social", "news", "fundamentals", "capital_flow"],
            config=config,
        )
        _, decision = ta.propagate(ticker, trade_date)
        md_path = save_analysis_markdown(ta.curr_state, ticker, trade_date, Path(output_dir_str))
        # Async fire-and-forget Opus re-run for Buy / Overweight ratings;
        # returns immediately (bash script daemonizes claude).
        maybe_spawn_opus(md_path, decision)
        return (ticker, decision, str(md_path), None)
    except Exception as e:
        import traceback
        return (ticker, None, None, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-500:]}")


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    print(f"\n▶ Batch: {len(TICKERS)} tickers for {TRADE_DATE}, {WORKERS} workers in parallel")
    print(f"▶ Output: {OUTPUT_DIR}\n")

    results = []
    with ProcessPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(analyze_one, t, TRADE_DATE, str(OUTPUT_DIR)): t
            for t in TICKERS
        }
        completed = 0
        for fut in as_completed(futures):
            completed += 1
            ticker, decision, path, err = fut.result()
            results.append((ticker, decision, path, err))
            if err:
                print(f"[{completed}/{len(TICKERS)}] ❌ {ticker}: {err.splitlines()[0]}")
            else:
                print(f"[{completed}/{len(TICKERS)}] ✅ {ticker}: {decision} → {Path(path).name}")

    print("\n" + "=" * 60)
    print("BATCH SUMMARY")
    print("=" * 60)
    for ticker, decision, path, err in results:
        if err:
            print(f"  ❌ {ticker}: {err.splitlines()[0]}")
        else:
            print(f"  ✅ {ticker}: {decision} → {Path(path).name}")


if __name__ == "__main__":
    main()
