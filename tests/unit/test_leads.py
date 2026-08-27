"""Tests for the lead queue store."""

import pytest

from src.storage.leads import Lead, LeadStore


@pytest.fixture
def store(tmp_path):
    return LeadStore(data_dir=str(tmp_path))


def _lead(url="https://news.example/article", title="Denmark heat mandate"):
    return Lead(title=title, source_url=url)


class TestLeadStore:
    def test_add_and_list(self, store):
        added = store.add_leads([_lead()])
        assert added == 1
        leads = store.list()
        assert len(leads) == 1
        assert leads[0].status == "new"

    def test_dedupes_by_source_url(self, store):
        store.add_leads([_lead()])
        added = store.add_leads([_lead(title="Same URL again")])
        assert added == 0
        assert len(store.list()) == 1

    def test_persistence_roundtrip(self, tmp_path):
        store = LeadStore(data_dir=str(tmp_path))
        store.add_leads([_lead()])
        reloaded = LeadStore(data_dir=str(tmp_path))
        assert len(reloaded.list()) == 1

    def test_update_status_and_filter(self, store):
        lead = _lead()
        store.add_leads([lead])
        store.update_status(lead.lead_id, "dismissed")
        assert store.list(status="new") == []
        assert len(store.list(status="dismissed")) == 1

    def test_chase_records_policy_url(self, store):
        lead = _lead()
        store.add_leads([lead])
        updated = store.update_status(
            lead.lead_id, "chased", policy_url="https://gov.example/law",
        )
        assert updated.policy_url == "https://gov.example/law"

    def test_invalid_status_rejected(self, store):
        lead = _lead()
        store.add_leads([lead])
        with pytest.raises(ValueError):
            store.update_status(lead.lead_id, "bogus")

    def test_unknown_lead_returns_none(self, store):
        assert store.update_status("nope", "dismissed") is None

    def test_corrupt_file_backed_up(self, tmp_path):
        (tmp_path / "leads.json").write_text("not json", encoding="utf-8")
        store = LeadStore(data_dir=str(tmp_path))
        assert store.list() == []
        assert (tmp_path / "leads.json.corrupt").exists()


class TestNoteOnlyLeadDedupe:
    """Note-only leads (no URL) must not collide on an empty source_url."""

    def _note_lead(self, title, snippet):
        return Lead(title=title, source_url="", snippet=snippet)

    def test_source_url_defaults_to_empty_string(self):
        lead = Lead(title="t")
        assert lead.source_url == ""

    def test_distinct_note_only_leads_both_added(self, store):
        added = store.add_leads([
            self._note_lead("Rumor A", "First rumor"),
            self._note_lead("Rumor B", "Second, unrelated rumor"),
        ])
        assert added == 2
        assert len(store.list()) == 2

    def test_identical_note_text_dedupes(self, store):
        store.add_leads([self._note_lead("Rumor", "Same text")])
        added = store.add_leads([self._note_lead("Rumor again", "Same text")])
        assert added == 0
        assert len(store.list()) == 1

    def test_note_only_lead_does_not_collide_with_url_lead(self, store):
        store.add_leads([_lead()])  # has a real source_url
        added = store.add_leads([self._note_lead("Rumor", "Some note")])
        assert added == 1
        assert len(store.list()) == 2


class TestUrlVariantDedupe:
    """WP-42: dedupe keys normalize URL variants (see core.urls.normalize_url)."""

    @pytest.mark.medium
    def test_utm_variant_of_stored_url_dedupes(self, store):
        store.add_leads([_lead(url="https://news.example/article")])
        added = store.add_leads(
            [_lead(url="https://news.example/article?utm_source=newsletter",
                    title="Same story, tracked link")]
        )
        assert added == 0
        assert len(store.list()) == 1

    @pytest.mark.medium
    def test_trailing_slash_and_scheme_variants_dedupe(self, store):
        store.add_leads([_lead(url="https://news.example/article")])
        added = store.add_leads([
            _lead(url="https://news.example/article/", title="Trailing slash"),
            _lead(url="HTTPS://NEWS.EXAMPLE/article", title="Different case host"),
        ])
        assert added == 0
        assert len(store.list()) == 1

    @pytest.mark.medium
    def test_pre_existing_raw_key_duplicates_exactly_once_then_dedupes(self, tmp_path):
        """Boundary documented in _dedupe_key: a row stored before this
        normalization pass keeps its raw-URL key. The first normalized
        variant collides with nothing (one honest duplicate); every
        variant after that shares the now-normalized key and dedupes.
        """
        import json

        store = LeadStore(data_dir=str(tmp_path))
        raw_key_url = "https://news.example/article?utm_source=old-run"
        # Simulate a row inserted under the pre-normalization scheme: the
        # dedupe key column holds the untouched raw URL.
        store._conn.execute(
            "INSERT INTO leads (lead_id, source_url, status, raw) VALUES (?, ?, ?, ?)",
            (
                "legacy1",
                raw_key_url,
                "new",
                json.dumps({
                    "lead_id": "legacy1", "title": "Legacy row",
                    "source_url": raw_key_url, "snippet": "", "jurisdiction_guess": "",
                    "origin": "news", "found_at": "2026-01-01T00:00:00+00:00",
                    "status": "new", "policy_url": None, "chased_at": None,
                    "chase_outcome": None, "chase_error": None,
                }),
            ),
        )
        store._conn.commit()

        added_first = store.add_leads(
            [_lead(url="https://news.example/article", title="Normalized variant")]
        )
        assert added_first == 1  # documented one-time duplicate
        assert len(store.list()) == 2

        added_second = store.add_leads(
            [_lead(url="https://news.example/article/", title="Another normalized variant")]
        )
        assert added_second == 0  # now dedupes against the normalized row
        assert len(store.list()) == 2


class TestRecordChase:
    """record_chase() persists a chase attempt's outcome and timing."""

    def test_defaults_are_unset(self, store):
        lead = _lead()
        store.add_leads([lead])
        fresh = store.get(lead.lead_id)
        assert fresh.chased_at is None
        assert fresh.chase_outcome is None
        assert fresh.chase_error is None

    def test_policy_found_marks_chased_with_url(self, store):
        lead = _lead()
        store.add_leads([lead])
        updated = store.record_chase(
            lead.lead_id, outcome="policy_found", mark_chased=True,
            policy_url="https://gov.example/law",
        )
        assert updated.status == "chased"
        assert updated.policy_url == "https://gov.example/law"
        assert updated.chase_outcome == "policy_found"
        assert updated.chased_at is not None

    def test_no_policy_marks_chased_without_url(self, store):
        lead = _lead()
        store.add_leads([lead])
        updated = store.record_chase(lead.lead_id, outcome="no_policy", mark_chased=True)
        assert updated.status == "chased"
        assert updated.policy_url is None
        assert updated.chase_outcome == "no_policy"
        assert updated.chased_at is not None

    def test_fetch_failed_keeps_status_and_records_error(self, store):
        """A fetch failure must not remove the tip from further chase attempts."""
        lead = _lead()
        store.add_leads([lead])
        updated = store.record_chase(
            lead.lead_id, outcome="fetch_failed", mark_chased=False,
            error="ConnectionError: too many redirects",
        )
        assert updated.status == "new"  # unchanged - stays chaseable
        assert updated.chase_outcome == "fetch_failed"
        assert updated.chase_error == "ConnectionError: too many redirects"
        assert updated.chased_at is not None

    def test_unknown_lead_returns_none(self, store):
        assert store.record_chase("nope", outcome="no_policy", mark_chased=True) is None
