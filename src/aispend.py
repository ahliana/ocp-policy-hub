"""A ledger of what PolicyPulse spends on the Anthropic API.

Written 2026-08-28 after a simple question could not be answered: which of
the five apps sharing an Anthropic credit balance emptied it. FinDigger was
the only one that could answer for itself - $1.66 over five weeks, measured -
and the other four, this one included, kept no record at all. An unmetered
spender is not innocent, it is invisible.

PolicyPulse spreads its calls widest of the four: seven call sites across
the agent, the core LLM client, the auditor, the signals poller and the
English backfill. That is exactly the shape where per-call-site bookkeeping
misses one, so every call goes through acreate and a test reads the source to
prove it.

Note it already had data/ask_usage.json - a per-day request COUNT for one
route, used as a rate limit. It carries no tokens and no cost, so it answers
"how often" and never "how much". This is the other question.

Three deliberate choices.

**It records, it does not enforce.** No cap, nothing that can refuse a scan
or an agent run. Knowing the number is the whole job.

**Its own SQLite file, beside the record rather than inside it.** It lands
in data/, which compose mounts as ./data:/app/data precisely so things there
survive a rebuild - the same reason policypulse.db lives there.

**An unpriced model costs None, never zero.** A model id this table does not
know is recorded with its tokens and a NULL cost so it reads as "spent,
amount unknown" rather than free. A new model silently priced at nothing is
how a bill surprises someone.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Estimated USD per 1,000,000 tokens, (input, output). An ESTIMATE for a
# report, never a bill. Keyed by FAMILY rather than exact model id so a new
# release prices itself instead of recording as unknown.
PRICES = {
    "opus": (15.00, 75.00),
    "sonnet": (3.00, 15.00),
    "haiku": (1.00, 5.00),
}

APP = "policypulse"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    app TEXT NOT NULL,
    week TEXT NOT NULL,
    at TEXT NOT NULL,
    model TEXT NOT NULL,
    family TEXT,
    label TEXT NOT NULL DEFAULT '',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_ai_spend_week ON ai_spend (week);
"""


def ledger_path() -> Path:
    """Where the ledger lives. POLICYPULSE_AISPEND_DB overrides it, so a test never
    writes to the real one. The default sits in the bind-mounted data
    directory (./data:/app/data), which is what makes it survive a rebuild."""
    env = os.environ.get("POLICYPULSE_AISPEND_DB")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "aispend.db"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(path) if path else ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def week_key(ts: str | None = None) -> str:
    """ISO year-week, matching FinDigger's so all five ledgers read alike."""
    dt = (datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
          if ts else datetime.now(timezone.utc))
    year, week, _ = dt.isocalendar()
    return f"{year}-W{week:02d}"


def family(model: str | None) -> str | None:
    """The pricing family a model id belongs to, or None when it names none."""
    low = (model or "").lower()
    for name in PRICES:
        if name in low:
            return name
    return None


def estimate(model: str | None, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated USD, or None when the model's family is unknown."""
    fam = family(model)
    if fam is None:
        return None
    per_in, per_out = PRICES[fam]
    return (input_tokens or 0) * per_in / 1e6 + (output_tokens or 0) * per_out / 1e6


def usage_of(response) -> tuple[int, int]:
    """(input_tokens, output_tokens) from an Anthropic response, SDK object or
    raw dict. Missing counts read as zero: the call happened and reported
    nothing, which is still a row."""
    if response is None:
        return (0, 0)
    usage = response.get("usage") if isinstance(response, dict) else getattr(
        response, "usage", None)
    if usage is None:
        return (0, 0)
    if isinstance(usage, dict):
        return (int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0))
    return (int(getattr(usage, "input_tokens", 0) or 0),
            int(getattr(usage, "output_tokens", 0) or 0))


def record(model: str, input_tokens: int, output_tokens: int, label: str = "",
           conn: sqlite3.Connection | None = None, at: str | None = None) -> dict:
    """Write one call to the ledger and return what was written.

    Never raises. The weekly signals run and the monthly scan are unattended,
    and a bookkeeping failure must not cost the work they just did."""
    row = {
        "app": APP, "week": week_key(at), "at": at or _now(), "model": model,
        "family": family(model), "label": label,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cost_usd": estimate(model, input_tokens, output_tokens),
        "recorded": False,
    }
    own = conn is None
    try:
        conn = conn or connect()
        conn.execute(
            "INSERT INTO ai_spend (app, week, at, model, family, label, "
            "input_tokens, output_tokens, cost_usd) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["app"], row["week"], row["at"], row["model"], row["family"],
             row["label"], row["input_tokens"], row["output_tokens"], row["cost_usd"]))
        conn.commit()
        row["recorded"] = True
        if own:
            conn.close()
    except sqlite3.Error:
        row["recorded"] = False
    return row


async def acreate(client, label: str = "", **kwargs):
    """Call messages.create and record what it cost.

    Async only, because every one of this app's seven call sites is async. A
    sync twin with no caller would be unwired code the gate rightly refuses.

    Wrapping the CALL rather than the client is what makes the guard
    checkable: a client can be built anywhere and handed around, but
    `.messages.create(` is a string a test can look for. Returns the response
    untouched, so a caller cannot tell it was measured."""
    response = await client.messages.create(**kwargs)
    used_in, used_out = usage_of(response)
    record(kwargs.get("model", ""), used_in, used_out, label=label)
    return response


def totals(conn: sqlite3.Connection | None = None) -> dict:
    """Everything the ledger knows. `unpriced` is counted separately rather
    than folded into the total, because "$2.10 plus four calls we cannot
    price" is the honest sentence and "$2.10" alone is not."""
    own = conn is None
    conn = conn or connect()
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(input_tokens), 0), "
        "COALESCE(SUM(output_tokens), 0), COALESCE(SUM(cost_usd), 0), "
        "MIN(at), MAX(at) FROM ai_spend").fetchone()
    unpriced = conn.execute(
        "SELECT COUNT(*) FROM ai_spend WHERE cost_usd IS NULL").fetchone()[0]
    by_week = [{"week": w, "calls": n, "cost_usd": c} for w, n, c in conn.execute(
        "SELECT week, COUNT(*), COALESCE(SUM(cost_usd), 0) FROM ai_spend "
        "GROUP BY week ORDER BY week")]
    by_label = [{"label": lb, "calls": n, "cost_usd": c} for lb, n, c in conn.execute(
        "SELECT label, COUNT(*), COALESCE(SUM(cost_usd), 0) FROM ai_spend "
        "GROUP BY label ORDER BY COALESCE(SUM(cost_usd), 0) DESC")]
    by_model = [{"model": m, "calls": n, "cost_usd": c} for m, n, c in conn.execute(
        "SELECT model, COUNT(*), COALESCE(SUM(cost_usd), 0) FROM ai_spend "
        "GROUP BY model ORDER BY COALESCE(SUM(cost_usd), 0) DESC")]
    if own:
        conn.close()
    return {"app": APP, "calls": row[0], "input_tokens": row[1],
            "output_tokens": row[2], "cost_usd": row[3], "unpriced": unpriced,
            "first": row[4], "last": row[5], "by_week": by_week,
            "by_label": by_label, "by_model": by_model}


def report(totals_: dict | None = None) -> str:
    """The ledger as a person reads it. `python -m src.aispend`.

    By model AND by caller: the agent, the auditor and the signals poller do
    not run on the same model or the same schedule, so "which model" and
    "which feature" are different questions and both matter."""
    t = totals_ if totals_ is not None else totals()
    if not t["calls"]:
        return f"{t['app']}: no Anthropic calls recorded yet"
    lines = [
        f"{t['app']}: {t['calls']} calls, "
        f"{t['input_tokens']:,} in + {t['output_tokens']:,} out tokens, "
        f"estimated ${t['cost_usd']:.4f}",
        f"  first {t['first']}  last {t['last']}",
    ]
    if t["unpriced"]:
        lines.append(f"  plus {t['unpriced']} call(s) on an unpriced model, "
                     "not included in the total")
    lines.append("  by week:")
    lines += [f"    {w['week']}  {w['calls']:4} calls  ${w['cost_usd']:.4f}"
              for w in t["by_week"]]
    lines.append("  by model:")
    lines += [f"    {m['model']:32} {m['calls']:4} calls  ${m['cost_usd']:.4f}"
              for m in t["by_model"]]
    lines.append("  by caller:")
    lines += [f"    {(lb['label'] or '(unlabelled)'):24} {lb['calls']:4} calls  "
              f"${lb['cost_usd']:.4f}" for lb in t["by_label"]]
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":  # pragma: no cover - the entry point itself
    print(report())
