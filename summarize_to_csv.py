"""Scan every Markdown report under analyses/ and emit a CSV that joins the
analysis output back onto the input universe.

Each report's structured fields (Portfolio Manager rating, executive summary,
investment thesis, price target, time horizon; Trader action/entry/stop/sizing;
Research Manager recommendation) are extracted with regex from the rendered
sections so we don't need to re-invoke any LLM. The Opus _Opus.md (when present)
overrides the corresponding non-Opus row's "Opus" columns.

Usage
-----
    .venv/bin/python summarize_to_csv.py
      [--input  screen_final_534_drop_C_20260512.csv]
      [--output screen_final_534_drop_C_20260512_with_analysis.csv]
      [--analyses-dir analyses]

The script is idempotent and safe to run while a batch is still in flight —
each call regenerates the output CSV from whatever .md files exist right now.
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, Iterable, Optional

logger = logging.getLogger(__name__)

# === DATA SCHEMA ===
#
# Filename convention (locked by tradingagents/graph/markdown_export.py):
#     <name>_<bare_code>_<YYYY-MM-DD>(_Opus)?.md
# Examples:
#     神州数码_000034_2026-05-12.md
#     神州数码_000034_2026-05-12_Opus.md
#
# Inside each .md, structured fields rendered by tradingagents/agents/schemas.py:
#
#   ## 九、最终决策（Portfolio Manager[ · Opus]）
#     **Rating**: <Buy|Overweight|Hold|Underweight|Sell>
#     **Executive Summary**: <prose ...>
#     **Investment Thesis**: <prose ...>
#     **Price Target**: <number>        (optional)
#     **Time Horizon**: <free text>     (optional)
#
#   ## 七、交易员投资计划（Trader[ · Opus]）
#     **Action**: <Buy|Hold|Sell>
#     **Reasoning**: <prose ...>
#     **Entry Price**: <number>         (optional)
#     **Stop Loss**: <number>           (optional)
#     **Position Sizing**: <free text>  (optional)
#     FINAL TRANSACTION PROPOSAL: **BUY|HOLD|SELL**
#
#   ## 六、多空辩论（Bull / Bear Researchers[ · Opus]）
#     ### Research Manager 判决[（Opus）]
#       **Recommendation**: <Buy|Overweight|Hold|Underweight|Sell>
#       **Rationale**: <prose ...>
#       **Strategic Actions**: <prose ...>
#
# Note: long prose fields may span many lines and contain blank lines.
# A field ends at the next ``**Label**:`` line or the next section header.

FILENAME_RE = re.compile(
    r"^(?P<name>.+?)_(?P<code>\d{6})_(?P<date>\d{4}-\d{2}-\d{2})(?P<opus>_Opus)?\.md$"
)


@dataclass
class AnalysisRow:
    """One ticker × one date × {regular, opus} result row."""
    code: str = ""                 # bare 6-digit code (matches CSV ts_code prefix)
    name: str = ""                 # 中文 short name from filename
    trade_date: str = ""           # YYYY-MM-DD
    # ---- DeepSeek (regular) decision-chain ----
    final_rating: str = ""
    pm_summary: str = ""
    pm_thesis: str = ""
    pm_price_target: str = ""
    pm_time_horizon: str = ""
    trader_action: str = ""
    trader_entry: str = ""
    trader_stop: str = ""
    trader_sizing: str = ""
    rm_recommendation: str = ""
    md_path: str = ""
    # ---- Opus re-run (if present) ----
    final_rating_opus: str = ""
    pm_summary_opus: str = ""
    pm_thesis_opus: str = ""
    pm_price_target_opus: str = ""
    pm_time_horizon_opus: str = ""
    trader_action_opus: str = ""
    rm_recommendation_opus: str = ""
    opus_md_path: str = ""


# Field labels we extract. Each maps to the dataclass attribute name.
_PM_FIELDS = {
    "Rating": "final_rating",
    "Executive Summary": "pm_summary",
    "Investment Thesis": "pm_thesis",
    "Price Target": "pm_price_target",
    "Time Horizon": "pm_time_horizon",
}
_TRADER_FIELDS = {
    "Action": "trader_action",
    "Entry Price": "trader_entry",
    "Stop Loss": "trader_stop",
    "Position Sizing": "trader_sizing",
}
_RM_FIELDS = {
    "Recommendation": "rm_recommendation",
}


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------


# Section headers in the unified report layout. We anchor to the leading
# number+pause so analyst LLMs' "## 一、..." inside their own bodies (which
# get demoted to ### / #### by markdown_export) cannot collide.
_SECTION_HEADER_RE = re.compile(
    r"^##\s+(?P<num>[一二三四五六七八九])、",
    re.MULTILINE,
)


def _slice_section(md: str, section_char: str) -> str:
    """Return the body text of section '<section_char>' or empty string.

    e.g. _slice_section(md, "九") returns everything between the
    ``## 九、...`` header and the next ``## X、...`` header.
    """
    matches = list(_SECTION_HEADER_RE.finditer(md))
    for i, m in enumerate(matches):
        if m.group("num") == section_char:
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
            return md[start:end]
    return ""


def _extract_field(body: str, label: str) -> str:
    """Pull the value for ``**<label>**:`` out of ``body``.

    The value runs from the colon up to (a) the next ``**X**:`` label,
    (b) the next ``### `` subheader, or (c) the body end. Trailing
    whitespace, asterisks, and stray newlines are stripped; the result
    is squashed to a single line (markdown paragraphs collapsed to spaces).
    """
    # Match **label**:  (allow optional space after colon; case-sensitive label)
    pat = re.compile(
        rf"\*\*{re.escape(label)}\*\*:\s*(.*?)(?=\n\s*\*\*[A-Z][A-Za-z ]+\*\*:|\n###\s|\Z)",
        re.DOTALL,
    )
    m = pat.search(body)
    if not m:
        return ""
    raw = m.group(1).strip()
    # Collapse internal newlines to spaces; drop double-asterisk emphasis.
    raw = re.sub(r"\s+", " ", raw)
    # Pull plain text out of leading bold markers like "**Buy**" → "Buy".
    raw = re.sub(r"^\*\*(.+?)\*\*\s*", r"\1 ", raw)
    return raw.strip()


def _research_manager_block(section_six_body: str) -> str:
    """Inside section 六, return only the 'Research Manager 判决' subsection."""
    # Subsection header tolerates either '判决' or '判决（Opus）'
    m = re.search(
        r"###\s+Research Manager\s+判决[^\n]*\n(.*?)(?=\n###\s|\Z)",
        section_six_body,
        re.DOTALL,
    )
    return m.group(1) if m else ""


# Fallback regexes for old free-text reports (pre-unified-format change):
#   - H1 title carries the rating in the new format, e.g.
#     "# 神州数码 (000034.SZ) 投资分析报告 · 最终决策：Overweight（增持）"
#   - Old reports lack the structured **Rating**: line but emit
#     "FINAL TRANSACTION PROPOSAL: **BUY/SELL/HOLD**" as a free-text directive.
_H1_RATING_RE = re.compile(
    r"^#\s+.+?最终决策：([A-Za-z]+)",
    re.MULTILINE,
)
_FINAL_PROPOSAL_RE = re.compile(
    r"FINAL\s+TRANSACTION\s+PROPOSAL\s*[:：]\s*\*{0,2}([A-Za-z]+)\*{0,2}",
    re.IGNORECASE,
)


def _fallback_rating(md: str) -> str:
    """Best-effort rating recovery for old-format / free-text reports.

    Tries H1 title first (new format always has it there), then the
    ``FINAL TRANSACTION PROPOSAL: X`` directive that survives in both
    old free-text and new structured trader output. Returns "" if nothing
    parseable is found.
    """
    m = _H1_RATING_RE.search(md)
    if m:
        return m.group(1).strip().capitalize()
    m = _FINAL_PROPOSAL_RE.search(md)
    if m:
        return m.group(1).strip().capitalize()
    return ""


def parse_md(path: Path) -> Dict[str, str]:
    """Return a dict of extracted fields. Missing fields → empty string."""
    md = path.read_text(encoding="utf-8", errors="replace")
    out: Dict[str, str] = {}

    pm_body = _slice_section(md, "九")
    for label, attr in _PM_FIELDS.items():
        out[attr] = _extract_field(pm_body, label)

    trader_body = _slice_section(md, "七")
    for label, attr in _TRADER_FIELDS.items():
        out[attr] = _extract_field(trader_body, label)

    rm_body = _research_manager_block(_slice_section(md, "六"))
    for label, attr in _RM_FIELDS.items():
        out[attr] = _extract_field(rm_body, label)

    # Robustness: if structured **Rating**: wasn't present (pre-unified-format
    # report), fall back to the H1 title or the FINAL TRANSACTION PROPOSAL line.
    if not out.get("final_rating"):
        out["final_rating"] = _fallback_rating(md)

    return out


# ---------------------------------------------------------------------------
# Build the row index by (code, trade_date)
# ---------------------------------------------------------------------------


def collect_analysis_rows(analyses_dir: Path) -> Dict[str, AnalysisRow]:
    """Walk analyses/ and return {code: AnalysisRow} using the latest date per code.

    If a ticker has reports for multiple trade dates, the most-recent date
    wins. The Opus and non-Opus reports for that date are merged into one row.
    """
    # Stage 1: group .md files by (code, date)
    grouped: Dict[tuple, dict] = {}  # (code, date) -> {"regular": Path?, "opus": Path?}
    for p in sorted(analyses_dir.glob("*.md")):
        m = FILENAME_RE.match(p.name)
        if not m:
            logger.debug("skip non-conforming filename: %s", p.name)
            continue
        key = (m["code"], m["date"])
        slot = "opus" if m["opus"] else "regular"
        grouped.setdefault(key, {})[slot] = (p, m["name"])

    # Stage 2: for each code, pick the latest date and build the row
    by_code: Dict[str, AnalysisRow] = {}
    latest_date_for_code: Dict[str, str] = {}
    for (code, date), files in grouped.items():
        if latest_date_for_code.get(code, "") > date:
            continue
        latest_date_for_code[code] = date

    for (code, date), files in grouped.items():
        if date != latest_date_for_code[code]:
            continue
        row = by_code.setdefault(code, AnalysisRow(code=code, trade_date=date))
        if "regular" in files:
            p, name = files["regular"]
            row.name = name
            row.md_path = str(p)
            try:
                fields = parse_md(p)
            except Exception as exc:
                logger.warning("parse failed: %s (%s)", p, exc)
                fields = {}
            for k, v in fields.items():
                setattr(row, k, v)
        if "opus" in files:
            p, name = files["opus"]
            if not row.name:
                row.name = name
            row.opus_md_path = str(p)
            try:
                fields = parse_md(p)
            except Exception as exc:
                logger.warning("parse failed: %s (%s)", p, exc)
                fields = {}
            # Stash the Opus values into the *_opus columns
            row.final_rating_opus = fields.get("final_rating", "")
            row.pm_summary_opus = fields.get("pm_summary", "")
            row.pm_thesis_opus = fields.get("pm_thesis", "")
            row.pm_price_target_opus = fields.get("pm_price_target", "")
            row.pm_time_horizon_opus = fields.get("pm_time_horizon", "")
            row.trader_action_opus = fields.get("trader_action", "")
            row.rm_recommendation_opus = fields.get("rm_recommendation", "")
    return by_code


# ---------------------------------------------------------------------------
# CSV join + write
# ---------------------------------------------------------------------------


# Columns we add to the original CSV (in this order)
_ANALYSIS_COLS = [
    "trade_date",
    "final_rating",
    "pm_summary",
    "pm_price_target",
    "pm_time_horizon",
    "trader_action",
    "trader_entry",
    "trader_stop",
    "trader_sizing",
    "rm_recommendation",
    "final_rating_opus",
    "pm_summary_opus",
    "pm_price_target_opus",
    "pm_time_horizon_opus",
    "trader_action_opus",
    "rm_recommendation_opus",
    "md_path",
    "opus_md_path",
]


def merge_and_write(
    input_csv: Path,
    output_csv: Path,
    rows_by_code: Dict[str, AnalysisRow],
) -> tuple[int, int]:
    """Write a new CSV that has every input row plus the analysis columns.

    Returns (rows_written, rows_with_analysis).
    """
    with input_csv.open(encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        in_rows = list(reader)
        in_fieldnames = reader.fieldnames or []

    out_fieldnames = list(in_fieldnames) + _ANALYSIS_COLS
    written = 0
    matched = 0

    with output_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        for row in in_rows:
            ts = (row.get("ts_code") or "").strip()
            code = ts.split(".", 1)[0]
            ar = rows_by_code.get(code)
            if ar is not None:
                matched += 1
                for col in _ANALYSIS_COLS:
                    row[col] = getattr(ar, col, "")
            else:
                for col in _ANALYSIS_COLS:
                    row[col] = ""
            writer.writerow(row)
            written += 1

    return written, matched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--input", default="screen_final_534_drop_C_20260512.csv")
    parser.add_argument(
        "--output", default="screen_final_534_drop_C_20260512_with_analysis.csv"
    )
    parser.add_argument("--analyses-dir", default="analyses")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    input_csv = Path(args.input)
    output_csv = Path(args.output)
    analyses_dir = Path(args.analyses_dir)

    logger.info("scanning %s for *.md ...", analyses_dir)
    rows_by_code = collect_analysis_rows(analyses_dir)
    logger.info("found analysis rows for %d distinct codes", len(rows_by_code))

    written, matched = merge_and_write(input_csv, output_csv, rows_by_code)
    logger.info("wrote %s (%d rows; %d with analysis, %d still empty)",
                output_csv, written, matched, written - matched)

    # Print a small distribution summary to stdout for at-a-glance feedback
    if rows_by_code:
        from collections import Counter
        ratings = Counter(r.final_rating or "(empty)" for r in rows_by_code.values())
        print("\nrating distribution (excluding Opus):")
        for k, v in sorted(ratings.items(), key=lambda kv: -kv[1]):
            print(f"  {k:>16}: {v}")


if __name__ == "__main__":
    main()
