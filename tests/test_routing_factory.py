"""Offline factory opt-in and legacy preservation; every SDK is injected."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import os
import pytest
from src.config import ConfigurationError
from src.llm.providers import get_gateway, GroqProvider
from src.llm.openai_client import OpenAIProvider
from src.llm.model_routing import ModelRoutingPolicy, RequestControl, RoutedGateway
from tests.test_providers import settings, Responses, response

def test_opt_in_routes_and_does_not_inherit_retries():
    client = SimpleNamespace(max_retries=0, responses=Responses([response()]))
    with patch.dict(os.environ, {"MODEL_ROUTING_ENABLED": "true"}, clear=True):
        gateway = get_gateway(replace(settings(), timeout_seconds=120), client=client)
    assert isinstance(gateway, RoutedGateway)
    assert gateway.gateway._settings.max_retries == 1
    assert gateway.gateway._settings.timeout_seconds == 60
    assert gateway.complete_routed("extraction", [{"role":"user","content":"offline"}],
        "optimized_extraction", {"document_id":"fake", "logical_step_id":"initial",
                                "fields":["seguradora"]}) == "OK"
    assert len(client.responses.calls) == gateway.control.http_attempts == 1
    assert client.responses.calls[0]["model"] == "gpt-5.6-luna"

def test_explicit_policy_and_durable_control_are_retained():
    control = RequestControl(3)
    policy = ModelRoutingPolicy()
    with patch.dict(os.environ, {"MODEL_ROUTING_ENABLED":"false"}, clear=True):
        value = get_gateway(settings(), client=SimpleNamespace(max_retries=0),
                            routing_policy=policy, request_control=control)
    assert value.policy is policy and value.control is control

def test_default_legacy_and_groq_selection_remain():
    with patch.dict(os.environ, {}, clear=True):
        assert isinstance(get_gateway(settings(), client=SimpleNamespace()), OpenAIProvider)
    with patch.dict(os.environ, {"MODEL_ROUTING_ENABLED":"true"}, clear=True):
        assert isinstance(get_gateway(replace(settings(), llm_provider="groq"),
                                      client=SimpleNamespace()), GroqProvider)

def test_explicit_openai_policy_cannot_dispatch_groq():
    with pytest.raises(ConfigurationError):
        get_gateway(replace(settings(), llm_provider="groq"), client=SimpleNamespace(),
                    routing_policy=ModelRoutingPolicy())

def test_enabled_profile_rejects_client_hidden_retry_before_dispatch():
    with patch.dict(os.environ, {"MODEL_ROUTING_ENABLED":"true"}, clear=True):
        with pytest.raises(ConfigurationError):
            get_gateway(settings(), client=SimpleNamespace(max_retries=2))

def test_invalid_toggle_fails_before_client_construction():
    with patch.dict(os.environ, {"MODEL_ROUTING_ENABLED":"sometimes"}, clear=True):
        with patch("src.llm.openai_client.OpenAI") as sdk, pytest.raises(ConfigurationError):
            get_gateway(settings())
        sdk.assert_not_called()

def bounded_gateway_class():
    # Load just the adapter class; importing the app would start Streamlit.
    import ast
    from pathlib import Path
    from src.llm.resilience import LLMClientError
    tree = ast.parse(Path("interface/app.py").read_text(encoding="utf-8"))
    node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BoundedLogicalGateway")
    namespace = {"LLMClientError": LLMClientError}
    exec(compile(ast.Module(body=[node],type_ignores=[]),"interface/app.py","exec"),namespace)
    return namespace["BoundedLogicalGateway"]

def test_ui_wrapper_preserves_governance_and_stops_logical_budget_before_dispatch():
    from src.llm.resilience import LLMClientError
    calls = []
    policy, control = ModelRoutingPolicy(), RequestControl()
    raw = SimpleNamespace(complete_routed=lambda *a,**kw: calls.append((a,kw)) or "OK",
                          policy=policy, control=control,record_validation=lambda *a,**kw: None)
    wrapped = bounded_gateway_class()(raw,1)
    assert wrapped.policy is policy and wrapped.control is control
    assert wrapped.complete_routed("extraction",context={"logical_step_id":"first"}) == "OK"
    with pytest.raises(LLMClientError) as caught:
        wrapped.complete_routed("extraction",context={"logical_step_id":"second"})
    assert caught.value.kind == "budget_exhausted" and len(calls) == 1
    assert wrapped.logical_calls == 1

def test_ui_legacy_wrapper_does_not_claim_routed_capability():
    wrapped = bounded_gateway_class()(SimpleNamespace(complete=lambda **kw:"OK"),1)
    assert not hasattr(wrapped,"complete_routed")
    assert wrapped.complete(model="offline") == "OK"
