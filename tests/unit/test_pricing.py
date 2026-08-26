"""Tests for src.core.pricing (WP-19) - the single source of truth for API
pricing, shared by ScanManager.estimate_cost() and
ClaudeClient.update_cost_estimate() so neither hardcodes a price literal.
"""

import logging
from pathlib import Path

import pytest

from src.core.pricing import ModelPrice, PricingLoader


def _write_pricing_yaml(config_dir: Path, contents: str) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "pricing.yaml").write_text(contents, encoding="utf-8")


_SAMPLE_YAML = """
models:
  cheap-model:
    input_per_mtok: 1.0
    output_per_mtok: 2.0
  expensive-model:
    input_per_mtok: 10.0
    output_per_mtok: 20.0
estimator:
  screening_input: 111
  screening_output: 22
  structured_items_per_source: 40
"""


@pytest.mark.small
class TestModelPrice:
    def test_cost_usd_combines_input_and_output(self):
        price = ModelPrice(input_per_mtok=1.0, output_per_mtok=2.0)
        # 1,000,000 input tokens @ $1/Mtok + 1,000,000 output @ $2/Mtok
        assert price.cost_usd(1_000_000, 1_000_000) == pytest.approx(3.0)

    def test_cost_usd_zero_tokens_is_zero(self):
        price = ModelPrice(input_per_mtok=5.0, output_per_mtok=25.0)
        assert price.cost_usd(0, 0) == 0.0


@pytest.mark.medium
class TestPricingLoaderKnownModel:
    def test_pricing_for_known_model(self, tmp_path):
        _write_pricing_yaml(tmp_path, _SAMPLE_YAML)
        loader = PricingLoader(config_dir=str(tmp_path))

        price = loader.pricing_for("cheap-model")

        assert price.input_per_mtok == 1.0
        assert price.output_per_mtok == 2.0

    def test_models_property_lists_every_entry(self, tmp_path):
        _write_pricing_yaml(tmp_path, _SAMPLE_YAML)
        loader = PricingLoader(config_dir=str(tmp_path))

        assert set(loader.models) == {"cheap-model", "expensive-model"}

    def test_estimator_values_loaded(self, tmp_path):
        _write_pricing_yaml(tmp_path, _SAMPLE_YAML)
        loader = PricingLoader(config_dir=str(tmp_path))

        assert loader.estimator["screening_input"] == 111
        assert loader.estimator["structured_items_per_source"] == 40


@pytest.mark.medium
class TestPricingLoaderUnknownModelFallback:
    """An unrecognized model id must never be priced as if it were cheap."""

    def test_unknown_model_falls_back_to_most_expensive(self, tmp_path):
        _write_pricing_yaml(tmp_path, _SAMPLE_YAML)
        loader = PricingLoader(config_dir=str(tmp_path))

        price = loader.pricing_for("some-future-model-id")

        assert price.input_per_mtok == 10.0
        assert price.output_per_mtok == 20.0

    def test_unknown_model_logs_a_warning(self, tmp_path, caplog):
        _write_pricing_yaml(tmp_path, _SAMPLE_YAML)
        loader = PricingLoader(config_dir=str(tmp_path))

        with caplog.at_level(logging.WARNING):
            loader.pricing_for("some-future-model-id")

        assert any(
            "pricing_unknown_model_fallback" in r.message
            or "unknown" in r.message.lower()
            for r in caplog.records
        )

    def test_no_models_configured_raises(self, tmp_path):
        _write_pricing_yaml(tmp_path, "models: {}\nestimator: {}\n")
        loader = PricingLoader(config_dir=str(tmp_path))

        with pytest.raises(ValueError):
            loader.pricing_for("anything")


@pytest.mark.medium
class TestPricingLoaderMissingFile:
    def test_missing_pricing_yaml_yields_empty_models(self, tmp_path):
        loader = PricingLoader(config_dir=str(tmp_path))
        assert loader.models == {}
        assert loader.estimator == {}


@pytest.mark.small
class TestRealPricingFile:
    """The repo's real config/pricing.yaml must actually load and price the
    two models the pipeline is configured to use."""

    def test_real_pricing_yaml_prices_haiku_and_sonnet(self):
        loader = PricingLoader(config_dir="config")
        haiku = loader.pricing_for("claude-haiku-4-5-20251001")
        sonnet = loader.pricing_for("claude-sonnet-4-6")

        assert haiku.input_per_mtok == 1.00
        assert haiku.output_per_mtok == 5.00
        assert sonnet.input_per_mtok == 3.00
        assert sonnet.output_per_mtok == 15.00

    def test_real_pricing_yaml_has_estimator_assumptions(self):
        loader = PricingLoader(config_dir="config")
        estimator = loader.estimator

        assert estimator["screening_input"] == 2000
        assert estimator["screening_output"] == 50
        assert estimator["analysis_input"] == 20000
        assert estimator["analysis_output"] == 1000
        assert estimator["auditor_input"] == 5000
        assert estimator["auditor_output"] == 2000
        assert estimator["structured_items_per_source"] == 40


# ---------------------------------------------------------------------------
# Source hygiene - the stale hardcoded literals must be gone from the two
# call sites that used to hardcode them.
# ---------------------------------------------------------------------------

@pytest.mark.small
class TestNoHardcodedPriceLiterals:
    """0.25/1.25 were the stale Haiku $/MTok literals hardcoded in both
    scan_manager.py's estimate_cost() and llm.py's update_cost_estimate().
    Both must now read prices from PricingLoader instead."""

    def test_scan_manager_has_no_stale_haiku_literals(self):
        text = Path("src/orchestration/scan_manager.py").read_text(encoding="utf-8")
        assert "0.25" not in text
        assert "1.25" not in text

    def test_llm_has_no_stale_haiku_literals(self):
        text = Path("src/core/llm.py").read_text(encoding="utf-8")
        assert "0.25" not in text
        assert "1.25" not in text
        assert "HAIKU_INPUT" not in text
        assert "HAIKU_OUTPUT" not in text
