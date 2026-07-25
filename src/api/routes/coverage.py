"""World-map coverage — read-only aggregate of where PolicyPulse has looked.

``GET /api/coverage`` answers "which countries have tracked sources or found
policies, and what sits above the country level" in one shot, so the map can
color countries before anyone types a word. Nothing is precomputed or stored:
every count is derived at request time from the canonical jurisdiction
registry (``src/core/jurisdictions.py``) applied to the existing policy data
and domain configs. Adding a source or finding a policy changes the response
with no schema change and no backfill.

Attribution rules (uniform for policies and sources):
- ``resolve_text`` / registry slug -> a ``Jurisdiction``.
- ``country_of`` rolls ``country``/``us_state``/``subnational`` up to a
  country, keyed by ``iso_numeric`` (the world-atlas join key).
- Jurisdictions with no map shape become ``supranational`` (off-map) entries
  keyed by slug: ``supranational``/``group`` kinds (the EU, a future ``global``
  IGO bucket) AND countries the registry carries without an ``iso_numeric``
  (e.g. Kosovo). "EU" is never hardcoded — whatever the registry returns is
  handled, and null-iso countries never land in ``countries`` under a None key.
- Source counts attribute a domain to a country once per country, however many
  of its ``region`` tags roll up there. Off-map entries carry a ``sources``
  count too, but only for null-iso countries; supranational/group entries stay
  policy-driven (a broad ``region`` tag like ``nordic`` is not a chip).
- Each country entry also carries ``children_with_data``: the count of its
  ``us_state``/``subnational`` children (per the registry) with >=1 policy or
  source resolved *directly* to them (no roll-up). 0 means not drillable.

``GET /api/coverage/children?parent=<country-slug>`` answers "what does this
one country look like broken out by state/province" — the drill-down behind
``children_with_data``. Unlike the world view, nothing is rolled up: a policy
or source lands under ``national`` only if it resolves to the country
jurisdiction itself, under a child only if it resolves to that exact child,
and nowhere in this response if it resolves elsewhere. ``national`` and each
child are kept visually distinct so a federal/nationwide policy is never
mistaken for a single-state one.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..deps import (
    get_config, get_domain_overrides_store, get_policy_store,
    get_public_visibility_store, get_scan_manager,
)
from ..review_visibility import passes_visibility, visibility_filter_kwargs
from ...core import jurisdictions
from ...core.config import ConfigLoader
from ...core.overrides import apply_domain_overrides
from ...orchestration.scan_manager import ScanManager
from ...storage.domain_overrides import DomainOverridesStore
from ...storage.store import PolicyStore

router = APIRouter(prefix="/api", tags=["coverage"])

_MAX_TOP_POLICIES = 3


def _top_policy_names(policies: list[dict]) -> list[str]:
    """Up to three policy names for a bucket, highest relevance first."""
    ranked = sorted(
        policies,
        key=lambda p: (-(p.get("relevance_score") or 0), p.get("policy_name") or ""),
    )
    return [p.get("policy_name") or "" for p in ranked[:_MAX_TOP_POLICIES]]


def compute_coverage(policies: list[dict], domains: list[dict]) -> dict:
    """Aggregate policies and domain sources into per-jurisdiction coverage.

    Pure: takes already-loaded policy dicts and domain dicts, returns the
    response body plus a ``diagnostics`` block. The route strips diagnostics
    off ``/api/coverage`` and exposes them on ``/api/coverage/unresolved``.
    """
    # country iso_numeric -> aggregate; slug -> supranational aggregate
    country_policies: dict[str, list[dict]] = {}
    country_names: dict[str, str] = {}
    country_slugs: dict[str, str] = {}
    country_sources: dict[str, set[str]] = {}
    # "off-map" = jurisdictions with no choropleth shape: supranational/group
    # (the EU, a future global IGO bucket) AND countries the registry carries
    # without an iso_numeric (e.g. Kosovo, code XK). All are keyed by slug and
    # rendered as chips beside the map, never as a country fill — and never
    # under a None iso key, where null-iso territories would silently collide.
    offmap_policies: dict[str, list[dict]] = {}
    offmap_names: dict[str, str] = {}
    offmap_sources: dict[str, set[str]] = {}
    unresolved_policies: list[str] = []
    unresolved_slugs: set[str] = set()
    # country iso_numeric -> slugs of its us_state/subnational children that
    # have >=1 policy or source resolved directly to them (no roll-up). Drives
    # ``children_with_data`` — the drill affordance for /api/coverage/children.
    child_data: dict[str, set[str]] = {}

    for policy in policies:
        raw = policy.get("jurisdiction")
        jur = jurisdictions.resolve_text(raw)
        if jur is None:
            unresolved_policies.append(raw or "(no jurisdiction)")
            continue
        country = jurisdictions.country_of(jur)
        if country is not None and country.iso_numeric:
            country_names.setdefault(country.iso_numeric, country.name)
            country_slugs.setdefault(country.iso_numeric, country.slug)
            country_policies.setdefault(country.iso_numeric, []).append(policy)
            if jur.kind in ("us_state", "subnational"):
                child_data.setdefault(country.iso_numeric, set()).add(jur.slug)
        elif country is not None:
            # A real country the registry has no iso_numeric for (Kosovo).
            offmap_names.setdefault(country.slug, country.name)
            offmap_policies.setdefault(country.slug, []).append(policy)
        elif jur.kind in ("supranational", "group"):
            offmap_names.setdefault(jur.slug, jur.name)
            offmap_policies.setdefault(jur.slug, []).append(policy)
        else:
            # Resolved but neither a country nor a supra/group kind — should
            # not happen with the current registry, but never silently drop it.
            unresolved_policies.append(raw)

    for domain in domains:
        did = domain.get("id")
        for slug in (domain.get("region") or []):
            jur = jurisdictions.get(slug)
            if jur is None:
                unresolved_slugs.add(slug)
                continue
            country = jurisdictions.country_of(jur)
            if country is not None and country.iso_numeric:
                country_names.setdefault(country.iso_numeric, country.name)
                country_slugs.setdefault(country.iso_numeric, country.slug)
                country_sources.setdefault(country.iso_numeric, set()).add(did)
                if jur.kind in ("us_state", "subnational"):
                    child_data.setdefault(country.iso_numeric, set()).add(jur.slug)
            elif country is not None:
                # Null-iso country source (Kosovo) -> off-map, keyed by slug,
                # so a country tracked-but-with-no-shape still shows coverage.
                offmap_names.setdefault(country.slug, country.name)
                offmap_sources.setdefault(country.slug, set()).add(did)
            # Group/supranational region tags (eu, nordic, apac, ...) do not
            # attribute a source anywhere; totals.sources still counts the
            # domain. Those off-map entries stay policy-driven.

    countries = [
        {
            "name": country_names[iso],
            "slug": country_slugs[iso],
            "iso_numeric": iso,
            "sources": len(country_sources.get(iso, set())),
            "policies": len(country_policies.get(iso, [])),
            "top_policy_names": _top_policy_names(country_policies.get(iso, [])),
            "children_with_data": len(child_data.get(iso, set())),
        }
        for iso in (country_names.keys() | country_sources.keys())
    ]
    countries.sort(key=lambda c: (-c["policies"], c["name"]))

    supranational = [
        {
            "name": offmap_names[slug],
            "slug": slug,
            "sources": len(offmap_sources.get(slug, set())),
            "policies": len(offmap_policies.get(slug, [])),
            "top_policy_names": _top_policy_names(offmap_policies.get(slug, [])),
        }
        for slug in (offmap_names.keys() | offmap_sources.keys())
    ]
    supranational.sort(key=lambda s: (-s["policies"], -s["sources"], s["name"]))

    return {
        "countries": countries,
        "supranational": supranational,
        "totals": {"sources": len(domains), "policies": len(policies)},
        "diagnostics": {
            "unresolved_policies": unresolved_policies,
            "unresolved_region_slugs": sorted(unresolved_slugs),
        },
    }


def compute_children(
    parent_slug: str, policies: list[dict], domains: list[dict]
) -> Optional[dict]:
    """Break one country out by its registered state/province children.

    Pure, like :func:`compute_coverage`. Returns ``None`` when ``parent_slug``
    is not a known ``country`` jurisdiction — the route turns that into a 404.

    No roll-up: a policy or source lands under ``national`` only when it
    resolves to the country jurisdiction itself, under a child only when it
    resolves to that exact child (``jurisdictions.children_of`` — generic over
    registry depth, so new admin-1 rows need no code change here), and is
    dropped from this response when it resolves anywhere else. Children with
    no data (no source, no policy) are omitted, matching ``children_with_data``
    on ``/api/coverage``.

    ``totals`` reconciles exactly with this country's entry in the world view,
    for both fields. Policies do so naturally (each resolves to exactly one
    jurisdiction, so ``national + sum(children)`` is the country total).
    Sources need a distinct-domain count: a domain tagged for both the country
    and one of its states appears in both buckets (that overlap is real —
    "3 sources watch Minnesota" and "165 watch the US" can share domains), so
    ``totals.sources`` counts distinct domains, same semantics as the world
    endpoint, and per-bucket sums may exceed it.
    """
    parent = jurisdictions.get(parent_slug)
    if parent is None or parent.kind != "country":
        return None

    children = jurisdictions.children_of(parent)
    child_by_slug = {c.slug: c for c in children}

    national_policies: list[dict] = []
    child_policies: dict[str, list[dict]] = {slug: [] for slug in child_by_slug}
    national_sources: set[str] = set()
    child_sources: dict[str, set[str]] = {slug: set() for slug in child_by_slug}

    for policy in policies:
        jur = jurisdictions.resolve_text(policy.get("jurisdiction"))
        if jur is None:
            continue
        if jur.slug == parent.slug:
            national_policies.append(policy)
        elif jur.slug in child_by_slug:
            child_policies[jur.slug].append(policy)
        # else: resolves elsewhere (another country, supranational, ...) —
        # not part of this country's breakdown.

    # Distinct domains attributed anywhere in this country — same semantics as
    # the world endpoint's per-country ``sources``, so totals reconcile even
    # when a multi-tag domain sits in several buckets below.
    distinct_sources: set[str] = set()

    for domain in domains:
        did = domain.get("id")
        for slug in (domain.get("region") or []):
            jur = jurisdictions.get(slug)
            if jur is None:
                continue
            if jur.slug == parent.slug:
                national_sources.add(did)
                distinct_sources.add(did)
            elif jur.slug in child_by_slug:
                child_sources[jur.slug].add(did)
                distinct_sources.add(did)

    national = {
        "sources": len(national_sources),
        "policies": len(national_policies),
        "top_policy_names": _top_policy_names(national_policies),
    }

    children_out = [
        {
            "slug": c.slug,
            "name": c.name,
            "kind": c.kind,
            "code": c.code,
            "sources": len(child_sources[c.slug]),
            "policies": len(child_policies[c.slug]),
            "top_policy_names": _top_policy_names(child_policies[c.slug]),
        }
        for c in children
        if child_sources[c.slug] or child_policies[c.slug]
    ]
    children_out.sort(key=lambda c: (-c["policies"], c["name"]))

    total_policies = len(national_policies) + sum(len(p) for p in child_policies.values())

    return {
        "parent": {
            "slug": parent.slug,
            "name": parent.name,
            "iso_numeric": parent.iso_numeric,
        },
        "national": national,
        "children": children_out,
        "totals": {"sources": len(distinct_sources), "policies": total_policies},
    }


def _enabled_domains(config: ConfigLoader, overrides_store: DomainOverridesStore) -> list[dict]:
    """All enabled domains, WP-8 overlay applied — an overlay-disabled
    domain must not count as a source on the map, same seam as
    ScanManager.start_scan/estimate_cost and GET /api/domains?group=."""
    return apply_domain_overrides(
        config.get_enabled_domains("all"), overrides_store.get_all(),
    )


def _all_policies(store: PolicyStore, manager: ScanManager, **filter_kwargs) -> list[dict]:
    """Persisted policies plus in-memory scan results, deduped by URL.

    The ``get_policy_store`` singleton is a snapshot loaded once, so policies
    found by scans in this process live in the scan manager until they land in
    that snapshot. Merging both (the same thing ``/api/policies`` does) keeps
    coverage as fresh as the policy list, so the map reflects a scan's finds.

    ``filter_kwargs`` (``exclude_review_status``/``review_status_in``) is the
    public review visibility clamp (src/api/review_visibility.py) — empty for
    an admin caller or the diagnostics-only /coverage/unresolved route, so
    both stay exactly as they were before this feature.
    """
    policies = store.get_all(**filter_kwargs)
    seen = {p.get("url") for p in policies}
    for policy in manager.get_all_policies():
        p = policy.model_dump(mode="json")
        if p.get("url") in seen:
            continue
        if not passes_visibility(p, filter_kwargs):
            continue
        policies.append(p)
        seen.add(p.get("url"))
    return policies


@router.get("/coverage")
def get_coverage(
    request: Request,
    review: Optional[str] = Query(
        None, description="Non-admin view clamp: 'reviewed' or 'all'.",
    ),
    store: PolicyStore = Depends(get_policy_store),
    manager: ScanManager = Depends(get_scan_manager),
    config: ConfigLoader = Depends(get_config),
    visibility_store=Depends(get_public_visibility_store),
    overrides_store: DomainOverridesStore = Depends(get_domain_overrides_store),
):
    """Coverage aggregate for the world map: countries, supranational, totals.

    ``review`` clamps non-admin callers the same as GET /api/policies — see
    src/api/review_visibility.py.
    """
    filter_kwargs = visibility_filter_kwargs(request, review, visibility_store.get().mode)
    result = compute_coverage(
        _all_policies(store, manager, **filter_kwargs), _enabled_domains(config, overrides_store)
    )
    return {k: result[k] for k in ("countries", "supranational", "totals")}


@router.get("/coverage/children")
def get_coverage_children(
    request: Request,
    parent: str,
    review: Optional[str] = Query(
        None, description="Non-admin view clamp: 'reviewed' or 'all'.",
    ),
    store: PolicyStore = Depends(get_policy_store),
    manager: ScanManager = Depends(get_scan_manager),
    config: ConfigLoader = Depends(get_config),
    visibility_store=Depends(get_public_visibility_store),
    overrides_store: DomainOverridesStore = Depends(get_domain_overrides_store),
):
    """One country broken out by state/province: national vs. each child.

    404s for a ``parent`` slug that is not a known country jurisdiction.
    """
    filter_kwargs = visibility_filter_kwargs(request, review, visibility_store.get().mode)
    result = compute_children(
        parent, _all_policies(store, manager, **filter_kwargs), _enabled_domains(config, overrides_store)
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown country '{parent}'")
    return result


@router.get("/coverage/unresolved")
def get_coverage_unresolved(
    store: PolicyStore = Depends(get_policy_store),
    manager: ScanManager = Depends(get_scan_manager),
    config: ConfigLoader = Depends(get_config),
    overrides_store: DomainOverridesStore = Depends(get_domain_overrides_store),
):
    """Jurisdiction strings and region slugs the registry could not resolve.

    Surfaces a newly added source that forgot its registry row (or an LLM
    jurisdiction string with no alias) instead of it silently reading as
    "untracked". Both lists should be empty; the domain-slug guardrail test
    keeps the slug list empty in CI.
    """
    result = compute_coverage(
        _all_policies(store, manager), _enabled_domains(config, overrides_store)
    )
    return result["diagnostics"]
