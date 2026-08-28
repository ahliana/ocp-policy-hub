"""What PolicyPulse spends on the Anthropic API, and the guard that keeps the
meter honest.

Written 2026-08-28 after a simple question could not be answered: which of
the five apps sharing an Anthropic credit balance emptied it. FinDigger was
the only one that could answer for itself - $1.66 over five weeks - and the
other four kept no record at all. An unmetered spender is not innocent, it is
invisible.

PolicyPulse spreads its calls widest of the four: seven sites across the
agent, the core LLM client, the auditor, the signals poller and the English
backfill. That is exactly the shape where per-call-site bookkeeping misses
one, which is why the guard below reads the source rather than trusting a
list.

It already had data/ask_usage.json, a per-day request COUNT for one route
used as a rate limit. It carries no tokens and no cost, so it answers "how
often" and never "how much". This answers the other question.
"""

from __future__ import annotations

import asyncio
import pathlib
import sqlite3

import pytest

from src import aispend

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("POLICYPULSE_AISPEND_DB", str(tmp_path / "aispend.db"))
    return tmp_path / "aispend.db"


class FakeUsage:
    def __init__(self, i, o):
        self.input_tokens, self.output_tokens = i, o


class FakeResponse:
    def __init__(self, i=100, o=50):
        self.usage = FakeUsage(i, o)


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class FakeClient:
    def __init__(self, response=None):
        self.messages = FakeMessages(response or FakeResponse())


# --------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------

@pytest.mark.small
def test_the_models_this_app_actually_uses_are_priced():
    """From the source 2026-08-28: claude-sonnet-4-6 and
    claude-haiku-4-5-20251001."""
    assert aispend.family("claude-sonnet-4-6") == "sonnet"
    assert aispend.family("claude-haiku-4-5-20251001") == "haiku"


@pytest.mark.small
def test_the_tiers_are_priced_in_the_right_order():
    assert aispend.PRICES["opus"][0] > aispend.PRICES["sonnet"][0]
    assert aispend.PRICES["sonnet"][0] > aispend.PRICES["haiku"][0]


@pytest.mark.small
def test_an_unrecognised_model_has_no_family_and_no_estimate():
    assert aispend.family("gpt-4") is None
    assert aispend.family(None) is None
    assert aispend.estimate("gpt-4", 1000, 1000) is None


@pytest.mark.small
def test_the_estimate_is_input_plus_output_at_the_family_rate():
    assert aispend.estimate("claude-sonnet-4-6", 1_000_000, 1_000_000) == \
        pytest.approx(18.0)


# --------------------------------------------------------------------------
# Usage
# --------------------------------------------------------------------------

@pytest.mark.small
def test_usage_is_read_off_an_sdk_response():
    assert aispend.usage_of(FakeResponse(1200, 340)) == (1200, 340)


@pytest.mark.small
def test_usage_is_also_read_off_a_raw_dict():
    assert aispend.usage_of({"usage": {"input_tokens": 7, "output_tokens": 9}}) == (7, 9)


@pytest.mark.small
def test_a_response_with_no_usage_reads_as_zero_tokens():
    """The call happened and reported nothing, which is different from no
    call at all - so it is still a row."""
    assert aispend.usage_of(object()) == (0, 0)
    assert aispend.usage_of(None) == (0, 0)


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------

@pytest.mark.medium
def test_a_call_is_written_with_its_cost(ledger):
    got = aispend.record("claude-sonnet-4-6", 1000, 500, label="agent:ask")
    assert got["recorded"] is True
    assert got["cost_usd"] == pytest.approx(1000 * 3 / 1e6 + 500 * 15 / 1e6)
    assert aispend.totals()["calls"] == 1


@pytest.mark.medium
def test_an_unknown_model_records_tokens_and_no_cost(ledger):
    """"Spent, amount unknown" rather than free. A NULL is visible in the
    report; a zero disappears into it."""
    got = aispend.record("some-new-model", 5000, 2000)
    assert got["cost_usd"] is None
    totals = aispend.totals()
    assert totals["unpriced"] == 1 and totals["input_tokens"] == 5000


@pytest.mark.medium
def test_unpriced_calls_stay_out_of_the_total(ledger):
    aispend.record("claude-sonnet-4-6", 1000, 500)
    aispend.record("some-new-model", 9_000_000, 9_000_000)
    totals = aispend.totals()
    assert totals["unpriced"] == 1
    assert totals["cost_usd"] == pytest.approx(1000 * 3 / 1e6 + 500 * 15 / 1e6)


@pytest.mark.small
def test_recording_never_raises_when_the_ledger_cannot_be_written(monkeypatch):
    """The weekly signals run and the monthly scan are unattended. A
    bookkeeping failure must not cost the work they just did."""
    def boom(*a, **kw):
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(aispend, "connect", boom)
    got = aispend.record("claude-sonnet-4-6", 10, 10)
    assert got["recorded"] is False
    assert got["cost_usd"] is not None, "the estimate stands even unwritten"


@pytest.mark.medium
def test_the_week_key_matches_the_other_ledgers(ledger):
    """All five bucket weeks the same way, so one reader can cover them."""
    assert aispend.week_key("2026-08-28T06:37:24Z") == "2026-W35"


# --------------------------------------------------------------------------
# The wrapper
# --------------------------------------------------------------------------

@pytest.mark.medium
def test_the_wrapper_calls_through_and_returns_the_response_untouched(ledger):
    response = FakeResponse(80, 20)
    client = FakeClient(response)
    got = asyncio.run(aispend.acreate(client, label="agent:ask",
                                      model="claude-sonnet-4-6", max_tokens=10))
    assert got is response, "a caller must not be able to tell it was measured"
    assert "label" not in client.messages.calls[0], (
        "the label is ours and must not reach the API")


@pytest.mark.medium
def test_the_wrapper_records_what_the_call_cost(ledger):
    """Async only, because every one of this app's seven call sites is async.
    A sync twin with no caller would be unwired code the gate rightly
    refuses."""
    asyncio.run(aispend.acreate(FakeClient(FakeResponse(1000, 500)),
                                label="auditor", model="claude-sonnet-4-6"))
    totals = aispend.totals()
    assert totals["calls"] == 1
    assert totals["by_label"][0]["label"] == "auditor"


@pytest.mark.medium
def test_a_failing_call_records_nothing_and_still_raises(ledger):
    """An outage is the caller's business. The meter must not swallow it, and
    must not record a call that never happened."""
    client = FakeClient()

    async def boom(**kwargs):
        raise RuntimeError("overloaded")

    client.messages.create = boom
    with pytest.raises(RuntimeError):
        asyncio.run(aispend.acreate(client, label="agent:ask",
                                    model="claude-sonnet-4-6"))
    assert aispend.totals()["calls"] == 0


# --------------------------------------------------------------------------
# Reading it back
# --------------------------------------------------------------------------

@pytest.mark.medium
def test_an_empty_ledger_says_so_rather_than_printing_a_table(ledger):
    assert aispend.report() == "policypulse: no Anthropic calls recorded yet"


@pytest.mark.medium
def test_the_report_breaks_down_by_model_and_by_caller(ledger):
    """The agent, the auditor and the signals poller do not run on the same
    model or the same schedule, so both questions matter."""
    aispend.record("claude-sonnet-4-6", 5000, 2000, label="agent:ask")
    aispend.record("claude-haiku-4-5-20251001", 100, 50, label="signals:news")
    out = aispend.report()
    assert "by model:" in out and "by caller:" in out
    assert out.index("agent:ask") < out.index("signals:news"), (
        "the biggest spender should lead")


@pytest.mark.medium
def test_unpriced_calls_get_their_own_line_in_the_report(ledger):
    aispend.record("claude-sonnet-4-6", 1000, 500, label="agent:ask")
    aispend.record("some-new-model", 1000, 500, label="agent:ask")
    out = aispend.report()
    assert "1 call(s) on an unpriced model" in out
    assert "not included in the total" in out


# --------------------------------------------------------------------------
# The guard
# --------------------------------------------------------------------------

def _app_sources():
    out = []
    for p in (ROOT / "src").rglob("*.py"):
        if "__pycache__" in p.as_posix() or p.name == "aispend.py":
            continue
        out.append(p)
    return out


@pytest.mark.small
def test_every_anthropic_call_goes_through_the_meter():
    """The guard this whole exercise exists for.

    Seven call sites across six modules is exactly the shape where
    per-call-site bookkeeping misses one, and a report that still looks whole
    while missing calls is worse than no report. Wrapping the CALL rather than
    the client is what makes this checkable: a client can be built anywhere
    and handed around, but `.messages.create(` is a string a test can look
    for."""
    offenders = [p.relative_to(ROOT).as_posix() for p in _app_sources()
                 if ".messages.create(" in p.read_text(encoding="utf-8", errors="replace")]
    assert offenders == [], (
        f"these modules call the Anthropic API without recording it: "
        f"{offenders}. Use aispend.acreate(client, label=..., ...), or this "
        f"app's spend stops adding up.")


@pytest.mark.small
def test_every_known_caller_is_wired_and_labelled():
    """Each site gets its own label. "Which feature is spending" is the
    question this ledger exists to answer, and a shared blank label answers
    nothing."""
    expected = {
        "src/agent/ask.py": 'label="agent:ask"',
        "src/agent/orchestrator.py": 'label="agent:orchestrator"',
        "src/core/llm.py": 'label="core:llm"',
        "src/orchestration/auditor.py": 'label="auditor"',
        "src/output/backfill_english.py": 'label="backfill_english"',
        "src/signals/news.py": 'label="signals:news"',
    }
    for path, label in expected.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        assert label in text, f"{path} is not labelled"
        assert "aispend.acreate(" in text, f"{path} does not use the wrapper"


@pytest.mark.small
def test_the_ledger_sits_in_the_mounted_data_directory():
    """./data:/app/data in docker-compose.yml, the same reason
    policypulse.db lives there: things in data/ survive a rebuild."""
    assert aispend.ledger_path().name == "aispend.db"
    assert aispend.ledger_path().parent.name == "data"
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "./data:/app/data" in compose


@pytest.mark.small
def test_the_existing_usage_file_is_a_count_not_a_cost():
    """data/ask_usage.json predates this and is a per-day request count used
    as a rate limit. Assuming it already answered "how much" would have been
    the easy wrong turn here."""
    text = (ROOT / "src" / "api" / "routes" / "ask.py").read_text(encoding="utf-8")
    assert "ask_usage.json" in text
    assert "input_tokens" not in text, (
        "if this route starts counting tokens, reconcile it with aispend "
        "rather than keeping two answers to one question")
