"""OpenAI Responses adapter with bounded retries, strict schema and safe telemetry."""
from __future__ import annotations
import argparse, copy, json, time
from typing import Any, Callable
from openai import OpenAI, APIError
from src.config import Settings, ConfigurationError, get_settings
from src.llm.resilience import LLMClientError, classify_error, retry_after_seconds, emit_event, cached_tokens_from_usage, response_metadata, reported_token_count, repair_json_locally

def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    schema=copy.deepcopy(schema)
    def visit(node: Any) -> None:
        if isinstance(node,dict):
            node.pop("default",None)
            if node.get("type") == "object":
                node["additionalProperties"]=False
                node["required"]=list(node.get("properties",{}))
            for value in list(node.values()):visit(value)
        elif isinstance(node,list):
            for value in node:visit(value)
    visit(schema)
    return schema

def schema_for_stage(stage: str) -> dict[str, Any] | None:
    name=stage.lower()
    if name in {"optimized_extraction", "optimized_interpretation", "optimized_verification"}:
        from src.schemas.retrieval import GroupExtractionResponse
        return strict_schema(GroupExtractionResponse.model_json_schema())
    if "extract" in name:
        from src.schemas.policy import PolicyExtraction
        return strict_schema(PolicyExtraction.model_json_schema())
    if "segment" in name or "classif" in name:
        from src.schemas.clause import ClauseClassificationBatch
        return strict_schema(ClauseClassificationBatch.model_json_schema())
    if "compar" in name or "semantic" in name:
        from src.schemas.comparison import SemanticDecisionBatch
        return strict_schema(SemanticDecisionBatch.model_json_schema())
    if "rag" in name:
        from src.schemas.rag import RagModelAnswer
        return strict_schema(RagModelAnswer.model_json_schema())
    return None

class OpenAIProvider:
    provider = "openai"
    def __init__(self, settings: Settings, client: Any = None, event_callback: Callable | None = None,
                 local_json_repair: bool = False, response_callback: Callable | None = None,
                 result_callback: Callable | None = None, request_callback: Callable | None = None):
        self.response_callback=response_callback
        self.result_callback=result_callback
        self.request_callback=request_callback
        self._settings=settings
        self._local_json_repair=local_json_repair
        self._client=client or OpenAI(api_key=settings.openai_api_key,timeout=settings.timeout_seconds,max_retries=0)
        self._calls=self._input_tokens=self._output_tokens=0
        self._cached_tokens: int | None = 0
        self.events:list[dict[str,Any]]=[]
        self.event_callback=event_callback
    @property
    def usage(self) -> dict[str, int | None]:
        return {"calls":self._calls,"prompt_tokens":self._input_tokens,"completion_tokens":self._output_tokens,"cached_tokens":self._cached_tokens}
    def complete(self, *, model:str, messages:list[dict[str,str]], agent:str, max_tokens:int|None=None, temperature:float|None=None, response_format:dict[str,Any]|None=None) -> str:
        parameters:dict[str,Any]={"model":model,"input":messages,"max_output_tokens":max_tokens or self._settings.max_tokens,"store":False}
        # Reasoning-none keeps extraction inexpensive. Temperature omitted for model compatibility.
        if model.startswith(("gpt-5.6", "gpt-5.4", "gpt-5.2")):
            parameters["reasoning"]={"effort":"none" if model==self._settings.model_fast or agent=="optimized_extraction" else self._settings.openai_reasoning_effort}
        if response_format:
            schema=schema_for_stage(agent)
            parameters["text"]={"format":{"type":"json_schema","name":"result","schema":schema,"strict":True} if schema else {"type":"json_object"}}
        for attempt in range(1,self._settings.max_retries+1):
            started=time.perf_counter()
            self._calls+=1
            try:
                if self.request_callback:
                    try:
                        self.request_callback(parameters)
                    except Exception:
                        raise LLMClientError(self.provider, "persistence_error") from None
                response=self._client.responses.create(**parameters)
            except APIError as error:
                self._cached_tokens = None
                kind=classify_error(error)
                delay=retry_after_seconds(error)
                emit_event(self,model,agent,started,attempt,"error",kind=kind,**response_metadata(error))
                retryable=kind in {"RPM","TPM","rate_limit","timeout","provider_error"}
                if not retryable or attempt==self._settings.max_retries or (delay is not None and delay>self._settings.max_retry_wait_seconds):
                    raise LLMClientError(self.provider,kind,retry_after=delay) from None
                time.sleep(delay if delay is not None else min(2**(attempt-1),self._settings.max_retry_wait_seconds))
                continue
            if self.response_callback:
                received_usage = getattr(response, "usage", None)
                receipt = {"provider": self.provider, "model": model, "stage": agent,
                           "status": getattr(response, "status", "completed"),
                           "duration_ms": int((time.perf_counter() - started) * 1000),
                           "input_tokens": reported_token_count(received_usage, "input_tokens"),
                           "output_tokens": reported_token_count(received_usage, "output_tokens"),
                           "cached_tokens": cached_tokens_from_usage(received_usage, "input_tokens_details"),
                           "total_tokens": reported_token_count(received_usage, "total_tokens"),
                           **response_metadata(response)}
                try:
                    self.response_callback(receipt)
                except Exception:
                    raise LLMClientError(self.provider, "persistence_error") from None
            if self.result_callback:
                try:
                    self.result_callback({"output_text": getattr(response, "output_text", ""),
                                          "status": getattr(response, "status", "completed"),
                                          **response_metadata(response)})
                except Exception:
                    raise LLMClientError(self.provider, "persistence_error") from None
            usage=getattr(response,"usage",None)
            input_tokens=reported_token_count(usage,"input_tokens")
            output_tokens=reported_token_count(usage,"output_tokens")
            total_tokens=reported_token_count(usage,"total_tokens")
            if total_tokens is None and input_tokens is not None and output_tokens is not None:
                total_tokens=input_tokens+output_tokens
            self._input_tokens+=input_tokens or 0
            self._output_tokens+=output_tokens or 0
            cached_tokens = cached_tokens_from_usage(usage, "input_tokens_details")
            self._cached_tokens = None if cached_tokens is None or self._cached_tokens is None else self._cached_tokens + cached_tokens
            status=getattr(response,"status","completed")
            content=getattr(response,"output_text","")
            local_json_repair=False
            if self._local_json_repair and status=="completed" and response_format and isinstance(content,str):
                try:json.loads(content)
                except (ValueError,TypeError):
                    repaired=repair_json_locally(content)
                    if repaired is not None:
                        content=repaired
                        local_json_repair=True
            emit_event(self,model,agent,started,attempt,status,input_tokens=input_tokens,output_tokens=output_tokens,cached_tokens=cached_tokens,total_tokens=total_tokens,local_json_repair=local_json_repair,**response_metadata(response))
            if status!="completed":raise LLMClientError(self.provider,"response_incomplete")
            if not isinstance(content,str) or not content.strip():raise LLMClientError(self.provider,"response_empty")
            if response_format:
                try:json.loads(content)
                except json.JSONDecodeError:raise LLMClientError(self.provider,"invalid_response") from None
            return content.strip()
        raise LLMClientError(self.provider,"provider_error")

def main() -> int:
    parser=argparse.ArgumentParser(description="One neutral OpenAI Responses smoke call.")
    parser.add_argument("--ping",action="store_true")
    args=parser.parse_args()
    if not args.ping:parser.print_help();return 0
    try:
        settings=get_settings()
        if settings.llm_provider!="openai":raise ConfigurationError("Defina LLM_PROVIDER=openai.")
        provider=OpenAIProvider(settings)
        result=provider.complete(model=settings.model_fast,messages=[{"role":"user","content":"Responda apenas OK."}],agent="connection_test",max_tokens=64)
        print(json.dumps({"provider":"openai","model":settings.model_fast,"status":"completed","ok":result=="OK","usage":provider.usage}))
    except (ConfigurationError,LLMClientError) as error:print(str(error));return 1
    return 0
if __name__=="__main__":raise SystemExit(main())
