"""Per-model API pricing (WP-19) - the single source of truth for both the
pre-scan estimator (``ScanManager.estimate_cost``) and the post-scan actual
cost calculation (``ClaudeClient.update_cost_estimate``), so neither module
hardcodes a price literal.

Loaded from ``config/pricing.yaml`` with the same lazy-load-once pattern as
``ConfigLoader`` (see ``_load_yaml`` in ``src/core/config.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import structlog
import yaml

logger = logging.getLogger(__name__)
_struct_logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens for one model."""

    input_per_mtok: float
    output_per_mtok: float

    def cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok
        ) / 1_000_000


class PricingLoader:
    """Loads ``config/pricing.yaml``: per-model prices + estimator assumptions.

    A missing file is tolerated (mirrors ``ConfigLoader._load_yaml``) and
    yields empty tables - ``pricing_for`` then raises rather than pricing
    anything as free.
    """

    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self._models: Optional[dict[str, ModelPrice]] = None
        self._estimator: Optional[dict] = None

    def load(self) -> None:
        path = self.config_dir / "pricing.yaml"
        data: dict = {}
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        self._models = {
            model_id: ModelPrice(
                input_per_mtok=float(entry["input_per_mtok"]),
                output_per_mtok=float(entry["output_per_mtok"]),
            )
            for model_id, entry in (data.get("models") or {}).items()
        }
        self._estimator = data.get("estimator") or {}

    @property
    def models(self) -> dict[str, ModelPrice]:
        if self._models is None:
            self.load()
        return self._models

    @property
    def estimator(self) -> dict:
        if self._estimator is None:
            self.load()
        return self._estimator

    def pricing_for(self, model_id: str) -> ModelPrice:
        """Priced entry for ``model_id``.

        An unrecognized model id (a stale config value, or a new model not
        yet added to pricing.yaml) falls back to the single most expensive
        known model rather than guessing cheap, and logs a warning so the
        gap gets noticed and pricing.yaml gets updated.
        """
        models = self.models
        if model_id in models:
            return models[model_id]
        if not models:
            raise ValueError(f"No pricing entries in {self.config_dir / 'pricing.yaml'}")

        fallback_id, fallback_price = max(
            models.items(),
            key=lambda item: item[1].input_per_mtok + item[1].output_per_mtok,
        )
        logger.warning(
            "Unknown model '%s' has no pricing.yaml entry - falling back to "
            "the most expensive known model '%s'. Add '%s' to config/pricing.yaml.",
            model_id, fallback_id, model_id,
        )
        _struct_logger.warning(
            "pricing_unknown_model_fallback",
            model_id=model_id,
            fallback_model=fallback_id,
        )
        return fallback_price
