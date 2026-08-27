"""Tests for src/output/backfill_english.py (WP-35 English titles backfill).

No test in this file makes a real network call. The dry-run path never
constructs an Anthropic client at all (proven below by patching the
constructor to raise); the real-run tests patch anthropic.AsyncAnthropic
with an in-memory fake that records calls instead.
"""

import json
from unittest.mock import MagicMock

import anthropic
import pytest

from src.core.models import DEFAULT_SCREENING_MODEL, Policy, PolicyType
from src.core.pricing import PricingLoader
from src.output.backfill_english import (
    ESTIMATED_INPUT_TOKENS_PER_POLICY,
    ESTIMATED_OUTPUT_TOKENS_PER_POLICY,
    BackfillSummary,
    dry_run_report,
    estimate_cost_usd,
    main,
    run_backfill,
    select_candidates,
)
from src.storage.store import PolicyStore


def _policy(url, name, source_language="German", name_en=None, **overrides):
    defaults = dict(
        url=url,
        policy_name=name,
        policy_name_en=name_en,
        jurisdiction="Germany",
        policy_type=PolicyType.LAW,
        summary="s",
        relevance_score=7,
        source_language=source_language,
    )
    defaults.update(overrides)
    return Policy(**defaults)


def _seed(tmp_path, policies):
    store = PolicyStore(data_dir=str(tmp_path))
    store.add_policies(policies)
    return store


def _translation_json(text_en):
    return json.dumps({"policy_name_en": text_en})


class _FakeMessages:
    """Stands in for client.messages - responses consumed in call order."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        response = MagicMock()
        response.content = [MagicMock(text=item)]
        return response


class _FakeAsyncAnthropic:
    """Stands in for anthropic.AsyncAnthropic - never touches the network."""

    #: set by _install_fake_anthropic before each construction
    next_responses: list = []
    instances: list = []

    def __init__(self, api_key=None):
        self.api_key = api_key
        self.messages = _FakeMessages(_FakeAsyncAnthropic.next_responses)
        _FakeAsyncAnthropic.instances.append(self)

    async def close(self):
        pass


def _install_fake_anthropic(monkeypatch, responses):
    _FakeAsyncAnthropic.instances = []
    _FakeAsyncAnthropic.next_responses = responses
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _FakeAsyncAnthropic)


def _raising_anthropic(monkeypatch):
    """Anthropic client construction fails loudly - proves a code path
    never even tries to build one."""
    class _Boom:
        def __init__(self, *a, **k):
            raise AssertionError("anthropic client must not be constructed")

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Boom)
    monkeypatch.setattr(anthropic, "Anthropic", _Boom)


@pytest.mark.medium
class TestSelectCandidates:
    def test_selects_only_missing_or_blank(self, tmp_path):
        store = _seed(tmp_path, [
            _policy("https://a.gov/1", "A", name_en=None),
            _policy("https://a.gov/2", "B", name_en=""),
            _policy("https://a.gov/3", "C", name_en="Already Done"),
        ])
        urls = {p["url"] for p in select_candidates(store)}
        assert urls == {"https://a.gov/1", "https://a.gov/2"}

    def test_limit_caps_results(self, tmp_path):
        store = _seed(tmp_path, [_policy(f"https://a.gov/{i}", f"P{i}") for i in range(5)])
        assert len(select_candidates(store, limit=2)) == 2

    def test_no_limit_returns_everything_missing(self, tmp_path):
        store = _seed(tmp_path, [_policy(f"https://a.gov/{i}", f"P{i}") for i in range(5)])
        assert len(select_candidates(store)) == 5

    def test_no_candidates_when_all_translated(self, tmp_path):
        store = _seed(tmp_path, [_policy("https://a.gov/1", "A", name_en="Done")])
        assert select_candidates(store) == []


class TestCostEstimate:
    @pytest.mark.small
    def test_zero_candidates_costs_nothing(self):
        pricing = PricingLoader()
        assert estimate_cost_usd(0, pricing, DEFAULT_SCREENING_MODEL) == 0.0

    @pytest.mark.small
    def test_matches_pricing_table_math(self):
        """Cost must come from the pricing table, not a baked-in constant."""
        pricing = PricingLoader()
        price = pricing.pricing_for(DEFAULT_SCREENING_MODEL)
        count = 10
        expected = round(
            price.cost_usd(
                count * ESTIMATED_INPUT_TOKENS_PER_POLICY,
                count * ESTIMATED_OUTPUT_TOKENS_PER_POLICY,
            ),
            4,
        )
        assert expected > 0
        assert estimate_cost_usd(count, pricing, DEFAULT_SCREENING_MODEL) == expected

    @pytest.mark.medium
    def test_reacts_to_monkeypatched_pricing_table(self, tmp_path):
        (tmp_path / "pricing.yaml").write_text(
            f"models:\n  {DEFAULT_SCREENING_MODEL}:\n"
            "    input_per_mtok: 1000.0\n    output_per_mtok: 1000.0\n"
            "estimator: {}\n",
            encoding="utf-8",
        )
        pricing = PricingLoader(config_dir=str(tmp_path))
        cost = estimate_cost_usd(1, pricing, DEFAULT_SCREENING_MODEL)
        expected = round(
            (ESTIMATED_INPUT_TOKENS_PER_POLICY * 1000.0
             + ESTIMATED_OUTPUT_TOKENS_PER_POLICY * 1000.0) / 1_000_000,
            4,
        )
        assert cost == expected


@pytest.mark.medium
class TestDryRun:
    def test_makes_zero_api_calls(self, tmp_path, monkeypatch):
        _raising_anthropic(monkeypatch)
        store = _seed(tmp_path, [_policy("https://a.gov/1", "A")])
        summary = dry_run_report(store, config_dir="config")  # must not raise
        assert summary.candidates == 1

    def test_prints_would_translate_with_language_and_excludes_translated(
        self, tmp_path, monkeypatch, capsys,
    ):
        _raising_anthropic(monkeypatch)
        store = _seed(tmp_path, [
            _policy("https://a.gov/1", "Energiewendegesetz", source_language="German"),
            _policy("https://a.gov/2", "Already Done", name_en="Already Done"),
        ])
        dry_run_report(store, config_dir="config")
        out = capsys.readouterr().out
        assert "would translate: Energiewendegesetz (German)" in out
        assert out.count("would translate:") == 1

    def test_prints_total_and_estimated_cost(self, tmp_path, monkeypatch, capsys):
        _raising_anthropic(monkeypatch)
        store = _seed(tmp_path, [
            _policy("https://a.gov/1", "A"), _policy("https://a.gov/2", "B"),
        ])
        dry_run_report(store, config_dir="config")
        out = capsys.readouterr().out
        assert "Total: 2 policies to translate" in out
        assert "Estimated cost: $" in out

    def test_singular_wording_for_one_candidate(self, tmp_path, monkeypatch, capsys):
        _raising_anthropic(monkeypatch)
        store = _seed(tmp_path, [_policy("https://a.gov/1", "A")])
        dry_run_report(store, config_dir="config")
        assert "Total: 1 policy to translate" in capsys.readouterr().out

    def test_exits_zero_with_no_candidates(self, tmp_path, monkeypatch):
        _raising_anthropic(monkeypatch)
        store = _seed(tmp_path, [_policy("https://a.gov/1", "A", name_en="Done")])
        summary = dry_run_report(store, config_dir="config")
        assert summary.candidates == 0


@pytest.mark.medium
class TestRunBackfill:
    @pytest.mark.asyncio
    async def test_translates_each_candidate(self, tmp_path, monkeypatch):
        _install_fake_anthropic(monkeypatch, [
            _translation_json("Energy Transition Act"),
            _translation_json("Heat Networks Order"),
        ])
        store = _seed(tmp_path, [
            _policy("https://a.gov/1", "Energiewendegesetz"),
            _policy("https://a.gov/2", "Fernwaermeverordnung"),
        ])
        summary = await run_backfill(store, api_key="sk-ant-test", config_dir="config")

        assert summary.translated == 2
        assert summary.failed == 0
        assert summary.skipped_already_had == 0
        by_url = {p["url"]: p["policy_name_en"] for p in store.get_all()}
        assert by_url["https://a.gov/1"] == "Energy Transition Act"
        assert by_url["https://a.gov/2"] == "Heat Networks Order"

    @pytest.mark.asyncio
    async def test_per_policy_failure_is_isolated(self, tmp_path, monkeypatch):
        """One bad response must not abort the rest of the batch."""
        _install_fake_anthropic(monkeypatch, [
            _translation_json("Energy Transition Act"),
            # A realistic class - the batch catch is narrowed (SDK errors,
            # parse errors, timeouts), deliberately not blind Exception.
            anthropic.AnthropicError("model overloaded"),
            _translation_json("Heat Networks Order"),
        ])
        store = _seed(tmp_path, [
            _policy("https://a.gov/1", "Energiewendegesetz"),
            _policy("https://a.gov/2", "Broken One"),
            _policy("https://a.gov/3", "Fernwaermeverordnung"),
        ])
        summary = await run_backfill(store, api_key="sk-ant-test", config_dir="config")

        assert summary.translated == 2
        assert summary.failed == 1
        assert summary.failed_urls == ["https://a.gov/2"]
        by_url = {p["url"]: p["policy_name_en"] for p in store.get_all()}
        assert by_url["https://a.gov/1"] == "Energy Transition Act"
        assert by_url["https://a.gov/2"] is None
        assert by_url["https://a.gov/3"] == "Heat Networks Order"

    @pytest.mark.asyncio
    async def test_unparseable_response_counts_as_failure_not_a_crash(self, tmp_path, monkeypatch):
        _install_fake_anthropic(monkeypatch, ["not json at all"])
        store = _seed(tmp_path, [_policy("https://a.gov/1", "A")])
        summary = await run_backfill(store, api_key="sk-ant-test", config_dir="config")
        assert summary.failed == 1
        assert summary.translated == 0

    @pytest.mark.asyncio
    async def test_idempotent_second_run_is_a_no_op(self, tmp_path, monkeypatch):
        """A second full pass over an already-translated store changes
        nothing and needs no new API responses queued."""
        store = _seed(tmp_path, [_policy("https://a.gov/1", "Energiewendegesetz")])

        _install_fake_anthropic(monkeypatch, [_translation_json("Energy Transition Act")])
        first = await run_backfill(store, api_key="sk-ant-test", config_dir="config")
        assert first.translated == 1

        # No responses queued this time - if run_backfill tried to call the
        # API again it would raise IndexError (list.pop on an empty list).
        _install_fake_anthropic(monkeypatch, [])
        second = await run_backfill(store, api_key="sk-ant-test", config_dir="config")

        assert second.candidates == 0
        assert second.translated == 0
        assert second.skipped_already_had == 0
        assert store.get_all()[0]["policy_name_en"] == "Energy Transition Act"

    @pytest.mark.asyncio
    async def test_limit_caps_how_many_are_translated(self, tmp_path, monkeypatch):
        _install_fake_anthropic(monkeypatch, [_translation_json("Only One")])
        store = _seed(tmp_path, [
            _policy("https://a.gov/1", "A"), _policy("https://a.gov/2", "B"),
        ])
        summary = await run_backfill(
            store, api_key="sk-ant-test", config_dir="config", limit=1,
        )
        assert summary.translated == 1
        translated_count = sum(
            1 for p in store.get_all() if p["policy_name_en"]
        )
        assert translated_count == 1


@pytest.mark.medium
class TestMainCLI:
    """CLI argument wiring - dry_run_report/run_backfill are stubbed here,
    each already covered directly by the classes above."""

    def test_dry_run_never_needs_api_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(
            "src.output.backfill_english.dry_run_report",
            lambda store, config_dir, limit: BackfillSummary(candidates=3),
        )
        code = main(["--dry-run", "--data-dir", str(tmp_path)])
        assert code == 0

    def test_missing_api_key_returns_one_and_never_runs_backfill(
        self, monkeypatch, tmp_path, capsys,
    ):
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        called = {"ran": False}

        async def fake_run_backfill(*a, **k):
            called["ran"] = True
            return BackfillSummary()

        monkeypatch.setattr("src.output.backfill_english.run_backfill", fake_run_backfill)

        code = main(["--data-dir", str(tmp_path)])

        assert code == 1
        assert called["ran"] is False
        assert "ANTHROPIC_API_KEY" in capsys.readouterr().out

    def test_real_run_prints_summary(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        fake_summary = BackfillSummary(
            translated=2, skipped_already_had=1, failed=1,
            failed_urls=["https://broken.gov/x"],
        )

        async def fake_run_backfill(*a, **k):
            return fake_summary

        monkeypatch.setattr("src.output.backfill_english.run_backfill", fake_run_backfill)

        code = main(["--data-dir", str(tmp_path)])
        out = capsys.readouterr().out

        assert code == 0
        assert "Translated: 2" in out
        assert "Skipped (already had policy_name_en): 1" in out
        assert "Failed: 1" in out
        assert "https://broken.gov/x" in out

    def test_limit_flag_is_passed_through(self, monkeypatch, tmp_path):
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        captured = {}

        def fake_dry_run_report(store, config_dir, limit):
            captured["limit"] = limit
            return BackfillSummary()

        monkeypatch.setattr(
            "src.output.backfill_english.dry_run_report", fake_dry_run_report,
        )
        main(["--dry-run", "--data-dir", str(tmp_path), "--limit", "5"])
        assert captured["limit"] == 5
