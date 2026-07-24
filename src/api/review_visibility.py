"""Shared non-admin review-status clamping for public read endpoints (WP-3
"public review visibility"). Used by GET /api/policies, GET
/api/policies/search, GET /api/coverage, and GET /api/coverage/children.

Rejected policies are never returned, counted, or shaded for a non-admin
caller, regardless of any parameter. The coarse ``?review=`` query param
(``reviewed`` | ``all``) plus the admin's posture setting decide the rest;
admins are exempt entirely — unclamped, exactly as before this feature.
"""

from typing import Optional

from fastapi import Request

from .deps import request_is_admin


def effective_review_view(review: Optional[str], posture: str) -> str:
    """'reviewed' or 'all' for a non-admin request.

    ``reviewed_only`` clamps to 'reviewed' no matter what was asked.
    Otherwise an explicit ``review=reviewed`` wins; anything else (absent,
    or 'all') defaults to 'all' — the posture's "default" distinction is a
    frontend/initial-toggle concern, not a backend one: the frontend sends
    the param explicitly based on the toggle position.
    """
    if posture == "reviewed_only":
        return "reviewed"
    return "reviewed" if review == "reviewed" else "all"


def visibility_filter_kwargs(request: Request, review: Optional[str], posture: str) -> dict:
    """PolicyStore.get_all/search/search_text kwargs enforcing the posture.

    Admins are never *forced* into a clamp — no param means fully unclamped,
    exactly as before this feature — but an explicit ``review=reviewed`` is
    an intentional narrowing and is honored for everyone, so the public
    toggle behaves identically whether or not the caller counts as admin
    (open-mode loopback demos included). Non-admins always exclude rejected;
    a 'reviewed' effective view narrows to promoted-only. Never both keys at
    once, so this always safely combines with a store call via ``**kwargs``.
    """
    if request_is_admin(request):
        if review == "reviewed":
            return {"review_status_in": ["promoted"]}
        return {}
    if effective_review_view(review, posture) == "reviewed":
        return {"review_status_in": ["promoted"]}
    return {"exclude_review_status": "rejected"}


def passes_visibility(policy: dict, filter_kwargs: dict) -> bool:
    """Apply the same clamp to an in-memory (not-yet-persisted) policy dict.

    Mirrors the SQL a matching ``filter_kwargs`` would apply, so freshly
    scanned policies (review_status='new') merged in from ScanManager see
    the identical rule as what's already in the store.
    """
    status = policy.get("review_status", "new")
    excluded = filter_kwargs.get("exclude_review_status")
    if excluded and status == excluded:
        return False
    review_status_in = filter_kwargs.get("review_status_in")
    if review_status_in and status not in review_status_in:
        return False
    return True
