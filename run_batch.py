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
    # ---- 第三批：screen_final_534_drop_C_20260512.csv，已剔除 34 只已分析，剩 500 只 ----
    "002468.SZ", "600428.SH", "601872.SH", "600233.SH", "000088.SZ", "000089.SZ", "603713.SH", "002352.SZ",
    "601333.SH", "600221.SH", "600029.SH", "002040.SZ", "600179.SH", "002928.SZ", "601880.SH", "601816.SH",
    "001872.SZ", "601021.SH", "603885.SH", "601326.SH", "000099.SZ", "600009.SH", "601156.SH", "603444.SH",
    "002558.SZ", "603598.SH", "002517.SZ", "603888.SH", "600825.SH", "002256.SZ", "600995.SH", "002039.SZ",
    "605580.SH", "001258.SZ", "600236.SH", "600098.SH", "000899.SZ", "000690.SZ", "600226.SH", "600025.SH",
    "600900.SH", "000791.SZ", "000722.SZ", "001210.SZ", "600578.SH", "600191.SH", "001313.SZ", "002215.SZ",
    "002746.SZ", "002772.SZ", "002299.SZ", "002286.SZ", "002901.SZ", "603259.SH", "600867.SH", "600276.SH",
    "603538.SH", "002294.SZ", "000423.SZ", "600285.SH", "600993.SH", "603520.SH", "002603.SZ", "002349.SZ",
    "601607.SH", "000538.SZ", "000534.SZ", "603351.SH", "600196.SH", "603590.SH", "603939.SH", "603014.SH",
    "002287.SZ", "002107.SZ", "603233.SH", "600763.SH", "003010.SZ", "600415.SH", "601061.SH", "601086.SH",
    "600693.SH", "603101.SH", "605188.SH", "600710.SH", "603268.SH", "000519.SZ", "002414.SZ", "002297.SZ",
    "002111.SZ", "600150.SH", "002338.SZ", "600685.SH", "002625.SZ", "600184.SH", "600316.SH", "002985.SZ",
    "600590.SH", "001218.SZ", "600989.SH", "000893.SZ", "605016.SH", "600378.SH", "002170.SZ", "603276.SH",
    "002588.SZ", "603938.SH", "002246.SZ", "002545.SZ", "601208.SH", "603379.SH", "603683.SH", "002407.SZ",
    "002838.SZ", "002250.SZ", "605020.SH", "000565.SZ", "603334.SH", "002497.SZ", "603663.SH", "002768.SZ",
    "002942.SZ", "002886.SZ", "002669.SZ", "603255.SH", "603928.SH", "600389.SH", "002734.SZ", "002683.SZ",
    "001359.SZ", "600078.SH", "003017.SZ", "002562.SZ", "603650.SH", "002391.SZ", "600160.SH", "603067.SH",
    "603739.SH", "603172.SH", "603181.SH", "605033.SH", "002430.SZ", "002539.SZ", "601678.SH", "000792.SZ",
    "001369.SZ", "001325.SZ", "001231.SZ", "002748.SZ", "002440.SZ", "002917.SZ", "603916.SH", "603256.SH",
    "002080.SZ", "600176.SH", "601112.SH", "600801.SH", "002392.SZ", "603737.SH", "600692.SH", "000036.SZ",
    "603506.SH", "601069.SH", "001337.SZ", "000506.SZ", "002716.SZ", "002155.SZ", "600338.SH", "002378.SZ",
    "600988.SH", "002842.SZ", "603799.SH", "603045.SH", "600549.SH", "002192.SZ", "600111.SH", "000975.SZ",
    "600547.SH", "002460.SZ", "605376.SH", "603979.SH", "600489.SH", "002824.SZ", "000657.SZ", "603115.SH",
    "603271.SH", "000751.SZ", "603124.SH", "000737.SZ", "601899.SH", "002237.SZ", "605208.SH", "601168.SH",
    "600392.SH", "600961.SH", "603132.SH", "000426.SZ", "600768.SH", "000807.SZ", "000408.SZ", "603876.SH",
    "000603.SZ", "601677.SH", "001280.SZ", "600366.SH", "002996.SZ", "600531.SH", "002171.SZ", "000831.SZ",
    "002532.SZ", "000758.SZ", "601388.SH", "000960.SZ", "002379.SZ", "600362.SH", "600490.SH", "601958.SH",
    "601600.SH", "600595.SH", "601609.SH", "000612.SZ", "002975.SZ", "605305.SH", "600499.SH", "605389.SH",
    "603699.SH", "605056.SH", "603308.SH", "603025.SH", "603269.SH", "603638.SH", "605060.SH", "603966.SH",
    "603698.SH", "002979.SZ", "603901.SH", "603416.SH", "600980.SH", "001256.SZ", "603656.SH", "002204.SZ",
    "603270.SH", "002957.SZ", "601766.SH", "603298.SH", "603338.SH", "601100.SH", "600114.SH", "603337.SH",
    "600458.SH", "600558.SH", "601177.SH", "603280.SH", "601399.SH", "002490.SZ", "000680.SZ", "002690.SZ",
    "002353.SZ", "001226.SZ", "002132.SZ", "600320.SH", "000425.SZ", "603029.SH", "600031.SH", "002164.SZ",
    "601608.SH", "603111.SH", "000880.SZ", "601777.SH", "603119.SH", "605228.SH", "603085.SH", "002406.SZ",
    "002448.SZ", "002708.SZ", "603949.SH", "603210.SH", "001285.SZ", "603390.SH", "000951.SZ", "600148.SH",
    "002283.SZ", "603006.SH", "601799.SH", "002997.SZ", "603418.SH", "002488.SZ", "002516.SZ", "603009.SH",
    "002284.SZ", "600099.SH", "000800.SZ", "603166.SH", "600686.SH", "605319.SH", "002906.SZ", "600166.SH",
    "603013.SH", "603190.SH", "000957.SZ", "603049.SH", "605128.SH", "002664.SZ", "000570.SZ", "603997.SH",
    "603129.SH", "002472.SZ", "002126.SZ", "000589.SZ", "600523.SH", "601127.SH", "002590.SZ", "002128.SZ",
    "600395.SH", "603126.SH", "002645.SZ", "600388.SH", "600323.SH", "002266.SZ", "001336.SZ", "003039.SZ",
    "603817.SH", "601033.SH", "601330.SH", "002887.SZ", "603588.SH", "601368.SH", "000551.SZ", "601158.SH",
    "000598.SZ", "600769.SH", "603985.SH", "002487.SZ", "603032.SH", "002169.SZ", "002759.SZ", "002812.SZ",
    "002709.SZ", "603092.SH", "002202.SZ", "603530.SH", "002518.SZ", "002028.SZ", "002245.SZ", "603606.SH",
    "002850.SZ", "002196.SZ", "603659.SH", "601218.SH", "601126.SH", "600884.SH", "600550.SH", "001283.SZ",
    "002560.SZ", "601727.SH", "000682.SZ", "002364.SZ", "600885.SH", "600869.SH", "605117.SH", "600577.SH",
    "600406.SH", "600875.SH", "002335.SZ", "601179.SH", "603819.SH", "002576.SZ", "600478.SH", "002340.SZ",
    "003022.SZ", "002606.SZ", "001389.SZ", "001314.SZ", "002463.SZ", "600601.SH", "603629.SH", "600183.SH",
    "002916.SZ", "002222.SZ", "002475.SZ", "001287.SZ", "603296.SH", "001298.SZ", "002925.SZ", "002436.SZ",
    "002741.SZ", "603459.SH", "000062.SZ", "603186.SH", "603890.SH", "002636.SZ", "002273.SZ", "002947.SZ",
    "002384.SZ", "002859.SZ", "000100.SZ", "603078.SH", "000021.SZ", "002885.SZ", "002841.SZ", "600353.SH",
    "002484.SZ", "600130.SH", "600563.SH", "002137.SZ", "000725.SZ", "002643.SZ", "002402.SZ", "002377.SZ",
    "001316.SZ", "002721.SZ", "605599.SH", "002780.SZ", "002345.SZ", "002293.SZ", "603365.SH", "003016.SZ",
    "001390.SZ", "603889.SH", "002404.SZ", "600630.SH", "603238.SH", "603558.SH", "600398.SH", "600770.SH",
    "000833.SZ", "000421.SZ", "603059.SH", "001328.SZ", "002511.SZ", "603193.SH", "600315.SH", "002609.SZ",
    "601360.SH", "603171.SH", "600455.SH", "002063.SZ", "603615.SH", "002846.SZ", "605099.SH", "001221.SZ",
    "601968.SH", "001238.SZ", "600356.SH", "603733.SH", "002831.SZ", "600382.SH", "000778.SZ", "000783.SZ",
    "000776.SZ", "601375.SH", "601995.SH", "600030.SH", "600369.SH", "601878.SH", "002961.SZ", "601066.SH",
    "600999.SH", "600109.SH", "601456.SH", "000686.SZ", "600918.SH", "601696.SH", "600927.SH", "601901.SH",
    "601377.SH", "601788.SH", "601059.SH", "000166.SZ", "601108.SH", "601881.SH", "600906.SH", "002926.SZ",
    "000750.SZ", "601688.SH", "600901.SH", "600958.SH", "601136.SH", "600061.SH", "603093.SH", "002500.SZ",
    "600830.SH", "600318.SH", "002939.SZ", "600186.SH", "605499.SH", "002956.SZ", "603697.SH", "603170.SH",
    "600298.SH", "603288.SH", "002946.SZ", "600189.SH", "000729.SZ", "601579.SH", "001219.SZ", "600305.SH",
    "603755.SH", "002847.SZ", "002461.SZ", "600887.SH",
]
# 永远采用今日日期；akshare/yfinance 对非交易日会自动返回最近交易日的数据
TRADE_DATE = date.today().strftime("%Y-%m-%d")
OUTPUT_DIR = Path(__file__).parent / "analyses"
WORKERS = 8
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

    # A-share-first vendor routing. The ticker-bearing news tools auto-route
    # to akshare via _detect_market(), but get_global_news takes no ticker so
    # it would default to yfinance — which (a) rate-limits hard from China and
    # (b) catches its own errors and returns a string, defeating the fallback
    # chain. Force the entire news_data category to akshare; the fallback
    # chain still tries yfinance if every akshare endpoint fails.
    config["data_vendors"]            = dict(config.get("data_vendors", {}))
    config["data_vendors"]["news_data"] = "akshare"
    config["tool_vendors"]            = dict(config.get("tool_vendors", {}))
    config["tool_vendors"]["get_global_news"] = "akshare"
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
