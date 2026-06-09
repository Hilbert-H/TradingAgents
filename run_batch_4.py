"""Ad-hoc batch — 4 tickers for today (大港/博通集成/汇顶/通富微电)."""

import os

for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
           "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path


TICKERS = [
    "002077.SZ",   # 大港股份
    "603068.SH",   # 博通集成
    "603160.SH",   # 汇顶科技
    "002156.SZ",   # 通富微电
]
TRADE_DATE = date.today().strftime("%Y-%m-%d")
OUTPUT_DIR = Path(__file__).parent / "analyses"
WORKERS = 4


def analyze_one(ticker: str, trade_date: str, output_dir_str: str) -> tuple:
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

    config["data_vendors"]            = dict(config.get("data_vendors", {}))
    config["data_vendors"]["news_data"] = "akshare"
    config["tool_vendors"]            = dict(config.get("tool_vendors", {}))
    config["tool_vendors"]["get_global_news"] = "akshare"
    config["memory_log_path"] = os.path.expanduser(
        f"~/.tradingagents/memory/trading_memory_pid_{os.getpid()}.md"
    )

    try:
        ta = TradingAgentsGraph(
            debug=False,
            selected_analysts=["market", "social", "news", "fundamentals", "capital_flow"],
            config=config,
        )
        _, decision = ta.propagate(ticker, trade_date)
        md_path = save_analysis_markdown(ta.curr_state, ticker, trade_date, Path(output_dir_str))
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
                print(f"[{completed}/{len(TICKERS)}] FAIL {ticker}: {err.splitlines()[0]}")
            else:
                print(f"[{completed}/{len(TICKERS)}] OK {ticker}: {decision} -> {Path(path).name}")

    print("\n" + "=" * 60)
    print("BATCH SUMMARY")
    print("=" * 60)
    for ticker, decision, path, err in results:
        if err:
            print(f"  FAIL {ticker}: {err.splitlines()[0]}")
        else:
            print(f"  OK {ticker}: {decision} -> {Path(path).name}")


if __name__ == "__main__":
    main()
