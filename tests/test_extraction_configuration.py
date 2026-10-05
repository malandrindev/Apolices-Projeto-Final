"""Offline E1 configuration boundaries and configurable provider routing."""
import os
from unittest.mock import patch
import pytest
from src.config import (
    ConfigurationError, ExtractionOptimizationSettings, ExtractionRouting,
    get_extraction_optimization_settings, get_extraction_routing, get_settings,
)


def local_optimization(values=None):
    with patch.dict(os.environ, values or {}, clear=True), patch("src.config.load_dotenv"):
        return get_extraction_optimization_settings()


def local_settings(provider="openai", values=None):
    with patch.dict(os.environ, values or {}, clear=True), patch("src.config.load_dotenv"):
        return get_settings(provider=provider, require_api_key=False)


def local_routing(settings, values=None):
    with patch.dict(os.environ, values or {}, clear=True), patch("src.config.load_dotenv"):
        return get_extraction_routing(settings)


def test_offline_defaults_preserve_existing_model_family_until_dashboard_decision():
    settings = local_settings()
    routing = local_routing(settings)
    assert (routing.simple_model, routing.interpretation_model,
            routing.verification_model, routing.comparison_model) == (
        "gpt-5.6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-sol")
    controls = local_optimization()
    assert controls.strategy == "auto"
    assert controls.confidence_threshold == 0.75
    assert controls.auto_min_pages == 8 and controls.auto_min_chunks == 20


def test_roles_can_choose_explicit_snapshots_without_changing_provider_defaults():
    settings = local_settings()
    routing = local_routing(settings, {
        "EXTRACTION_MODEL_SIMPLE": "gpt-5.4-mini-2026-03-17",
        "EXTRACTION_MODEL_INTERPRETATION": "gpt-5.4-2026-03-05",
        "EXTRACTION_MODEL_VERIFICATION": "gpt-5.2-2025-12-11",
        "EXTRACTION_MODEL_COMPARISON": "gpt-4.1-mini-2025-04-14",
    })
    assert routing.simple_model == "gpt-5.4-mini-2026-03-17"
    assert routing.interpretation_model == "gpt-5.4-2026-03-05"
    assert routing.verification_model == "gpt-5.2-2025-12-11"
    assert routing.comparison_model == "gpt-4.1-mini-2025-04-14"
    assert settings.model_fast == "gpt-5.6-luna"
    assert settings.model_strong == "gpt-5.6-sol"


def test_groq_and_local_identifier_routes_are_not_hardcoded_to_openai():
    settings = local_settings("groq", {
        "GROQ_MODEL_FAST": "llama-fast-offline",
        "GROQ_MODEL_STRONG": "llama-strong-offline",
        "GROQ_MODEL_VISION": "vision-offline",
    })
    defaults = local_routing(settings)
    assert defaults.simple_model == "llama-fast-offline"
    assert defaults.verification_model == defaults.interpretation_model == "llama-strong-offline"
    override = local_routing(settings, {"EXTRACTION_MODEL_SIMPLE": "vendor/model-name:version"})
    assert override.simple_model == "vendor/model-name:version"


def test_empty_role_overrides_keep_backwards_compatible_fallbacks():
    settings = local_settings()
    routing = local_routing(settings, {
        "EXTRACTION_MODEL_SIMPLE": " ",
        "EXTRACTION_MODEL_INTERPRETATION": "",
        "EXTRACTION_MODEL_VERIFICATION": "\t",
        "EXTRACTION_MODEL_COMPARISON": "",
    })
    assert routing.simple_model == settings.model_fast
    assert routing.interpretation_model == routing.comparison_model == settings.model_strong
    assert routing.verification_model == settings.model_intermediate


def test_optimization_controls_are_separate_and_can_be_set_without_any_api_key():
    controls = local_optimization({
        "PUBLIC_EXTRACTION_STRATEGY": " OPTIMIZED ",
        "PUBLIC_RETRIEVAL_BATCH_CHARS": "64000",
        "PUBLIC_RETRIEVAL_INITIAL_TOP_N": "3",
        "PUBLIC_RETRIEVAL_EXPANDED_TOP_N": "8",
        "PUBLIC_EXTRACTION_CONFIDENCE_THRESHOLD": "0.9",
        "PUBLIC_EXTRACTION_AUTO_MIN_PAGES": "12",
        "PUBLIC_EXTRACTION_AUTO_MIN_CHUNKS": "40",
    })
    assert controls == ExtractionOptimizationSettings(
        strategy="optimized", batch_chars=64000, initial_top_n=3, expanded_top_n=8,
        confidence_threshold=0.9, auto_min_pages=12, auto_min_chunks=40)


@pytest.mark.parametrize("threshold", ["nan", "NaN", "inf", "-inf", "0", "-0.1", "1.01"])
def test_nonfinite_or_out_of_range_confidence_is_rejected(threshold):
    with pytest.raises(ConfigurationError):
        local_optimization({"PUBLIC_EXTRACTION_CONFIDENCE_THRESHOLD": threshold})


@pytest.mark.parametrize("values", [
    {"PUBLIC_EXTRACTION_STRATEGY": "send_entire_document"},
    {"PUBLIC_RETRIEVAL_BATCH_CHARS": "3999"},
    {"PUBLIC_RETRIEVAL_BATCH_CHARS": "64001"},
    {"PUBLIC_RETRIEVAL_BATCH_CHARS": "20000.5"},
    {"PUBLIC_RETRIEVAL_INITIAL_TOP_N": "0"},
    {"PUBLIC_RETRIEVAL_INITIAL_TOP_N": "21"},
    {"PUBLIC_RETRIEVAL_INITIAL_TOP_N": "5", "PUBLIC_RETRIEVAL_EXPANDED_TOP_N": "4"},
    {"PUBLIC_RETRIEVAL_EXPANDED_TOP_N": "101"},
    {"PUBLIC_EXTRACTION_AUTO_MIN_PAGES": "0"},
    {"PUBLIC_EXTRACTION_AUTO_MIN_CHUNKS": "-1"},
    {"PUBLIC_EXTRACTION_AUTO_MIN_CHUNKS": "inf"},
    {"PUBLIC_RETRIEVAL_BATCH_CHARS": "invalid_numeric_sentinel"},
])
def test_invalid_budget_and_retrieval_environment_cannot_reach_pipeline(values):
    with pytest.raises(ConfigurationError) as captured:
        local_optimization(values)
    assert not any(value in str(captured.value) for value in values.values()
                   if len(value) > 3)


def test_valid_boundary_threshold_does_not_require_secret_or_network():
    controls = local_optimization({
        "PUBLIC_EXTRACTION_CONFIDENCE_THRESHOLD": "1",
        "PUBLIC_RETRIEVAL_BATCH_CHARS": "4000",
        "PUBLIC_RETRIEVAL_INITIAL_TOP_N": "20",
        "PUBLIC_RETRIEVAL_EXPANDED_TOP_N": "100",
    })
    assert controls.confidence_threshold == 1
    assert controls.batch_chars == 4000
    assert controls.initial_top_n == 20 and controls.expanded_top_n == 100


@pytest.mark.parametrize("role", [
    "EXTRACTION_MODEL_SIMPLE", "EXTRACTION_MODEL_INTERPRETATION",
    "EXTRACTION_MODEL_VERIFICATION", "EXTRACTION_MODEL_COMPARISON",
])
def test_invalid_model_role_identifier_has_safe_message(role):
    settings = local_settings()
    invalid = "not a model\nprivate_metadata_sentinel"
    with pytest.raises(ConfigurationError) as captured:
        local_routing(settings, {role: invalid})
    assert "private_metadata_sentinel" not in str(captured.value)


def test_routing_works_with_legacy_settings_having_no_intermediate_model():
    settings = local_settings("groq", {
        "GROQ_MODEL_FAST": "fast-offline", "GROQ_MODEL_STRONG": "strong-offline",
        "GROQ_MODEL_VISION": "vision-offline",
    })
    from dataclasses import replace
    old = replace(settings, model_intermediate="")
    routing = local_routing(old)
    assert routing.verification_model == "strong-offline"
    explicit = ExtractionRouting("router-offline", "semantic-offline", "verify-offline")
    assert explicit.comparison_model == ""

@pytest.mark.parametrize("stage,model,expected_reasoning", [
    ("optimized_extraction", "gpt-5.4-mini-2026-03-17", "none"),
    ("optimized_interpretation", "gpt-5.4-2026-03-05", "low"),
    ("optimized_verification", "gpt-5.2-2025-12-11", "low"),
    ("optimized_extraction", "gpt-5.6-luna", "none"),
    ("optimized_extraction", "gpt-4.1-mini-2025-04-14", None),
])
def test_optimized_roles_use_compact_strict_schema_and_compatible_requests_offline(
        stage, model, expected_reasoning):
    import json
    from types import SimpleNamespace
    from src.llm.openai_client import OpenAIProvider
    settings = local_settings()
    captured = []
    def create(**params):
        captured.append(params)
        return SimpleNamespace(status="completed", output_text='{"fields":[]}',
            usage=SimpleNamespace(input_tokens=20, output_tokens=10,
                                  input_tokens_details=SimpleNamespace(cached_tokens=0)))
    provider = OpenAIProvider(settings, client=SimpleNamespace(
        responses=SimpleNamespace(create=create)))
    text = provider.complete(model=model, agent=stage, max_tokens=4500,
        messages=[{"role": "user", "content": "offline schema contract"}],
        response_format={"type": "json_object"})
    assert json.loads(text) == {"fields": []}
    assert len(captured) == 1
    params = captured[0]
    assert params["model"] == model and params["store"] is False
    assert params["max_output_tokens"] == 4500
    assert not any(name in params for name in ("tools", "tool_choice", "service_tier", "temperature"))
    if expected_reasoning is None:
        assert "reasoning" not in params
    else:
        assert params["reasoning"] == {"effort": expected_reasoning}
    schema = params["text"]["format"]["schema"]
    assert params["text"]["format"]["strict"] is True
    assert set(schema["properties"]) == {"fields"}
    def validate_strict(node):
        if isinstance(node, dict):
            assert "default" not in node
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node.get("properties", {}))
            for value in node.values():
                validate_strict(value)
        elif isinstance(node, list):
            for value in node:
                validate_strict(value)
    validate_strict(schema)
    assert set(schema["$defs"]["FieldStatus"]["enum"]) == {
        "FOUND", "NOT_FOUND", "NOT_RETRIEVED", "AMBIGUOUS"}


def test_legacy_extraction_schema_and_original_policy_stay_backwards_compatible():
    from src.llm.openai_client import schema_for_stage
    from src.schemas.policy import PolicyExtraction
    from src.agents.extraction import Phase2Report
    schema = schema_for_stage("extraction")
    assert set(schema["properties"]) == set(PolicyExtraction.model_fields)
    assert len(schema["properties"]) == 27
    report = Phase2Report(source_name="legacy.pdf", sha256="a"*64,
                          clause_count=0, policy=PolicyExtraction())
    assert report.field_status == {} and report.retrieval_diagnostics == {}
