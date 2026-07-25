"""GET /api/keywords and PUT /api/keywords/overrides (WP-10).

GET returns the merged (YAML + kv overlay) keyword config as structured
JSON — categories (name, weight, description, terms per language),
thresholds, exclusions, plus ``url_bonuses``/``stricter_requirements``
passed through as-is (not overlay-editable in this phase) — and a separate
``overrides`` section so the UI can show what's custom vs. YAML.

PUT replaces the overlay wholesale after validating: every category exists
in the loaded config, every language is among the 20 keywords.yaml ships
with, every term is a non-empty string <=80 chars, and threshold overrides
are within sane ranges. Setting empty categories/thresholds clears the
overlay. Non-GET, so gated by AdminGateMiddleware.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..deps import get_config, get_keyword_overrides_store, request_is_admin
from ...core.config import ConfigLoader
from ...core.keywords import VALID_KEYWORD_LANGUAGES
from ...core.overrides import apply_keyword_overrides
from ...storage.keyword_overrides import KeywordOverridesStore

router = APIRouter(prefix="/api/keywords", tags=["keywords"])

TermStr = Annotated[str, Field(min_length=1, max_length=80)]


@router.get("")
def get_keywords(
    request: Request,
    config: ConfigLoader = Depends(get_config),
    overrides_store: KeywordOverridesStore = Depends(get_keyword_overrides_store),
):
    """Merged keyword config, admin-only.

    This is a GET, so AdminGateMiddleware doesn't cover it (mirrors GET
    /api/policies/library and GET /api/sources/status) — checked here.
    """
    if not request_is_admin(request):
        raise HTTPException(status_code=403, detail="Administrator access required")

    overrides = overrides_store.get()
    merged = apply_keyword_overrides(config.keywords_config, overrides)

    categories = {
        name: {
            "weight": cfg.get("weight", 1.0),
            "description": cfg.get("description", ""),
            "terms": cfg.get("terms", {}),
        }
        for name, cfg in merged.get("keywords", {}).items()
    }

    return {
        "categories": categories,
        "thresholds": merged.get("thresholds", {}),
        "exclusions": merged.get("exclusions", []),
        "url_bonuses": merged.get("url_bonuses", {}),
        "stricter_requirements": merged.get("stricter_requirements", {}),
        "overrides": overrides,
    }


class KeywordTermOverride(BaseModel):
    added: list[TermStr] = Field(default_factory=list)
    removed: list[TermStr] = Field(default_factory=list)


class KeywordThresholdOverrides(BaseModel):
    minimum_keyword_score: Optional[float] = Field(default=None, ge=0, le=50)
    minimum_matches: Optional[int] = Field(default=None, ge=0, le=20)


class KeywordOverridesUpdate(BaseModel):
    categories: dict[str, dict[str, KeywordTermOverride]] = Field(default_factory=dict)
    thresholds: KeywordThresholdOverrides = Field(default_factory=KeywordThresholdOverrides)


def _validate_against_config(payload: KeywordOverridesUpdate, config: ConfigLoader) -> list[str]:
    """Semantic checks pydantic's field constraints can't express: category
    must exist in the loaded config, language must be one of the 20."""
    valid_categories = set(config.keywords_config.get("keywords", {}).keys())
    errors = []
    for category, by_language in payload.categories.items():
        if category not in valid_categories:
            errors.append(f"Unknown category: '{category}'")
            continue
        for language in by_language:
            if language not in VALID_KEYWORD_LANGUAGES:
                errors.append(f"Unknown language '{language}' for category '{category}'")
    return errors


@router.put("/overrides")
def update_keyword_overrides(
    payload: KeywordOverridesUpdate,
    config: ConfigLoader = Depends(get_config),
    overrides_store: KeywordOverridesStore = Depends(get_keyword_overrides_store),
):
    errors = _validate_against_config(payload, config)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    stored = {
        "categories": {
            category: {
                language: {"added": delta.added, "removed": delta.removed}
                for language, delta in by_language.items()
            }
            for category, by_language in payload.categories.items()
        },
        "thresholds": payload.thresholds.model_dump(exclude_none=True),
    }
    return overrides_store.update(stored)
