"""Email notification subscriptions and status (WP-44) - GET/POST/PUT/DELETE
/api/notifications/subscriptions, GET /api/notifications/status.

GET routes are admin-gated in-route (AdminGateMiddleware only covers
non-GET /api requests) - same pattern as GET /api/signals/status,
/api/schedules. POST/PUT/DELETE are covered by that middleware
automatically.
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from ..deps import (
    get_mailer, get_notification_state_store, get_notification_subscriptions_store,
    request_is_admin,
)
from ...notifications.mailer import Mailer
from ...storage.notifications import (
    FREQUENCIES, TOPICS, DuplicateEmailError, InvalidFrequencyError, InvalidTopicsError,
    NotificationStateStore, NotificationSubscriptionsStore,
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# No new dependency for email validation - a pragmatic shape check (one
# '@', at least one '.' after it, no whitespace) rather than full RFC 5322.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _validate_email(value: str) -> str:
    if not _EMAIL_RE.match(value):
        raise ValueError(f"'{value}' is not a valid email address.")
    return value


def _validate_topics(value: Optional[list[str]]) -> Optional[list[str]]:
    if value is None:
        return value
    if not value:
        raise ValueError("At least one topic must be selected.")
    invalid = sorted(set(value) - set(TOPICS))
    if invalid:
        raise ValueError(f"Invalid topic(s): {invalid}. Valid values: {sorted(TOPICS)}")
    return value


def _validate_frequency(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    if value not in FREQUENCIES:
        raise ValueError(f"Invalid frequency {value!r}. Valid values: {sorted(FREQUENCIES)}")
    return value


class SubscriptionCreate(BaseModel):
    email: str
    topics: list[str]
    frequency: str

    _check_email = field_validator("email")(_validate_email)
    _check_topics = field_validator("topics")(_validate_topics)
    _check_frequency = field_validator("frequency")(_validate_frequency)


class SubscriptionUpdate(BaseModel):
    topics: Optional[list[str]] = None
    frequency: Optional[str] = None

    _check_topics = field_validator("topics")(_validate_topics)
    _check_frequency = field_validator("frequency")(_validate_frequency)


def _public(sub: dict) -> dict:
    return {
        "id": sub["id"], "email": sub["email"],
        "topics": sub["topics"], "frequency": sub["frequency"],
    }


@router.get("/subscriptions")
def list_subscriptions(
    request: Request,
    store: NotificationSubscriptionsStore = Depends(get_notification_subscriptions_store),
):
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")
    return {"subscriptions": [_public(s) for s in store.list()]}


@router.post("/subscriptions")
def create_subscription(
    payload: SubscriptionCreate,
    store: NotificationSubscriptionsStore = Depends(get_notification_subscriptions_store),
):
    try:
        sub = store.create(email=payload.email, topics=payload.topics, frequency=payload.frequency)
    except DuplicateEmailError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except (InvalidTopicsError, InvalidFrequencyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _public(sub)


@router.put("/subscriptions/{subscription_id}")
def update_subscription(
    subscription_id: str,
    payload: SubscriptionUpdate,
    store: NotificationSubscriptionsStore = Depends(get_notification_subscriptions_store),
):
    fields = payload.model_dump(exclude_unset=True)
    try:
        updated = store.update(subscription_id, **fields)
    except (InvalidTopicsError, InvalidFrequencyError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Subscription '{subscription_id}' not found")
    return _public(updated)


@router.delete("/subscriptions/{subscription_id}")
def delete_subscription(
    subscription_id: str,
    store: NotificationSubscriptionsStore = Depends(get_notification_subscriptions_store),
):
    if not store.delete(subscription_id):
        raise HTTPException(status_code=404, detail=f"Subscription '{subscription_id}' not found")
    return {"status": "deleted", "id": subscription_id}


@router.get("/status")
def notifications_status(
    request: Request,
    mailer: Mailer = Depends(get_mailer),
    state: NotificationStateStore = Depends(get_notification_state_store),
):
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")
    last_send_error = state.get_last_send_error()
    return {
        "smtp_configured": mailer.smtp_configured,
        "last_digest": state.get_last_digest(),
        "last_send_error": last_send_error["error"] if last_send_error else None,
    }
