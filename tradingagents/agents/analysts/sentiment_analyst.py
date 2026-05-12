"""Sentiment analyst — multi-source sentiment analysis for a target ticker.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news — which led LLMs to fabricate
Reddit/X/StockTwits content under prompt pressure (verified live).

The redesigned agent pre-fetches complementary data sources before the
LLM is invoked and injects them into the prompt as structured blocks.
The data sources are chosen per market:

A-share (``.SS`` / ``.SH`` / ``.SZ`` suffix) — pulled from akshare:
  1. **News headlines** — East-Money per-stock news, past 7 days
  2. **East-Money attention rank** — current top-100 + per-stock daily history
  3. **Shareholder count** — quarterly history (chip-concentration proxy)
  4. **Research reports** — analyst target prices and ratings, past 7 days

  StockTwits and Reddit are skipped — they have zero coverage of A-share
  cashtags / tickers and previously returned empty placeholders that
  triggered the LLM into "based on limited data" narratives.

Non-A-share (US, HK, EU, etc.):
  1. News headlines — Yahoo Finance, past 7 days
  2. StockTwits messages — retail-trader cashtag stream
  3. Reddit posts — r/wallstreetbets, r/stocks, r/investing

The agent does not use tool-calling; the data is in the prompt from
turn 0. The LLM produces the sentiment report in a single invocation.

See: https://github.com/TauricResearch/TradingAgents/issues/557
"""

from datetime import datetime, timedelta

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_news,
    get_stock_hot_rank,
    get_shareholder_count,
    get_research_reports,
)
from tradingagents.dataflows.akshare_common import is_a_share
from tradingagents.dataflows.reddit import fetch_reddit_posts
from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Routes to one of two data-source bundles based on whether the target
    ticker is an A-share, then produces a sentiment report in a single
    LLM call (no tool-calling — data is pre-fetched into the prompt).
    """

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = build_instrument_context(ticker)

        if is_a_share(ticker):
            system_message = _build_a_share_system_message(
                ticker=ticker, start_date=start_date, end_date=end_date,
            )
        else:
            system_message = _build_us_system_message(
                ticker=ticker, start_date=start_date, end_date=end_date,
            )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    "\n{system_message}\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "sentiment_report": result.content,
        }

    return sentiment_analyst_node


# ---------------------------------------------------------------------------
# A-share branch — uses akshare-native signals
# ---------------------------------------------------------------------------
def _build_a_share_system_message(*, ticker: str, start_date: str, end_date: str) -> str:
    """Pre-fetch A-share-specific signals and assemble the system message.

    Every fetcher degrades gracefully to a placeholder string, so the LLM
    always sees something — either real data or a clear unavailable marker.
    """
    news_block        = get_news.func(ticker, start_date, end_date)
    hot_rank_block    = get_stock_hot_rank.func(ticker, end_date)
    shareholder_block = get_shareholder_count.func(ticker, end_date)
    research_block    = get_research_reports.func(ticker, start_date, end_date)

    return f"""You are an A-share market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering {start_date} → {end_date}, drawing on four complementary data sources that have already been collected for you from akshare (东方财富 / 同花顺).

## Data sources (pre-fetched, in this prompt)

### 1) 近期新闻舆情 — 东方财富个股新闻，过去 7 天
Per-stock news from East-Money. Fact-driven; includes both standalone company stories and the stock's appearance in sector / theme round-ups.

<start_of_news>
{news_block}
<end_of_news>

### 2) 东方财富热度排名 — 当前 snapshot + 个股近 30 个交易日历史
Attention-rank signal — measures retail interest. The "新晋粉丝 / 铁杆粉丝" ratio matters: rising new-fan share signals fresh attention; high tenured-fan share signals stable conviction.

<start_of_hot_rank>
{hot_rank_block}
<end_of_hot_rank>

### 3) 股东户数 / 筹码集中度 — 季度历史
Chip-concentration proxy. **Falling shareholder count = chips concentrating (often institutional accumulation)**; rising count = retail dispersion. Compare户均持股 trend with price.

<start_of_shareholder>
{shareholder_block}
<end_of_shareholder>

### 4) 研报 / 机构评级 — 过去 7 天分析师研报
Sell-side institutional view. Look for: ratings (买入 / 增持 / 持有), target prices (目标价), rating changes (upgrade / downgrade), and the underlying logic in 标题.

<start_of_research>
{research_block}
<end_of_research>

## Required output structure (MUST follow exactly — top-level sections are H3 ``###`` to nest cleanly under the report's outer H2 heading)

### 一、综合情绪结论
One-paragraph headline: overall sentiment direction (看多 / 看空 / 中性 / 分化) + confidence level + key driver.

### 二、近期新闻舆情分析
Walk through the news block above. Pull out concrete events with dates. Note板块联动 vs. 公司专属事件. If news mentioned the stock only in a list (e.g. "100股获机构买入评级"), say so — it's weaker signal than a dedicated headline.

### 三、东方财富热度排名分析
**This MUST be its own section.** Report the current rank (or "未进入top-100"), then describe the 30-day trajectory using specific numbers from the data block. Cross-reference with price action where possible. Flag any 新晋粉丝 / 铁杆粉丝 ratio shifts.

### 四、股东户数 / 筹码集中度分析
**This MUST be its own section.** Use the actual numbers from the shareholder block. If only old data is available (akshare sometimes returns data through 2022 only), state the cutoff explicitly. Quote最近两期股东户数 and 户均持股市值, compute % change, and interpret (机构吸筹 vs. 散户化).

### 五、研报 / 机构评级分析
**This MUST be its own section.** List each research report with: date, 机构, 评级, 目标价 (if any), and the one-line thesis. If no reports in window, say so explicitly. Then compute aggregate signal: 几家买入 / 几家增持 / 几家中性, average目标价 if computable.

### 六、关键风险与催化剂
What in the data could move the price in the next 1–5 trading days?

### 七、关键信号汇总表 (markdown table)
Columns: 维度 | 信号 | 方向（看多/看空/中性）| 重要性 (⭐⭐⭐⭐⭐). One row per data source.

## Rules
1. **No fabrication.** If a data block contains "_Source unavailable_" or "_No data._", explicitly say so in the corresponding section and base your judgment only on what you do have.
2. **Cite specific numbers** — dates, percentages, rank values, target prices. The reader will fact-check.
3. **No StockTwits / Reddit references** — those sources have no A-share coverage and are not provided.
4. **Sections 二 through 五 are MANDATORY independent subsections.** Do not collapse 股东户数 into the summary table only, and do not merge 研报 into 新闻 — each gets its own ``###`` section with at least a short paragraph plus the relevant data points even when data is sparse.

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Non-A-share branch — original upstream design
# ---------------------------------------------------------------------------
def _build_us_system_message(*, ticker: str, start_date: str, end_date: str) -> str:
    """Pre-fetch StockTwits + Reddit + news and assemble the upstream-style prompt."""
    news_block = get_news.func(ticker, start_date, end_date)
    stocktwits_block = fetch_stocktwits_messages(ticker, limit=30)
    reddit_block = fetch_reddit_posts(ticker)

    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on three complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### StockTwits messages — retail-trader social platform indexed by cashtag
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

### Reddit posts — r/wallstreetbets, r/stocks, r/investing (past 7 days)
Community discussion. Engagement signal via upvote score and comment count. Subreddit character matters (r/wallstreetbets is often contrarian/exuberant; r/stocks more measured; r/investing longer-term).

<start_of_reddit>
{reddit_block}
<end_of_reddit>

## How to analyze this data (best practices)

1. **Read the StockTwits Bullish/Bearish ratio as a leading retail-sentiment signal.** A 70/30 bullish/bearish split is moderately bullish; ≥90/10 may indicate over-extension and contrarian risk; 50/50 is uncertainty. Sample size matters — base rates on the actual message count, not percentages alone.

2. **Look for cross-source divergences.** If news framing is bearish but StockTwits is overwhelmingly bullish, that mismatch is itself a signal — it can mean retail is leaning into a thesis the news flow hasn't caught up to (or vice versa, that retail is chasing while institutions are cautious).

3. **Weight Reddit posts by engagement.** A 400-upvote / 200-comment thread reflects community attention; a 3-upvote post is noise. Read the body excerpts for context — the title alone often misleads.

4. **Distinguish opinion from event.** A news headline ("Nvidia announces $500M Corning deal") is an event; a StockTwits post ("buying NVDA, this is going to moon") is opinion. Both are inputs but should be weighted differently in your conclusions.

5. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment.

6. **Be honest about data limits.** If StockTwits returned only a handful of messages, or one or more sources returned an "<unavailable>" placeholder, the sentiment read is less robust — flag this caveat explicitly. If the sources are silent on a given subreddit, say so.

7. **Identify catalysts and risks** that emerge across sources — news of upcoming earnings, product launches, competitive threats, macro headlines, etc.

8. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

## Output

Produce a sentiment report covering, in order:

1. **Overall sentiment direction** — Bullish / Bearish / Neutral / Mixed — with a brief confidence note based on data quality and sample size.
2. **Source-by-source breakdown** — what each of news / StockTwits / Reddit is telling you, with specific evidence (cite message counts, ratios, notable posts).
3. **Divergences, alignments, and key narratives** across sources.
4. **Catalysts and risks** surfaced by the data.
5. **Markdown table** at the end summarizing key sentiment signals, their direction, source, and supporting evidence.

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
