"""Trigger the Opus decision-chain re-run for high-conviction analyst calls.

After the regular pipeline finishes and the markdown report is saved, this
helper checks whether the Portfolio Manager's final rating is Buy or
Overweight; if so, it fires `bin/spawn_opus_analysis.sh <md_path>` to
relaunch the decision chain (Bull/Bear → Research Manager → Trader → Risk
debate → PM) with Claude Opus and write a sibling `<basename>_Opus.md`.

The spawn defaults to asynchronous mode — the bash script daemonizes the
claude --print invocation and returns immediately, so the calling runner
isn't blocked by Opus's runtime (it can be many minutes per ticker).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Default trigger set. Hold / Underweight / Sell are skipped because the
# Opus re-run costs noticeably more than the regular flash chain and only
# adds value on the high-conviction long calls.
DEFAULT_TRIGGER_RATINGS: frozenset[str] = frozenset({"Buy", "Overweight"})


def _repo_root() -> Path:
    """Return the repo root by walking up from this file's location."""
    return Path(__file__).resolve().parents[2]


def maybe_spawn_opus(
    md_path: Path | str,
    decision_rating: str,
    *,
    trigger_ratings: Iterable[str] = DEFAULT_TRIGGER_RATINGS,
    wait: bool = False,
) -> Optional[subprocess.CompletedProcess]:
    """If ``decision_rating`` triggers an Opus re-run, fire the spawn script.

    Parameters
    ----------
    md_path:
        Path to the regular markdown report just written by
        ``save_analysis_markdown``.
    decision_rating:
        The 5-tier rating extracted from the Portfolio Manager's final
        decision (Buy / Overweight / Hold / Underweight / Sell).
    trigger_ratings:
        Ratings that should trigger an Opus re-run. Defaults to
        ``{"Buy", "Overweight"}``.
    wait:
        If True, run synchronously and surface stdout/stderr.  Default is
        async — the script daemonizes claude and returns immediately.

    Returns
    -------
    subprocess.CompletedProcess | None
        ``None`` if the rating did not trigger; the completed process
        otherwise.  Async invocations return as soon as the script
        daemonizes the child, so the returncode reflects the bash dispatch
        step, not the Opus run itself.
    """
    rating_clean = (decision_rating or "").strip().title()
    if rating_clean not in trigger_ratings:
        logger.debug(
            "Opus spawn skipped: rating %r not in %r", rating_clean, sorted(trigger_ratings)
        )
        return None

    script = _repo_root() / "bin" / "spawn_opus_analysis.sh"
    if not script.exists():
        logger.warning("Opus spawn script missing at %s; skipping", script)
        return None

    md_path_str = str(Path(md_path).resolve())
    cmd = ["bash", str(script)]
    if wait:
        cmd.append("--wait")
    cmd.append(md_path_str)

    logger.info("Spawning Opus decision chain for %s (rating=%s)", md_path_str, rating_clean)
    try:
        # capture_output keeps the dispatch log out of the runner's stdout
        # in async mode (where the script prints a one-line summary and
        # exits); in --wait mode the long claude stream is teed by the
        # script itself.
        result = subprocess.run(
            cmd,
            capture_output=not wait,
            text=True,
            check=False,
        )
        if not wait:
            # exit code 2 = "_Opus.md already exists, skipped" (idempotent re-runs)
            if result.returncode in (0, 2) and result.stdout:
                # surface the short status line so the user sees what got dispatched
                for line in result.stdout.strip().splitlines():
                    logger.info("  %s", line)
            elif result.returncode not in (0, 2):
                logger.warning(
                    "Opus spawn script exited with %d: %s",
                    result.returncode, (result.stderr or result.stdout or "").strip(),
                )
        return result
    except Exception as exc:
        logger.warning("Failed to spawn Opus chain for %s: %s", md_path_str, exc)
        return None
