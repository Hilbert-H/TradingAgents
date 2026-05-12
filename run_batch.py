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
from datetime import date
from pathlib import Path


# ----- batch config -----
TICKERS = [
    # ---- 第二批：用户名单 64 只，已剔除 8 只已分析（浪潮信息/智微智能/日海智能/
    # 星网锐捷/海康威视/金溢科技/中科曙光/新洁能），剩 56 只 ----
    "000034.SZ",  # 神州数码
    "000157.SZ",  # 中联重科
    "000555.SZ",  # 神州信息
    "000560.SZ",  # 我爱我家
    "000625.SZ",  # 长安汽车
    "000967.SZ",  # 盈峰环境
    "001230.SZ",  # 劲旅环境
    "001388.SZ",  # 信通电子
    "002044.SZ",  # 美年健康
    "002065.SZ",  # 东华软件
    "002090.SZ",  # 金智科技
    "002139.SZ",  # 拓邦股份
    "002212.SZ",  # 天融信
    "002229.SZ",  # 鸿博股份
    "002265.SZ",  # 建设工业
    "002279.SZ",  # 久其软件
    "002298.SZ",  # 中电鑫龙
    "002355.SZ",  # 兴民智通
    "002362.SZ",  # 汉王科技
    "002373.SZ",  # 千方科技
    "002398.SZ",  # 垒知集团
    "002413.SZ",  # 雷科防务
    "002444.SZ",  # 巨星科技
    "002587.SZ",  # 奥拓电子
    "002602.SZ",  # 世纪华通
    "002611.SZ",  # 东方精工
    "002649.SZ",  # 博彦科技
    "002803.SZ",  # 吉宏股份
    "002829.SZ",  # 星网宇达
    "002845.SZ",  # 同兴达
    "002862.SZ",  # 实丰文化
    "002878.SZ",  # 元隆雅图
    "002889.SZ",  # 东方嘉盛
    "003007.SZ",  # 直真科技
    "003013.SZ",  # 地铁设计
    "600704.SH",  # 物产中大
    "600718.SH",  # 东软集团
    "600756.SH",  # 浪潮软件
    "600797.SH",  # 浙大网新
    "600839.SH",  # 四川长虹
    "600880.SH",  # 博瑞传播
    "600959.SH",  # 江苏有线
    "600986.SH",  # 浙文互联
    "601609.SH",  # 金田股份
    "603000.SH",  # 人民网
    "603018.SH",  # 华设集团
    "603123.SH",  # 翠微股份
    "603322.SH",  # 超讯通信
    "603602.SH",  # 纵横通信
    "603611.SH",  # 诺力股份
    "603613.SH",  # 国联股份
    "603633.SH",  # 徕木股份
    "603636.SH",  # 南威软件
    "603686.SH",  # 福龙马
    "603956.SH",  # 威派格
    "605168.SH",  # 三人行
]
# 永远采用今日日期；akshare/yfinance 对非交易日会自动返回最近交易日的数据
TRADE_DATE = date.today().strftime("%Y-%m-%d")
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
