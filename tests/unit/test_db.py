"""Tests for the SQLite storage foundation (src/storage/db.py): schema,
connection factory, JSON -> SQLite migration and its verification, FTS5
search with a LIKE fallback, and cross-connection (concurrent-ish) safety.
"""

import json
import sqlite3
from datetime import date

import pytest

from src.core.models import Policy, PolicyType
from src.storage import db as storage_db
from src.storage.leads import Lead, LeadStore
from src.storage.store import PolicyStore


def _full_policy_dict(url: str = "https://a.gov/full", **overrides) -> dict:
    """A Policy dict exercising the full field set, exactly as add_policies
    would serialize it (Policy.model_dump(mode="json"))."""
    defaults = dict(
        url=url,
        policy_name="Übergangsregelung für Abwärmenutzung",
        jurisdiction="Germany (Bayern)",
        policy_type=PolicyType.REGULATION,
        summary="Mandates data centre waste heat (Abwärme) reuse where feasible.",
        relevance_score=9,
        effective_date=date(2026, 1, 1),
        source_language="German",
        bill_number="BT-Drs. 20/1234",
        key_requirements="Operators over 1MW must submit a heat reuse (chaleur fatale) plan.",
        crawl_status="success",
        review_status="new",
        scan_id="scan-1",
        domain_id="domain-1",
        referenced_policies=["EnEfG §12"],
        referenced_urls=["https://a.gov/related"],
        lifecycle_stage="enacted",
    )
    defaults.update(overrides)
    return Policy(**defaults).model_dump(mode="json")


def _write_json(path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class TestMigrationFidelity:
    def test_no_legacy_files_is_a_no_op(self, tmp_path):
        storage_db.migrate_json_to_db(tmp_path)
        assert not (tmp_path / storage_db.DB_FILENAME).exists()

    def test_policies_round_trip_byte_equal(self, tmp_path):
        full = _full_policy_dict()
        null_fields = _full_policy_dict(
            url="https://b.gov/nulls", effective_date=None, bill_number=None,
            key_requirements=None, domain_id=None, scan_id=None,
        )
        # A partial/legacy-shaped record (not every store writes every key).
        partial = {
            "url": "https://c.gov/partial",
            "policy_name": "Loi sur la chaleur fatale",
            "jurisdiction": "France",
        }
        _write_json(tmp_path / "policies.json", [full, null_fields, partial])

        storage_db.migrate_json_to_db(tmp_path)

        conn = sqlite3.connect(tmp_path / storage_db.DB_FILENAME)
        rows = {
            row[0]: json.loads(row[1])
            for row in conn.execute("SELECT url, raw FROM policies")
        }
        conn.close()

        assert rows[full["url"]] == full
        assert rows[null_fields["url"]] == null_fields
        assert rows[partial["url"]] == partial

    def test_leads_round_trip_byte_equal(self, tmp_path):
        lead = Lead(
            title="Løv om overskudsvarme",
            source_url="https://news.example/da/artikel",
            snippet="Regeringen foreslår ny lov om genanvendelse af overskudsvarme",
            jurisdiction_guess="Denmark",
        ).model_dump(mode="json")
        _write_json(tmp_path / "leads.json", [lead])

        storage_db.migrate_json_to_db(tmp_path)

        conn = sqlite3.connect(tmp_path / storage_db.DB_FILENAME)
        row = conn.execute(
            "SELECT raw FROM leads WHERE lead_id = ?", (lead["lead_id"],)
        ).fetchone()
        conn.close()
        assert json.loads(row[0]) == lead

    def test_kv_files_round_trip(self, tmp_path):
        _write_json(tmp_path / "ask_usage.json", {"date": "2026-07-23", "count": 4})
        _write_json(tmp_path / "legiscan_usage.json", {"month": "2026-07", "queries": 120})
        _write_json(tmp_path / "legiscan_seen.json", {"1234": "abcd-hash"})
        _write_json(tmp_path / "nim_seen.json", {"denmark": ["m1", "m2"]})

        storage_db.migrate_json_to_db(tmp_path)

        conn = sqlite3.connect(tmp_path / storage_db.DB_FILENAME)
        assert storage_db.kv_get(conn, "ask_usage") == {"date": "2026-07-23", "count": 4}
        assert storage_db.kv_get(conn, "legiscan_usage") == {"month": "2026-07", "queries": 120}
        assert storage_db.kv_get(conn, "legiscan_seen") == {"1234": "abcd-hash"}
        assert storage_db.kv_get(conn, "nim_seen") == {"denmark": ["m1", "m2"]}
        conn.close()

    def test_source_json_files_untouched_after_migration(self, tmp_path):
        policies_path = tmp_path / "policies.json"
        leads_path = tmp_path / "leads.json"
        _write_json(policies_path, [_full_policy_dict()])
        _write_json(leads_path, [Lead(title="T", source_url="https://n.example/a").model_dump(mode="json")])
        policies_bytes = policies_path.read_bytes()
        leads_bytes = leads_path.read_bytes()

        storage_db.migrate_json_to_db(tmp_path)

        assert policies_path.read_bytes() == policies_bytes
        assert leads_path.read_bytes() == leads_bytes


class TestMigrationIdempotency:
    def test_second_call_does_not_duplicate_rows(self, tmp_path):
        _write_json(tmp_path / "policies.json", [_full_policy_dict()])

        storage_db.migrate_json_to_db(tmp_path)
        conn = sqlite3.connect(tmp_path / storage_db.DB_FILENAME)
        before = conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
        conn.close()

        storage_db.migrate_json_to_db(tmp_path)  # legacy JSON is still there
        conn = sqlite3.connect(tmp_path / storage_db.DB_FILENAME)
        after = conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0]
        conn.close()

        assert before == after == 1

    def test_second_store_construction_does_not_reimport(self, tmp_path):
        _write_json(tmp_path / "policies.json", [_full_policy_dict()])

        PolicyStore(data_dir=str(tmp_path))
        store2 = PolicyStore(data_dir=str(tmp_path))
        assert len(store2.get_all()) == 1


class TestMigrationVerificationFailure:
    def test_corrupted_write_raises_and_deletes_partial_db(self, tmp_path, monkeypatch):
        _write_json(tmp_path / "policies.json", [_full_policy_dict()])

        original_insert = storage_db._insert_policy_row

        def _tampering_insert(conn, record):
            # Simulate a write-path bug: what lands in the db silently
            # diverges from the JSON source.
            original_insert(conn, {**record, "policy_name": "TAMPERED"})

        monkeypatch.setattr(storage_db, "_insert_policy_row", _tampering_insert)

        with pytest.raises(storage_db.MigrationVerificationError):
            storage_db.migrate_json_to_db(tmp_path)

        assert not (tmp_path / storage_db.DB_FILENAME).exists()
        assert (tmp_path / "policies.json").exists()  # source never touched

    def test_retry_succeeds_once_write_path_is_fixed(self, tmp_path, monkeypatch):
        full = _full_policy_dict()
        _write_json(tmp_path / "policies.json", [full])

        original_insert = storage_db._insert_policy_row
        monkeypatch.setattr(
            storage_db, "_insert_policy_row",
            lambda conn, record: original_insert(conn, {**record, "policy_name": "X"}),
        )
        with pytest.raises(storage_db.MigrationVerificationError):
            storage_db.migrate_json_to_db(tmp_path)
        assert not (tmp_path / storage_db.DB_FILENAME).exists()

        monkeypatch.setattr(storage_db, "_insert_policy_row", original_insert)
        storage_db.migrate_json_to_db(tmp_path)

        conn = sqlite3.connect(tmp_path / storage_db.DB_FILENAME)
        row = conn.execute("SELECT raw FROM policies").fetchone()
        conn.close()
        assert json.loads(row[0]) == full


class TestFTS5Search:
    def test_fts5_enabled_on_this_build(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        assert storage_db.fts5_enabled(store._conn)

    def test_jurisdiction_substring_match(self, tmp_path):
        """Exact semantics of the JSON-backed store: case-insensitive substring,
        including mid-word fragments, which FTS5 token matching would miss."""
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([
            Policy(url="https://a.gov", policy_name="A", jurisdiction="Germany",
                   policy_type=PolicyType.LAW, summary="s", relevance_score=8),
            Policy(url="https://b.gov", policy_name="B", jurisdiction="France",
                   policy_type=PolicyType.LAW, summary="s", relevance_score=8),
        ])
        assert {p["url"] for p in store.search(jurisdiction="Ger")} == {"https://a.gov"}
        assert {p["url"] for p in store.search(jurisdiction="Germany")} == {"https://a.gov"}
        assert {p["url"] for p in store.search(jurisdiction="erman")} == {"https://a.gov"}
        assert {p["url"] for p in store.search(jurisdiction="RANC")} == {"https://b.gov"}

    def test_respects_other_filters_alongside_jurisdiction(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([
            Policy(url="https://a.gov", policy_name="A", jurisdiction="Germany",
                   policy_type=PolicyType.LAW, summary="s", relevance_score=9),
            Policy(url="https://c.gov", policy_name="C", jurisdiction="Germany",
                   policy_type=PolicyType.REGULATION, summary="s", relevance_score=3),
        ])
        results = store.search(jurisdiction="Germany", min_score=5)
        assert [p["url"] for p in results] == ["https://a.gov"]

    def test_summary_and_key_requirements_are_indexed(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([
            Policy(url="https://heat.gov", policy_name="Heat Reuse Mandate",
                   jurisdiction="US", policy_type=PolicyType.LAW,
                   summary="Requires heat reuse for data centres.", relevance_score=8),
            Policy(url="https://de.gov", policy_name="Abwaermegesetz",
                   jurisdiction="Germany", policy_type=PolicyType.LAW,
                   summary="Regelt die Nutzung von Abwärme.", relevance_score=8),
            Policy(url="https://fr.gov", policy_name="Loi chaleur fatale",
                   jurisdiction="France", policy_type=PolicyType.LAW,
                   summary="Encadre la chaleur fatale des centres de donnees.",
                   relevance_score=8),
        ])

        def hits(match_query):
            rows = store._conn.execute(
                "SELECT policies.url FROM policies "
                "JOIN policies_fts ON policies.rowid = policies_fts.rowid "
                "WHERE policies_fts MATCH ?",
                (match_query,),
            ).fetchall()
            return [r[0] for r in rows]

        assert hits('"heat reuse"') == ["https://heat.gov"]
        assert hits('"Abwärme"') == ["https://de.gov"]
        assert hits('"chaleur fatale"') == ["https://fr.gov"]


class TestLikeFallback:
    """Forces fts5_supported() to return False to exercise the LIKE path."""

    def test_forced_fallback_skips_fts_table(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage_db, "fts5_supported", lambda: False)
        store = PolicyStore(data_dir=str(tmp_path))
        assert not storage_db.fts5_enabled(store._conn)

    def test_search_works_without_fts5_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage_db, "fts5_supported", lambda: False)
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([
            Policy(url="https://a.gov", policy_name="A", jurisdiction="Germany",
                   policy_type=PolicyType.LAW, summary="s", relevance_score=8),
            Policy(url="https://b.gov", policy_name="B", jurisdiction="France",
                   policy_type=PolicyType.LAW, summary="s", relevance_score=8),
        ])
        assert {p["url"] for p in store.search(jurisdiction="Ger")} == {"https://a.gov"}
        assert {p["url"] for p in store.search(jurisdiction="Germany")} == {"https://a.gov"}
        assert {p["url"] for p in store.search()} == {"https://a.gov", "https://b.gov"}


def _seed_search_fixture(tmp_path):
    """Four policies in four languages/scripts, chosen so each free-text
    test case below has an unambiguous expected match set."""
    store = PolicyStore(data_dir=str(tmp_path))
    store.add_policies([
        Policy(
            url="https://heat.gov", policy_name="Heat Reuse Mandate", jurisdiction="US",
            policy_type=PolicyType.LAW, summary="Requires heat reuse for data centres.",
            relevance_score=7, review_status="new",
        ),
        Policy(
            url="https://de.gov", policy_name="Abwaermegesetz", jurisdiction="Germany",
            policy_type=PolicyType.LAW, summary="Regelt die Nutzung von Abwärme.",
            relevance_score=6, review_status="reviewed",
        ),
        Policy(
            url="https://fr.gov", policy_name="Loi chaleur fatale", jurisdiction="France",
            policy_type=PolicyType.REGULATION,
            summary="Encadre la chaleur fatale des centres de donnees.",
            key_requirements="Operators must submit a chaleur fatale plan.",
            relevance_score=9, review_status="new",
        ),
        Policy(
            url="https://jp.gov", policy_name="熱回収規則", jurisdiction="Japan",
            policy_type=PolicyType.REGULATION,
            summary="データセンターの排熱利用に関する規則。日本語の要約。",
            relevance_score=7, review_status="new",
        ),
    ])
    return store


class TestSearchText:
    """PolicyStore.search_text(): FTS5-backed free-text search with a
    LIKE-based fallback when FTS5 is unavailable."""

    def test_multiword_query_is_anded(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        results = store.search_text("heat reuse")
        assert {r["url"] for r in results} == {"https://heat.gov"}

    def test_prefix_match_on_last_token(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        results = store.search_text("chal")
        assert {r["url"] for r in results} == {"https://fr.gov"}

    def test_unicode_german_token(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        results = store.search_text("Abwärme")
        assert {r["url"] for r in results} == {"https://de.gov"}

    def test_unicode_japanese_token(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        results = store.search_text("日本語")
        assert {r["url"] for r in results} == {"https://jp.gov"}

    def test_quote_and_operator_injection_is_neutralized(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        malicious_queries = [
            '" OR "1"="1',
            "heat) OR (1=1",
            "heat OR reuse",
            "heat AND NOT reuse",
            "heat*",
            "juris:Germany",
            '"""',
            "(((",
            "heat -reuse",
            "NEAR(heat reuse)",
        ]
        for query in malicious_queries:
            results = store.search_text(query)  # must never raise
            assert isinstance(results, list)

    def test_jurisdiction_filter_combines_with_query(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        # "centres" hits both heat.gov (English) and fr.gov (French); the
        # jurisdiction filter must narrow that down to just one.
        results = store.search_text("centres", jurisdiction="france")
        assert {r["url"] for r in results} == {"https://fr.gov"}

    def test_policy_type_filter_combines_with_query(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        assert {r["url"] for r in store.search_text("centres", policy_type="regulation")} == {
            "https://fr.gov"
        }
        assert {r["url"] for r in store.search_text("centres", policy_type="law")} == {
            "https://heat.gov"
        }

    def test_min_score_filter_combines_with_query(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        results = store.search_text("centres", min_score=8)
        assert {r["url"] for r in results} == {"https://fr.gov"}

    def test_review_status_filter_combines_with_query(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        assert {r["url"] for r in store.search_text("Regelt", review_status="reviewed")} == {
            "https://de.gov"
        }
        assert store.search_text("Regelt", review_status="new") == []

    def test_all_filters_together(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        results = store.search_text(
            "centres", jurisdiction="france", policy_type="regulation",
            min_score=5, review_status="new",
        )
        assert {r["url"] for r in results} == {"https://fr.gov"}

    def test_limit_clamps_to_100(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([
            Policy(
                url=f"https://a.gov/{i}", policy_name=f"Heat Policy {i}", jurisdiction="US",
                policy_type=PolicyType.LAW, summary="heat reuse", relevance_score=5,
            )
            for i in range(150)
        ])
        assert len(store.search_text("heat", limit=1000)) == 100

    def test_limit_clamps_to_1_minimum(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([
            Policy(url="https://a.gov/1", policy_name="Heat Policy A", jurisdiction="US",
                   policy_type=PolicyType.LAW, summary="heat reuse", relevance_score=5),
            Policy(url="https://a.gov/2", policy_name="Heat Policy B", jurisdiction="US",
                   policy_type=PolicyType.LAW, summary="heat reuse", relevance_score=5),
        ])
        assert len(store.search_text("heat", limit=0)) == 1
        assert len(store.search_text("heat", limit=-5)) == 1

    def test_empty_or_whitespace_query_returns_empty(self, tmp_path):
        store = _seed_search_fixture(tmp_path)
        assert store.search_text("") == []
        assert store.search_text("   ") == []

    def test_ranked_by_relevance_name_hit_first(self, tmp_path):
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([
            Policy(
                url="https://buried.gov", policy_name="Some Policy", jurisdiction="US",
                policy_type=PolicyType.LAW,
                summary=(
                    "This regulation, deep within a long paragraph about many other "
                    "unrelated administrative matters and general provisions, "
                    "eventually gets to mandating waste heat reuse for facilities."
                ),
                relevance_score=5,
            ),
            Policy(
                url="https://named.gov", policy_name="Waste Heat Reuse Act", jurisdiction="US",
                policy_type=PolicyType.LAW, summary="A short act.", relevance_score=5,
            ),
        ])
        results = store.search_text("waste heat reuse")
        assert [r["url"] for r in results] == ["https://named.gov", "https://buried.gov"]


class TestSearchTextLikeFallback:
    """Same fixture and assertions as TestSearchText, forced onto the LIKE
    fallback path (fts5_supported() -> False) to prove parity."""

    def test_multiword_query_is_anded(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage_db, "fts5_supported", lambda: False)
        store = _seed_search_fixture(tmp_path)
        assert not storage_db.fts5_enabled(store._conn)
        results = store.search_text("heat reuse")
        assert {r["url"] for r in results} == {"https://heat.gov"}

    def test_unicode_german_token(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage_db, "fts5_supported", lambda: False)
        store = _seed_search_fixture(tmp_path)
        results = store.search_text("Abwärme")
        assert {r["url"] for r in results} == {"https://de.gov"}

    def test_jurisdiction_filter_combines_with_query(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage_db, "fts5_supported", lambda: False)
        store = _seed_search_fixture(tmp_path)
        results = store.search_text("centres", jurisdiction="france")
        assert {r["url"] for r in results} == {"https://fr.gov"}

    def test_quote_and_operator_injection_is_neutralized(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage_db, "fts5_supported", lambda: False)
        store = _seed_search_fixture(tmp_path)
        for query in ['" OR "1"="1', "heat) OR (1=1", "100%", "heat*", "under_score"]:
            results = store.search_text(query)  # must never raise
            assert isinstance(results, list)

    def test_empty_or_whitespace_query_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage_db, "fts5_supported", lambda: False)
        store = _seed_search_fixture(tmp_path)
        assert store.search_text("") == []
        assert store.search_text("   ") == []

    def test_limit_clamps_to_100(self, tmp_path, monkeypatch):
        monkeypatch.setattr(storage_db, "fts5_supported", lambda: False)
        store = PolicyStore(data_dir=str(tmp_path))
        store.add_policies([
            Policy(
                url=f"https://a.gov/{i}", policy_name=f"Heat Policy {i}", jurisdiction="US",
                policy_type=PolicyType.LAW, summary="heat reuse", relevance_score=5,
            )
            for i in range(150)
        ])
        assert len(store.search_text("heat", limit=1000)) == 100


class TestConcurrentAccess:
    def test_two_policy_stores_see_committed_writes(self, tmp_path):
        store1 = PolicyStore(data_dir=str(tmp_path))
        store2 = PolicyStore(data_dir=str(tmp_path))

        store1.add_policies([
            Policy(url="https://a.gov", policy_name="A", jurisdiction="US",
                   policy_type=PolicyType.LAW, summary="s", relevance_score=8),
        ])
        assert len(store2.get_all()) == 1

        store2.update_review_status("https://a.gov", "confirmed")
        assert store1.get_all()[0]["review_status"] == "confirmed"

    def test_two_lead_stores_see_committed_writes(self, tmp_path):
        store1 = LeadStore(data_dir=str(tmp_path))
        store2 = LeadStore(data_dir=str(tmp_path))
        store1.add_leads([Lead(title="T", source_url="https://n.example/a")])
        assert len(store2.list()) == 1


class TestKvHelpers:
    def test_set_and_get_round_trip_across_connections(self, tmp_path):
        conn1 = storage_db.connect(tmp_path)
        storage_db.kv_set(conn1, "ask_usage", {"date": "2026-07-23", "count": 1})
        conn1.close()

        conn2 = storage_db.connect(tmp_path)
        assert storage_db.kv_get(conn2, "ask_usage") == {"date": "2026-07-23", "count": 1}
        conn2.close()

    def test_set_overwrites_existing_value(self, tmp_path):
        conn = storage_db.connect(tmp_path)
        storage_db.kv_set(conn, "legiscan_usage", {"month": "2026-06", "queries": 10})
        storage_db.kv_set(conn, "legiscan_usage", {"month": "2026-07", "queries": 1})
        assert storage_db.kv_get(conn, "legiscan_usage") == {"month": "2026-07", "queries": 1}
        conn.close()

    def test_missing_key_returns_none(self, tmp_path):
        conn = storage_db.connect(tmp_path)
        assert storage_db.kv_get(conn, "does-not-exist") is None
        conn.close()


class TestJurisdictionsMirror:
    def test_rebuilt_from_yaml_on_connect(self, tmp_path):
        conn = storage_db.connect(tmp_path)
        count = conn.execute("SELECT COUNT(*) FROM jurisdictions").fetchone()[0]
        assert count > 0
        row = conn.execute(
            "SELECT name, kind FROM jurisdictions WHERE slug = 'eu'"
        ).fetchone()
        assert row == ("European Union", "supranational")
        conn.close()


@pytest.mark.medium
class TestScanDomainsSchema:
    """The scan_domains table (WP-23) - one row per domain per scan."""

    def test_table_created_on_connect(self, tmp_path):
        conn = storage_db.connect(tmp_path)
        conn.execute(
            "INSERT INTO scan_domains (scan_id, domain_id, channel, pages_crawled, "
            "keywords_matched, filtered_keywords, filtered_screening, llm_skipped, "
            "policies_found, errors, completed_at) VALUES "
            "('s1', 'd1', 'crawl', 100, 10, 5, 2, 1, 3, 0, '2026-01-01T00:00:00')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT scan_id, domain_id, channel, pages_crawled FROM scan_domains"
        ).fetchone()
        assert row == ("s1", "d1", "crawl", 100)
        conn.close()

    def test_primary_key_is_scan_id_and_domain_id(self, tmp_path):
        conn = storage_db.connect(tmp_path)
        conn.execute(
            "INSERT INTO scan_domains (scan_id, domain_id, channel) "
            "VALUES ('s1', 'd1', 'crawl')"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO scan_domains (scan_id, domain_id, channel) "
                "VALUES ('s1', 'd1', 'law_apis')"
            )
        conn.close()

    def test_reconnect_does_not_duplicate_or_error(self, tmp_path):
        conn1 = storage_db.connect(tmp_path)
        conn1.execute(
            "INSERT INTO scan_domains (scan_id, domain_id, channel) "
            "VALUES ('s1', 'd1', 'crawl')"
        )
        conn1.commit()
        conn1.close()

        conn2 = storage_db.connect(tmp_path)  # migration/schema setup re-runs
        count = conn2.execute("SELECT COUNT(*) FROM scan_domains").fetchone()[0]
        assert count == 1
        conn2.close()


@pytest.mark.medium
class TestScansEstimateColumnsMigration:
    """The estimated_cost_usd/estimated_low_usd/estimated_high_usd columns
    (WP-24) on the ``scans`` table: present on a fresh db via CREATE TABLE,
    and guard-added in place via ALTER TABLE for a pre-existing db that
    predates them."""

    def test_fresh_db_has_estimate_columns(self, tmp_path):
        conn = storage_db.connect(tmp_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(scans)")}
        assert {"estimated_cost_usd", "estimated_low_usd", "estimated_high_usd"} <= columns
        conn.close()

    def test_pre_existing_scans_table_gets_columns_added(self, tmp_path):
        # Simulate a database created before WP-24: a scans table with the
        # old column set only, no estimate columns.
        db_path = tmp_path / storage_db.DB_FILENAME
        legacy = sqlite3.connect(db_path)
        legacy.execute(
            "CREATE TABLE scans (scan_id TEXT PRIMARY KEY, domain_group TEXT, "
            "mode TEXT, channels TEXT, status TEXT, started_at TEXT, "
            "completed_at TEXT, domains_scanned INTEGER, policies_found INTEGER, "
            "cost_usd REAL, input_tokens INTEGER, output_tokens INTEGER)"
        )
        legacy.execute(
            "INSERT INTO scans (scan_id, domain_group, status) VALUES ('s1', 'quick', 'completed')"
        )
        legacy.commit()
        legacy.close()

        conn = storage_db.connect(tmp_path)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(scans)")}
        assert {"estimated_cost_usd", "estimated_low_usd", "estimated_high_usd"} <= columns
        # The pre-existing row survives the ALTER, with the new columns NULL.
        row = conn.execute(
            "SELECT scan_id, estimated_cost_usd FROM scans WHERE scan_id = 's1'"
        ).fetchone()
        assert row == ("s1", None)
        conn.close()

    def test_migration_is_idempotent_across_reconnects(self, tmp_path):
        storage_db.connect(tmp_path).close()
        # Second connect() must not raise "duplicate column name".
        conn = storage_db.connect(tmp_path)
        columns = [row[1] for row in conn.execute("PRAGMA table_info(scans)")]
        assert columns.count("estimated_cost_usd") == 1
        conn.close()
