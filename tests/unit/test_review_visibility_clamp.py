"""Clamping matrix for WP-3 "public review visibility".

Non-admin callers must never see rejected policies, anywhere, under any
combination of posture + ?review= param. Admin callers (valid token, or
loopback/testclient in open mode) are fully exempt — existing behavior.

Covers GET /api/policies, GET /api/policies/search, GET /api/coverage (and
/api/coverage/children), plus the in-memory scan-results merge.
"""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from src.core.models import Policy, PolicyType
from src.storage.public_visibility import PublicVisibilityStore, PublicVisibilitySettings
from src.storage.store import PolicyStore

REVIEW_STATUSES = ["new", "reviewed", "promoted", "rejected"]
POSTURES = ["default_all", "default_reviewed", "reviewed_only"]


def _policy(url: str, review_status: str) -> Policy:
    return Policy(
        url=url,
        policy_name=f"Policy {review_status}",
        jurisdiction="Sweden",
        policy_type=PolicyType.LAW,
        summary="A heat reuse policy",
        relevance_score=7,
        review_status=review_status,
    )


@pytest.fixture
def store(tmp_path):
    s = PolicyStore(data_dir=str(tmp_path))
    s.add_policies([_policy(f"https://a.gov/{status}", status) for status in REVIEW_STATUSES])
    return s


@pytest.fixture
def visibility_store(tmp_path):
    # Same data_dir as `store` in tests that use both — separate tmp_path
    # here is fine too since each store opens its own sqlite connection to
    # whatever data_dir it's given; posture and policies are independent
    # concerns and every test below overrides both dependencies explicitly.
    return PublicVisibilityStore(data_dir=str(tmp_path))


def _empty_manager():
    manager = MagicMock()
    manager.get_all_policies.return_value = []
    return manager


def _make_client(store, visibility_store, manager=None, remote=False):
    from src.api.app import app
    from src.api import deps

    app.dependency_overrides[deps.get_policy_store] = lambda: store
    app.dependency_overrides[deps.get_scan_manager] = lambda: (manager or _empty_manager())
    app.dependency_overrides[deps.get_public_visibility_store] = lambda: visibility_store
    client_addr = ("203.0.113.5", 12345) if remote else None
    return app, TestClient(app, client=client_addr) if client_addr else TestClient(app)


def _set_posture(visibility_store, mode):
    visibility_store.update(PublicVisibilitySettings(mode=mode))


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from src.api.app import app
    app.dependency_overrides.clear()


class TestPoliciesClampMatrix:
    """review_status counts: new, reviewed, promoted, rejected = 1 each."""

    @pytest.mark.parametrize("posture", POSTURES)
    @pytest.mark.parametrize("review_param", [None, "all"])
    def test_admin_always_sees_all_four(self, store, visibility_store, posture, review_param):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=False)  # testclient == admin
        params = {"review": review_param} if review_param else {}
        resp = client.get("/api/policies", params=params)
        assert resp.status_code == 200
        assert resp.json()["count"] == 4

    @pytest.mark.parametrize("posture", POSTURES)
    def test_admin_explicit_reviewed_narrows_to_promoted(self, store, visibility_store, posture):
        # An explicit review=reviewed is an intentional narrowing and is
        # honored even for admins — the public toggle must behave the same
        # in open-mode loopback demos, where every request counts as admin.
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=False)
        resp = client.get("/api/policies", params={"review": "reviewed"})
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        assert resp.json()["policies"][0]["review_status"] == "promoted"

    @pytest.mark.parametrize("posture", ["default_all", "default_reviewed"])
    def test_non_admin_absent_param_excludes_only_rejected(self, store, visibility_store, posture):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=True)
        resp = client.get("/api/policies")
        assert resp.status_code == 200
        statuses = {p["review_status"] for p in resp.json()["policies"]}
        assert statuses == {"new", "reviewed", "promoted"}
        assert "rejected" not in statuses

    @pytest.mark.parametrize("posture", ["default_all", "default_reviewed"])
    def test_non_admin_review_all_excludes_only_rejected(self, store, visibility_store, posture):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=True)
        resp = client.get("/api/policies", params={"review": "all"})
        statuses = {p["review_status"] for p in resp.json()["policies"]}
        assert statuses == {"new", "reviewed", "promoted"}

    @pytest.mark.parametrize("posture", POSTURES)
    def test_non_admin_review_reviewed_is_promoted_only(self, store, visibility_store, posture):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=True)
        resp = client.get("/api/policies", params={"review": "reviewed"})
        statuses = {p["review_status"] for p in resp.json()["policies"]}
        assert statuses == {"promoted"}

    def test_non_admin_reviewed_only_posture_ignores_review_param(self, store, visibility_store):
        _set_posture(visibility_store, "reviewed_only")
        _, client = _make_client(store, visibility_store, remote=True)
        for params in ({}, {"review": "all"}, {"review": "reviewed"}):
            resp = client.get("/api/policies", params=params)
            statuses = {p["review_status"] for p in resp.json()["policies"]}
            assert statuses == {"promoted"}, params

    def test_non_admin_rejected_never_appears_in_any_cell(self, store, visibility_store):
        for posture in POSTURES:
            _set_posture(visibility_store, posture)
            _, client = _make_client(store, visibility_store, remote=True)
            for params in ({}, {"review": "all"}, {"review": "reviewed"}):
                resp = client.get("/api/policies", params=params)
                statuses = {p["review_status"] for p in resp.json()["policies"]}
                assert "rejected" not in statuses, (posture, params)


class TestRejectedReviewStatusParam:
    def test_non_admin_explicit_rejected_query_returns_empty(self, store, visibility_store):
        _, client = _make_client(store, visibility_store, remote=True)
        resp = client.get("/api/policies", params={"review_status": "rejected"})
        assert resp.status_code == 200
        assert resp.json() == {"policies": [], "count": 0}

    def test_admin_explicit_rejected_query_returns_the_row(self, store, visibility_store):
        _, client = _make_client(store, visibility_store, remote=False)
        resp = client.get("/api/policies", params={"review_status": "rejected"})
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        assert resp.json()["policies"][0]["review_status"] == "rejected"


class TestSearchClampMatrix:
    """Same matrix, over the free-text /api/policies/search endpoint."""

    def _search(self, client, **params):
        params.setdefault("q", "sweden")
        return client.get("/api/policies/search", params=params)

    @pytest.mark.parametrize("posture", POSTURES)
    def test_admin_sees_all_four(self, store, visibility_store, posture):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=False)
        resp = self._search(client)
        assert resp.status_code == 200
        assert resp.json()["total"] == 4

    @pytest.mark.parametrize("posture", ["default_all", "default_reviewed"])
    def test_non_admin_default_excludes_rejected(self, store, visibility_store, posture):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=True)
        resp = self._search(client)
        statuses = {p["review_status"] for p in resp.json()["policies"]}
        assert statuses == {"new", "reviewed", "promoted"}

    @pytest.mark.parametrize("posture", POSTURES)
    def test_non_admin_reviewed_is_promoted_only(self, store, visibility_store, posture):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=True)
        resp = self._search(client, review="reviewed")
        statuses = {p["review_status"] for p in resp.json()["policies"]}
        assert statuses == {"promoted"}

    def test_reviewed_only_posture_ignores_review_param(self, store, visibility_store):
        _set_posture(visibility_store, "reviewed_only")
        _, client = _make_client(store, visibility_store, remote=True)
        resp = self._search(client, review="all")
        statuses = {p["review_status"] for p in resp.json()["policies"]}
        assert statuses == {"promoted"}


class TestCoverageClampMatrix:
    @pytest.mark.parametrize("posture", POSTURES)
    def test_admin_sees_all_four(self, store, visibility_store, posture):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=False)
        resp = client.get("/api/coverage")
        assert resp.status_code == 200
        assert resp.json()["totals"]["policies"] == 4

    @pytest.mark.parametrize("posture", ["default_all", "default_reviewed"])
    def test_non_admin_default_excludes_rejected(self, store, visibility_store, posture):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=True)
        resp = client.get("/api/coverage")
        assert resp.json()["totals"]["policies"] == 3

    @pytest.mark.parametrize("posture", POSTURES)
    def test_non_admin_reviewed_is_promoted_only(self, store, visibility_store, posture):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=True)
        resp = client.get("/api/coverage", params={"review": "reviewed"})
        assert resp.json()["totals"]["policies"] == 1

    def test_reviewed_only_posture_forces_one_regardless_of_param(self, store, visibility_store):
        _set_posture(visibility_store, "reviewed_only")
        _, client = _make_client(store, visibility_store, remote=True)
        for params in ({}, {"review": "all"}, {"review": "reviewed"}):
            resp = client.get("/api/coverage", params=params)
            assert resp.json()["totals"]["policies"] == 1, params


class TestCoverageChildrenClamp:
    @pytest.fixture
    def us_store(self, tmp_path):
        s = PolicyStore(data_dir=str(tmp_path))
        s.add_policies([
            Policy(
                url=f"https://a.gov/{status}",
                policy_name=f"NJ policy {status}",
                jurisdiction="New Jersey, United States",
                policy_type=PolicyType.LAW,
                summary="s",
                relevance_score=7,
                review_status=status,
            )
            for status in REVIEW_STATUSES
        ])
        return s

    def test_admin_sees_all_four_children(self, us_store, visibility_store):
        _, client = _make_client(us_store, visibility_store, remote=False)
        resp = client.get("/api/coverage/children", params={"parent": "us"})
        nj = next(c for c in resp.json()["children"] if c["slug"] == "new_jersey")
        assert nj["policies"] == 4

    def test_non_admin_default_excludes_rejected(self, us_store, visibility_store):
        _, client = _make_client(us_store, visibility_store, remote=True)
        resp = client.get("/api/coverage/children", params={"parent": "us"})
        nj = next(c for c in resp.json()["children"] if c["slug"] == "new_jersey")
        assert nj["policies"] == 3

    def test_non_admin_reviewed_view_is_promoted_only(self, us_store, visibility_store):
        _, client = _make_client(us_store, visibility_store, remote=True)
        resp = client.get(
            "/api/coverage/children", params={"parent": "us", "review": "reviewed"},
        )
        nj = next(c for c in resp.json()["children"] if c["slug"] == "new_jersey")
        assert nj["policies"] == 1


class TestCoveragePoliciesAgree:
    """/api/coverage's per-country count must equal /api/policies's count for
    the same effective view — otherwise the map and list would disagree."""

    @pytest.mark.parametrize("posture", POSTURES)
    @pytest.mark.parametrize("review_param", [None, "reviewed", "all"])
    def test_counts_reconcile_for_non_admin(self, store, visibility_store, posture, review_param):
        _set_posture(visibility_store, posture)
        _, client = _make_client(store, visibility_store, remote=True)
        params = {"review": review_param} if review_param else {}
        policies_count = client.get("/api/policies", params=params).json()["count"]
        coverage_total = client.get("/api/coverage", params=params).json()["totals"]["policies"]
        assert policies_count == coverage_total


class TestInMemoryMergeRespectsView:
    """Freshly-scanned policies (review_status='new') live in ScanManager's
    in-memory list until persisted — the merge must apply the same clamp."""

    def test_new_in_memory_policy_excluded_from_reviewed_view(self, tmp_path, visibility_store):
        store = PolicyStore(data_dir=str(tmp_path))
        fresh = _policy("https://fresh.gov/x", "new")
        manager = MagicMock()
        manager.get_all_policies.return_value = [fresh]

        _, client = _make_client(store, visibility_store, manager=manager, remote=True)
        resp = client.get("/api/policies", params={"review": "reviewed"})
        assert resp.json()["count"] == 0

        cov = client.get("/api/coverage", params={"review": "reviewed"}).json()
        assert cov["totals"]["policies"] == 0

    def test_new_in_memory_policy_included_in_all_view(self, tmp_path, visibility_store):
        store = PolicyStore(data_dir=str(tmp_path))
        fresh = _policy("https://fresh.gov/x", "new")
        manager = MagicMock()
        manager.get_all_policies.return_value = [fresh]

        _, client = _make_client(store, visibility_store, manager=manager, remote=True)
        resp = client.get("/api/policies", params={"review": "all"})
        assert resp.json()["count"] == 1

        cov = client.get("/api/coverage", params={"review": "all"}).json()
        assert cov["totals"]["policies"] == 1

    def test_rejected_in_memory_policy_never_shown_to_non_admin(self, tmp_path, visibility_store):
        store = PolicyStore(data_dir=str(tmp_path))
        rejected = _policy("https://fresh.gov/rej", "rejected")
        manager = MagicMock()
        manager.get_all_policies.return_value = [rejected]

        _, client = _make_client(store, visibility_store, manager=manager, remote=True)
        resp = client.get("/api/policies", params={"review": "all"})
        assert resp.json()["count"] == 0

    def test_admin_still_sees_in_memory_new_policy(self, tmp_path, visibility_store):
        store = PolicyStore(data_dir=str(tmp_path))
        fresh = _policy("https://fresh.gov/x", "new")
        manager = MagicMock()
        manager.get_all_policies.return_value = [fresh]

        _, client = _make_client(store, visibility_store, manager=manager, remote=False)
        resp = client.get("/api/policies")
        assert resp.json()["count"] == 1
