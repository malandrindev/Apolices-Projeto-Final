"""Estimate a LOCAL public-document preflight; never construct an inference client."""
from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.agents.extraction import EXTRACTION_PROMPT, ExtractionAgent
from src.agents.segmentation import SegmentationAgent
from src.agents.comparison import IDENTITY_FIELDS, MONEY_FIELDS, DATE_FIELDS, SEMANTIC_PROMPT
from src.schemas.policy import PolicyExtraction
from src.schemas.clause import ClauseCategory
from src.llm.openai_client import schema_for_stage
from src.config import get_ingestion_settings, get_settings
from src.pipeline import process_document
class ProhibitedGateway:
    provider = "openai"
    def complete(self, **kwargs):
        raise RuntimeError("GenAI is prohibited in preflight.")
def input_estimates(messages, stage):
    # Includes schema present in messages AND the adapter's text.format schema.
    extra = json.dumps(schema_for_stage(stage), ensure_ascii=False)
    contents = [message["content"] for message in messages] + [extra]
    return (math.ceil(sum(map(len, contents))/3.5)+1000,
            sum(len(value.encode("utf-8")) for value in contents)+1000)
def measure(record, settings, models):
    path = (ROOT / record["local_path"]).resolve()
    if not path.is_relative_to((ROOT / "data" / "processed").resolve()):
        raise ValueError("Preflight expects local files within data/processed.")
    info, doc = process_document(path, settings=settings)
    if info.sha256 != record["sha256"]:
        raise ValueError("Candidate hash changed; research approval must be reviewed.")
    segmenter = SegmentationAgent(gateway=None)
    chunks = segmenter.segment(doc)
    if len(chunks) != record["chunk_count"]:
        raise ValueError("Segmentation count changed; update candidate evidence.")
    extractor = ExtractionAgent(gateway=ProhibitedGateway(), model_strong=models.model_fast)
    planning, reserve, repair_reserve = 0, 0, 0
    repair = ("Corrija uma unica vez: JSON/schema ou evidencia invalida nos campos "
              + ", ".join(PolicyExtraction.model_fields) + ". Retorne o JSON completo.")
    for chunk in chunks:
        payload = {"clause_id":chunk.clause_id, "category":chunk.category.value,
                   "pages":[page.model_dump() for page in chunk.source_pages]}
        messages = [{"content":EXTRACTION_PROMPT+"\nSchema:\n"+extractor.schema},
                    {"content":json.dumps(payload,ensure_ascii=False)}]
        p, b = input_estimates(messages,"extraction")
        planning += p; reserve += b
        repair_reserve += b + len(repair.encode("utf-8")) + 1000
    titles, started, index = [], False, 0
    for page in doc.pages:
        for line in page.text.splitlines(keepends=True):
            heading = segmenter._heading(line)
            if heading or not started:
                index += 1; started = True
                title = line.strip() if heading else "Conteudo sem titulo"
                if segmenter._category(title) == ClauseCategory.UNKNOWN:
                    titles.append({"clause_id":f"heading-{index}", "title":title})
    sp, sb = 0, 0
    for offset in range(0,len(titles),20):
        p,b = input_estimates([{"content":"Classifique somente os titulos. Categorias: "
                                          +", ".join(c.value for c in ClauseCategory)},
                              {"content":json.dumps(titles[offset:offset+20],ensure_ascii=False)}],"segmentation")
        sp += p; sb += b
    return {"id":record["id"],"pages":len(doc.pages),"chunks":len(chunks),
            "classification_batches":math.ceil(len(titles)/20),"ocr_cache_hit":doc.cache_hit,
            "input_planning_extraction":planning,"input_reserve_extraction":reserve,
            "input_reserve_repair":repair_reserve,"input_planning_classification":sp,
            "input_reserve_classification":sb}
def scenarios(records, pairs, prices, model_names):
    n=sum(r["chunks"] for r in records); b=sum(r["classification_batches"] for r in records)
    semantic_fields=len(set(PolicyExtraction.model_fields)-IDENTITY_FIELDS-MONEY_FIELDS-DATE_FIELDS)
    batches=math.ceil(semantic_fields/8)*pairs
    # Evidence/value bounds are 1600/800 chars per source. Reserve UTF-8 up to4bytes/char.
    cp=math.ceil((len(SEMANTIC_PROMPT)+8*(2*1600+2*800+400)+2000)/3.5)+1000
    cb=4*(len(SEMANTIC_PROMPT)+8*(2*1600+2*800+400)+2000)+1000
    factor=prices["cache_write_input_multiplier"]
    def cost(role,input_tokens,output_tokens):
        p=prices["models"][model_names[role]]
        return (input_tokens*p["input"]*factor+output_tokens*p["output"])/1e6
    ei=sum(r["input_planning_extraction"]+r["input_planning_classification"] for r in records)
    er=sum(r["input_reserve_extraction"]+r["input_reserve_classification"] for r in records)
    tr=sum(r["input_reserve_repair"] for r in records)
    planning=cost("fast",ei,n*1500+b*800)+cost("strong",cp*batches,batches*1500)
    capped_no_repair=cost("fast",er,n*4500+b*1600)+cost("strong",cb*batches,batches*2500)
    repaired=capped_no_repair+cost("repair",tr,n*4500)+cost("strong",(cb+2000)*batches,batches*2500)
    return {"document_ids":[r["id"] for r in records],"pages":sum(r["pages"] for r in records),
            "chunks":n,"classification_batches":b,"comparison_batches_max":batches,
            "logical_normal_max":n+b+batches,"logical_with_repairs_max":2*n+b+2*batches,
            "http_attempts_stress_max":3*(2*n+b+2*batches),
            "usd_planning_no_repairs":round(planning,4),
            "usd_output_caps_input_reserve_no_repairs":round(capped_no_repair,4),
            "usd_stress_all_repairs_3_attempts":round(repaired*3,4)}
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",type=Path,default=ROOT/"data/processed/public_validation_preflight/candidates.json")
    parser.add_argument("--prices",type=Path,default=ROOT/"docs/OPENAI_PRICES_2026-10-04.json")
    parser.add_argument("--scenario", nargs="+", help="Reference ID followed by candidate IDs, for a local multi-document estimate.")
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text(encoding="utf-8"))
    prices=json.loads(args.prices.read_text(encoding="utf-8"))
    models=get_settings(require_api_key=False,provider="openai")
    model_names={"fast":models.model_fast,"repair":models.model_intermediate,"strong":models.model_strong}
    if any(name not in prices["models"] for name in model_names.values()):
        raise ValueError("Configured model absent from versioned prices; reverify rates.")
    measured=[measure(r,get_ingestion_settings(),models) for r in manifest["documents"]]
    result={"genai_calls":0,"method":"chars/3.5+1000; input reserve UTF8bytes+1000; no cache discount",
            "output_planning_assumption":{"extraction":1500,"classification":800,"comparison":1500},
            "prices_verified_on":prices["verified_on"],"models":model_names,"documents":measured,
            "pairs":[scenarios([a,b],1,prices,model_names) for i,a in enumerate(measured) for b in measured[i+1:]]}
    if args.scenario:
        lookup = {record["id"]: record for record in measured}
        if len(args.scenario) < 2 or len(set(args.scenario)) != len(args.scenario):
            raise ValueError("Scenario needs at least two distinct document IDs.")
        result["selected_scenario"] = scenarios([lookup[key] for key in args.scenario], len(args.scenario)-1, prices, model_names)
    target=args.manifest.parent/"cost_estimates.json"
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"status":"PREFLIGHT_ONLY","genai_calls":0,"documents":len(measured),"output":str(target.relative_to(ROOT))}))
    return 0
if __name__=="__main__":
    raise SystemExit(main())
