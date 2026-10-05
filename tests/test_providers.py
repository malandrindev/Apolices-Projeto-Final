"""Offline provider, safety and bounded recovery checks; no inference network."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import json, os
import httpx
import httpx2
import pytest
from openai import RateLimitError, AuthenticationError, APITimeoutError
from groq import RateLimitError as GroqRateLimitError
from src.config import Settings, get_settings, ConfigurationError
from src.llm.providers import get_gateway, GroqProvider
from src.llm.openai_client import OpenAIProvider, schema_for_stage
from src.llm.resilience import LLMClientError

def settings():
    return Settings(groq_api_key="dummy",model_fast="gpt-5.6-luna",model_strong="gpt-5.6-sol",model_vision="unused",temperature=0,max_tokens=4096,timeout_seconds=1,max_retries=3,llm_provider="openai",openai_api_key="dummy",model_intermediate="gpt-5.6-terra")

class Responses:
    def __init__(self, results):
        self.results=list(results)
        self.calls=[]
    def create(self,**kwargs):
        self.calls.append(kwargs)
        result=self.results.pop(0)
        if isinstance(result,Exception):raise result
        return result

def response(text="OK",status="completed",*,cached_tokens=None):
    usage=SimpleNamespace(input_tokens=15,output_tokens=5)
    if cached_tokens is not None:
        usage.input_tokens_details=SimpleNamespace(cached_tokens=cached_tokens)
    return SimpleNamespace(output_text=text,status=status,usage=usage)

def provider(results):
    client=SimpleNamespace(responses=Responses(results))
    return OpenAIProvider(settings(),client=client),client

def rate_error(code="",message="",after=None):
    request=httpx2.Request("POST","https://api.openai.com/v1/responses")
    headers={} if after is None else {"retry-after":str(after)}
    reply=httpx2.Response(429,request=request,headers=headers)
    return RateLimitError("simulated",response=reply,body={"error":{"code":code,"message":message}})

def call(gateway,**kwargs):
    return gateway.complete(model=settings().model_fast,messages=[{"role":"user","content":"JSON test"}],agent="extraction",**kwargs)

def test_openai_responses_schema_and_usage():
    gateway,client=provider([response("{}")])
    assert call(gateway,response_format={"type":"json_object"})=="{}"
    params=client.responses.calls[0]
    assert params["store"] is False
    assert "temperature" not in params
    assert not any(key in params for key in ("tools","tool_choice","function_call","service_tier"))
    assert params["reasoning"]=={"effort":"none"}
    schema=params["text"]["format"]
    assert schema["strict"] is True and schema["type"]=="json_schema"
    assert gateway.usage=={"calls":1,"prompt_tokens":15,"completion_tokens":5,"cached_tokens":None}
    assert gateway.events[0]["cached_tokens"] is None
    assert not any(key in gateway.events[0] for key in ("messages","prompt","content","api_key"))

@pytest.mark.parametrize("stage",["extraction","segmentation","comparison","rag"])
def test_strict_schema_makes_all_object_fields_required(stage):
    schema=schema_for_stage(stage)
    def visit(node):
        if isinstance(node,dict):
            if node.get("type")=="object":
                assert node["additionalProperties"] is False
                assert set(node["required"])==set(node.get("properties",{}))
            for value in node.values():visit(value)
        elif isinstance(node,list):
            for value in node:visit(value)
    visit(schema)

@pytest.mark.parametrize("code,kind",[("insufficient_quota","quota"),("billing_hard_limit_reached","credit")])
def test_quota_and_credit_do_not_retry(code,kind):
    gateway,client=provider([rate_error(code=code)])
    with patch("src.llm.openai_client.time.sleep") as sleep,pytest.raises(LLMClientError) as caught:
        call(gateway)
    assert caught.value.kind==kind and len(client.responses.calls)==1
    sleep.assert_not_called()

def test_retry_after_respected_and_attempts_bounded():
    gateway,client=provider([rate_error(after=2),response()])
    with patch("src.llm.openai_client.time.sleep") as sleep:
        assert call(gateway)=="OK"
    sleep.assert_called_once_with(2)
    assert gateway.usage["calls"]==2
    assert [event["attempt"] for event in gateway.events]==[1,2]

def test_long_retry_after_exits_without_sleep():
    gateway,client=provider([rate_error(after=3600)])
    with patch("src.llm.openai_client.time.sleep") as sleep,pytest.raises(LLMClientError) as caught:
        call(gateway)
    assert caught.value.retry_after==3600
    sleep.assert_not_called()
    assert len(client.responses.calls)==1

def test_retry_stops_at_three_attempts():
    gateway,client=provider([rate_error(),rate_error(),rate_error()])
    with patch("src.llm.openai_client.time.sleep"),pytest.raises(LLMClientError):
        call(gateway)
    assert len(client.responses.calls)==3

@pytest.mark.parametrize("content,status,kind",[("","completed","response_empty"),("{}","incomplete","response_incomplete")])
def test_empty_incomplete_not_accepted(content,status,kind):
    gateway,_=provider([response(content,status)])
    with pytest.raises(LLMClientError) as caught:call(gateway)
    assert caught.value.kind==kind
    assert gateway.usage["completion_tokens"]==5

def test_invalid_json_not_accepted():
    gateway,_=provider([response("not json")])
    with pytest.raises(LLMClientError):call(gateway,response_format={"type":"json_object"})

def test_selection_and_secret_repr():
    assert isinstance(get_gateway(settings(),client=SimpleNamespace()),OpenAIProvider)
    assert isinstance(get_gateway(replace(settings(),llm_provider="groq"),client=SimpleNamespace()),GroqProvider)
    assert "dummy" not in repr(settings())

def test_openai_config_without_groq_key():
    with patch.dict(os.environ,{"LLM_PROVIDER":"openai","OPENAI_API_KEY":"dummy"},clear=True),patch("src.config.load_dotenv"):
        value=get_settings()
    assert value.model_fast=="gpt-5.6-luna" and value.model_intermediate=="gpt-5.6-terra" and value.model_strong=="gpt-5.6-sol"
    assert not value.groq_api_key

def test_unknown_provider_rejected():
    with patch.dict(os.environ,{"LLM_PROVIDER":"unsupported"},clear=True),patch("src.config.load_dotenv"),pytest.raises(ConfigurationError):
        get_settings()

def test_groq_quota_stops_without_sleep_or_body_leak():
    request=httpx.Request("POST","https://api.groq.com/openai/v1/chat/completions")
    error=GroqRateLimitError("private body",response=httpx.Response(429,request=request),body={"error":{"code":"insufficient_quota","message":"private policy text"}})
    completions=SimpleNamespace(create=lambda **kwargs: (_ for _ in ()).throw(error))
    gateway=GroqProvider(replace(settings(),llm_provider="groq"),client=SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    with pytest.raises(LLMClientError) as caught:call(gateway)
    assert caught.value.kind=="quota" and gateway.usage["calls"]==1
    assert "private" not in str(caught.value)

@pytest.mark.parametrize("counts,total", [([0,0],0),([4,6],10),([None,6],None),([4,None],None),([-1,2],None),([True,2],None)])
def test_openai_cached_tokens_known_zero_and_unknown(counts,total):
    gateway,client=provider([response(cached_tokens=count) for count in counts])
    for _ in counts:
        assert call(gateway)=="OK"
    assert gateway.usage=={"calls":2,"prompt_tokens":30,"completion_tokens":10,"cached_tokens":total}
    assert [event["cached_tokens"] for event in gateway.events]==[
        count if isinstance(count,int) and not isinstance(count,bool) and count>=0 else None
        for count in counts
    ]
    assert all(not any(key in params for key in ("tools","tool_choice","function_call","service_tier")) for params in client.responses.calls)


def test_failed_attempt_keeps_cache_total_unknown():
    gateway,_=provider([rate_error(after=0),response(cached_tokens=5)])
    with patch("src.llm.openai_client.time.sleep"):
        assert call(gateway)=="OK"
    assert gateway.usage["cached_tokens"] is None
    assert [event["cached_tokens"] for event in gateway.events]==[None,5]


def test_safe_response_metadata_never_changes_request():
    result=response(cached_tokens=3)
    result.id="resp_test-123"
    result._request_id="req_test-456"
    result.service_tier="default"
    gateway,client=provider([result])
    call(gateway)
    event=gateway.events[0]
    assert event["response_id"]=="resp_test-123"
    assert event["request_id"]=="req_test-456"
    assert event["effective_service_tier"]=="default"
    assert "service_tier" not in client.responses.calls[0]
    unsafe=response(cached_tokens=0)
    unsafe.id="sk-proj-x"
    unsafe._request_id="gsk_x"
    unsafe.service_tier="private arbitrary text"
    gateway,_=provider([unsafe])
    call(gateway)
    assert not any(key in gateway.events[0] for key in ("response_id","request_id","effective_service_tier"))


@pytest.mark.parametrize("counts,total", [([0,2],2),([None,2],None),([2,None],None)])
def test_groq_cached_tokens_preserves_legacy_usage(counts,total):
    results=[]
    for count in counts:
        usage=SimpleNamespace(prompt_tokens=15,completion_tokens=5)
        if count is not None:
            usage.prompt_tokens_details=SimpleNamespace(cached_tokens=count)
        results.append(SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            usage=usage,
        ))
    completions=Responses(results)
    gateway=GroqProvider(replace(settings(),llm_provider="groq"),client=SimpleNamespace(chat=SimpleNamespace(completions=completions)))
    for _ in counts:
        assert call(gateway)=="OK"
    assert gateway.usage=={"calls":2,"prompt_tokens":30,"completion_tokens":10,"cached_tokens":total}
    assert [event["cached_tokens"] for event in gateway.events]==counts
    assert all(not any(key in params for key in ("tools","tool_choice","function_call","service_tier")) for params in completions.calls)
