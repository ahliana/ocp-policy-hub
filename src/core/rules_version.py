"""A fingerprint of everything that decides whether a page is relevant.

The URL cache remembers a verdict against a URL, a content hash and an
expiry. It does not remember which rules produced the verdict, so a page
judged irrelevant under one keyword file stays judged irrelevant after that
file changes. Tuning work then measures a mixture of old and new rules and
the result reads as noise.

This module produces a short hash over the inputs that can change a
verdict: the keyword configuration, the analysis settings (thresholds and
the data-centre scope rule), and the two prompts. The cache stores it
alongside each entry and treats a mismatch as a miss.

Two things are deliberately excluded. Comments and blank lines in the
keyword file are stripped before hashing, so documenting a keyword does not
throw away a month of cached analysis. And only the analysis section of the
settings file is read: crawl delays, output destinations and logging cannot
change what a page is, and including them would expire the cache for
edits that decide nothing.

Note the existing seven-day expiry on negative verdicts, which already
limited how long a wrong rejection could persist. This closes the same hole
for the thirty-day positive entries and makes the effect immediate rather
than eventual.
"""

import hashlib
import json
import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

#: Twelve hex characters is about 48 bits: far more than enough to tell a
#: handful of rule revisions apart, and short enough to read in a log line.
FINGERPRINT_CHARS = 12

DEFAULT_CONFIG_DIR = Path("config")


def _significant_lines(text: str) -> str:
    """Configuration lines that can change a decision.

    Comments and blank lines cannot, and stripping them means a session can
    explain a keyword without invalidating everyone's cached analysis.
    """
    lines = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if line.strip():
            lines.append(line)
    return "\n".join(lines)


def _read(path: Path) -> str:
    """File text, or an empty string with a warning.

    A missing config file is the config loader's problem to report. Here it
    must not crash a scan: an empty part makes every entry stale, which is
    the safe direction to fail in.
    """
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Rules fingerprint could not read %s: %s", path, e)
        return ""


def default_parts(config_dir: Path | None = None) -> list[str]:
    """The live rule inputs, in a fixed order."""
    root = config_dir or DEFAULT_CONFIG_DIR

    keywords = _significant_lines(_read(root / "keywords.yaml"))

    # Only the analysis section: thresholds and the scope rule decide
    # verdicts, crawl delays and output destinations do not.
    try:
        settings = yaml.safe_load(_read(root / "settings.yaml")) or {}
        analysis = settings.get("analysis") or {}
    except yaml.YAMLError as e:
        logger.warning("Rules fingerprint could not parse settings.yaml: %s", e)
        analysis = {}
    analysis_part = json.dumps(analysis, sort_keys=True, default=str)

    from .llm import ANALYSIS_PROMPT, SCREENING_PROMPT

    return [keywords, analysis_part, SCREENING_PROMPT, ANALYSIS_PROMPT]


def rules_fingerprint(parts: list[str] | None = None) -> str:
    """Short hash of the rules that decide relevance.

    Args:
        parts: Override the inputs, for tests. Production passes nothing,
            and the current config and prompts are read.
    """
    if parts is None:
        parts = default_parts()
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        # A separator, so ["ab", "c"] and ["a", "bc"] hash differently.
        digest.update(b"\x00")
    return digest.hexdigest()[:FINGERPRINT_CHARS]
