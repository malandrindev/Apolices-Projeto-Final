"""Reproduce offline D&O demo PDFs and human-authored ground truth from local sources.

No model/SDK/gateway/network. Original legal pages are inserted unchanged.
Run python -m scripts.build_demo_dataset [--output-dir PATH].
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import pymupdf
from src.agents.comparison import LABELS
from src.schemas.policy import NOT_FOUND, PolicyExtraction
ROOT = Path(__file__).resolve().parents[1]
VERSION = "demo-do-v1"
WARNINGS = ("DOCUMENTO DE DEMONSTRAÇÃO", "DADOS FICTÍCIOS", "SEM VALIDADE CONTRATUAL")
CONFIG = {
 "porto": {
  "source":"Apólice-PORTO_D&O_teste.pdf",
  "sha":"bf9e103a33e941e9df44a30d553e429825d66b48f658c17e3be807bec8be1145",
  "source_pages":52, "version":"Fevereiro/2022; vigente a partir de 01/02/2022",
  "selection":[4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,21,22,23,28,29,30,31,32,33,34,35,36],
  "insurer":"Porto Seguro Cia de Seguros Gerais", "label":"PORTO SEGURO",
  "number":"DEMO-D&O-PORTO-2026-001","limit":"R$ 20.000.000","premium":"R$ 120.000",
  "retention":"R$ 100.000","retroactive":"01/01/2020","process":"15414.901349/2019-94",
  "territory":"Mundial, na medida legalmente permitida, observadas restrições do wording.",
  "extension":"Bloqueio e indisponibilidade de bens (penhora on-line), conforme wording.",
 },
 "allianz": {
  "source":"Apólice-Allianz Condições_Gerais- 2025_teste.pdf",
  "sha":"1778f9d09c187996e5cef8fda73a51c89ced0d0dc1772aaa4f327a9f5eb9a6cc",
  "source_pages":75,"version":"Dezembro/2025",
  "selection":[8,9,10,11,12,13,14,15,16,17,18,19,21,22,24,25,27,28,29,30,33,34,36,37,38,39,40,41,46,49,50],
  "insurer":"Allianz Seguros S.A.","label":"ALLIANZ",
  "number":"DEMO-D&O-ALLIANZ-2026-002","limit":"R$ 30.000.000","premium":"R$ 135.000",
  "retention":"R$ 250.000","retroactive":"01/01/2018","process":"15414.901113/2017-96",
  "territory":"Brasil, escolhido apenas nesta especificação sintética.",
  "extension":"Indisponibilidade de bens por bloqueio e penhora online, conforme wording.",
 },
}
def digest(data): return hashlib.sha256(data).hexdigest()
def normal(text): return " ".join(text.split())
def save_json(path,value):
 path.write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+"\n").encode("utf-8"))
def synthetic_page(doc,title,blocks):
 page=doc.new_page(width=595,height=842)
 page.draw_rect(pymupdf.Rect(0,0,595,125),color=None,fill=(.07,.18,.25))
 for y,warning,size in zip((40,65,88),WARNINGS,(15,12,12)):
  page.insert_text((42,y),warning,fontsize=size,fontname="hebo",color=(1,1,1))
 page.insert_text((42,150),title,fontsize=17,fontname="hebo",color=(.07,.18,.25))
 if page.insert_textbox(pymupdf.Rect(42,172,553,775),"\n\n".join(blocks),
                        fontsize=11,fontname="helv",lineheight=1.2)<0:
  raise ValueError("Synthetic page does not fit")
 page.insert_text((42,804),"DEMONSTRAÇÃO ACADÊMICA | Excertos, não documento integral",fontsize=9)
def build_policy(key,output):
 c=CONFIG[key]; filename=key+"_demo_policy.pdf"
 raw=(ROOT/"data"/"demo_sources"/c["source"]).read_bytes()
 if digest(raw)!=c["sha"]: raise ValueError("Original source changed")
 source=pymupdf.open(stream=raw,filetype="pdf")
 if len(source)!=c["source_pages"]: raise ValueError("Source page count changed")
 demo=pymupdf.open()
 synthetic_page(demo,c["label"]+" - EXEMPLO D&O",[
  "DEMONSTRAÇÃO ACADÊMICA",
  "Especificação sintética + excertos de condições contratuais",
  "ALPHA TECNOLOGIA S.A. - empresa fictícia de capital fechado.",
  "Fonte do wording: "+c["source"],
  "Versão da fonte: "+c["version"]+". Processo SUSEP: "+c["process"]+".",
  "As páginas 1 e 2 são sintéticas. As demais são páginas originais selecionadas, "
  "preservadas sem reescrever texto jurídico. Consulte o mapa de páginas.",
  "Este conjunto não é apólice individual emitida nem cotação comercial. "
  "Valores e seleção de coberturas servem apenas à demonstração.",
  "Excertos não demonstram integralidade, atualidade comercial ou melhor seguradora. "
  "As fontes Porto/Allianz são de anos distintos (2022/2025)."])
 lines=[
  "Seguradora: "+c["insurer"],"Tomador: ALPHA TECNOLOGIA S.A.",
  "Número da apólice demo: "+c["number"],
  "Início da vigência: 01/01/2026","Fim da vigência: 31/12/2026","Moeda: BRL",
  "Limite Máximo de Garantia (LMG): "+c["limit"],"Prêmio: "+c["premium"],
  "Franquia / retenção Side B: "+c["retention"],
  "Data limite de retroatividade: "+c["retroactive"],
  "Âmbito geográfico demo: "+c["territory"],
  "Coberturas selecionadas: Side A e Side B, respeitado o wording e suas condições.",
  "Extensão selecionada: "+c["extension"],
  "Side C: não contratada neste exemplo fictício.",
  "Sublimites específicos: não definidos nesta especificação sintética.",
  "Prazo adicional: observar condições-base; extensão opcional não contratada na demo."]
 synthetic_page(demo,"ESPECIFICAÇÃO SINTÉTICA - "+c["label"],lines)
 mapping=[{"demo_page":n,"kind":"synthetic","source_file":None,"source_page":None} for n in (1,2)]
 for n in c["selection"]:
  demo.insert_pdf(source,from_page=n-1,to_page=n-1,links=False,annots=False)
  mapping.append({"demo_page":len(demo),"kind":"original_excerpt","source_file":c["source"],
   "source_page":n,"source_sha256":c["sha"],
   "text_sha256":digest(source[n-1].get_text().encode("utf-8"))})
 demo.set_metadata({"title":"DEMONSTRAÇÃO ACADÊMICA D&O - "+c["label"],
  "author":"InsurMinds - fixture acadêmica",
  "subject":"DADOS FICTÍCIOS; SEM VALIDADE CONTRATUAL; excertos selecionados",
  "creationDate":"D:20261004000000Z","modDate":"D:20261004000000Z"})
 fixed=digest((VERSION+key).encode())[:32]
 demo.xref_set_key(-1,"ID","[<"+fixed+"><"+fixed+">]")
 demo.save(output/filename,garbage=4,deflate=True,no_new_id=True)
 demo.close(); demo=pymupdf.open(output/filename)
 by_source={m["source_page"]:m["demo_page"] for m in mapping if m["kind"]=="original_excerpt"}
 def original(n,start,end=None):
  text=normal(source[n-1].get_text()); offset=text.index(start)
  finish=text.index(end,offset+len(start)) if end else len(text)
  excerpt=text[offset:finish].strip()
  if excerpt not in normal(demo[by_source[n]-1].get_text()): raise ValueError("Altered excerpt")
  return {"source_document":filename,"source_page":by_source[n],"original_source_document":c["source"],
          "original_source_page":n,"evidence_excerpt":excerpt,"origin":"original_wording","source_type":"real_wording"}
 def synth(line):
  if normal(line) not in normal(demo[1].get_text()): raise ValueError("Synthetic evidence not printed")
  return {"source_document":filename,"source_page":2,"original_source_document":None,
          "original_source_page":None,"evidence_excerpt":line,"origin":"synthetic_specification","source_type":"synthetic_specification"}
 fields={}
 def put(name,value,evidence,status="FOUND",basis=None,support=()):
  fields[name]={"expected_value":value,"status":status,**evidence,
   "confidence_basis":basis or ("Dado fictício explícito da página 2; não emitido."
    if evidence["origin"]=="synthetic_specification" else
    "Leitura humana do excerto literal, limitada ao aspecto descrito; demais condições permanecem."),
   "supporting_evidence":list(support)}
 for name,value,index in [
  ("seguradora",c["insurer"],0),("tomador_segurado","ALPHA TECNOLOGIA S.A.",1),
  ("numero_apolice",c["number"],2),("vigencia_inicio","01/01/2026",3),
  ("vigencia_fim","31/12/2026",4),("moeda","BRL",5),
  ("limite_maximo_garantia",c["limit"],6),("premio",c["premium"],7),
  ("retencao_franquia",c["retention"]+" (Side B)",8),
  ("data_retroativa",c["retroactive"],9),
  ("side_c","Não contratada neste exemplo fictício.",13)]:
  put(name,value,synth(lines[index]))
 fields["sublimites"]={"expected_value":NOT_FOUND,"status":"NOT_RETRIEVED",
  "source_document":filename,"source_page":None,"evidence_excerpt":NOT_FOUND,
  "original_source_document":None,"original_source_page":None,"origin":"conservative_unknown","source_type":None,
  "confidence_basis":"Especificação demo não define sublimites; não equivale à inexistência no contrato.",
  "supporting_evidence":[synth(lines[14])]}
 if key=="porto":
  put("side_a","Garantia A: pagamento ao segurado não indenizado pela sociedade, observadas condições.",
      original(32,"Garantia A - Segurados","Garantia B - Reembolso"),
      support=[original(16,"Com relação à Garantia A")])
  put("side_b","Reembolso à empresa por perda coberta paga ao segurado.",
      original(32,"Garantia B - Reembolso","Todos os outros termos"))
  put("custos_defesa","Adiantamento antes da decisão final; ressarcimento se valores indevidos.",
      original(18,"6.6 Adiantamentos","6.7 Consentimento"),
      support=[original(7,"2.11 Custos De Defesa","2.12")])
  put("extensoes_cobertura","Penhora on-line: adiantamento após 20 dias dos documentos, sujeito às condições.",
      original(33,"EXTENSÃO DE COBERTURA PARA BLOQUEIO"),
      support=[synth(lines[12]),original(34,"O pagamento será interrompido","Para fins desta cláusula")])
  put("base_cobertura","À base de reclamações com notificação.",
      original(4,"Mediante o pagamento do Prêmio","Para facilitar"))
  put("periodo_estendido_notificacao",
      "Prazo-base depende da especificação; extensão opcional de 12 meses, sujeita a aceitação e pagamento até 30 dias antes do fim da vigência.",
      original(30,"A extensão do Prazo Adicional é válida","Não será concedida"),"AMBIGUOUS",
      "Prazo-base não definido na demo. Extensão prevista no wording, não contratada; sem prazo total seguro.",
      [original(29,"8.20 Prazo Adicional"),synth(lines[15])])
  put("exclusoes",
      "Conduta: decisão final judicial/arbitral ou administrativa sem recurso nessa esfera, ou admissão por escrito; não imputação entre segurados.",
      original(13,"Para fins de aplicação da Exclusão Conduta","4.2 Danos Ambientais"))
  put("prazo_aviso_sinistro","Comunicar reclamação o mais rápido possível; observar demais condições.",
      original(28,"8.12 Cooperação","8.13 Sub-rogação"))
  put("controle_defesa","Livre escolha; segurado se defende; seguradora pode participar; advogados separados em conflito.",
      original(17,"6.4 Defesa e Acordos","A Sociedade ("),
      support=[original(7,"É garantida ao Segurado a livre escolha","É garantido à Seguradora")])
  put("consentimento_acordo","Acordo ou confissão dependem de consentimento prévio e expresso.",
      original(18,"6.7 Consentimento","6.8 Alocação"))
  put("rateio","Pagamento restrito à parcela da reclamação coberta; observar regras de alocação.",
      original(18,"6.8 Alocação"))
  put("jurisdicao_lei","Lei brasileira; foro do domicílio do tomador.",
      original(31,"8.27 Foro","8.28 Informações"),
      support=[original(29,"8.19 Interpretação","8.20 Prazo Adicional")])
  put("territorialidade","Mundial, legalmente permitido e sujeito a limites e restrições.",
      original(17,"6.1 Âmbito Geográfico","6.2 Boa Fé"),support=[synth(lines[10])])
  put("cancelamento_renovacao","Cancelamento por exaustão do LMG ou acordo; a pedido do tomador, devolução conforme Tabela de Prazo Curto.",
      original(23,"8.5 Cancelamento","8.6 Aumento"))
  put("definicoes_relevantes","Diretor: administradores responsáveis por decisões que impactam a entidade.",
      original(8,"2.18 Diretor","2.19 Diretor de Entidade Externa"),"AMBIGUOUS",
      "Excerto parcial de definição; não conclui o conjunto de pessoas seguradas.")
 else:
  put("side_a",
      "Cobertura A: impossibilidade legal ou insolvência; cláusula B prevê adiantamento de perda coberta após 30 dias.",
      original(9,"I) COBERTURA DE PAGAMENTO AO SEGURADO","II) COBERTURA"),
      support=[original(9,"Na hipótese de o Tomador")])
  put("side_b","Reembolso à empresa por perda coberta paga ao segurado.",
      original(9,"II) COBERTURA DE REEMBOLSO","A Franquia ou participação obrigatória do Segurado é"))
  put("custos_defesa","Adiantamento conforme incorrido, em até 30 dias da documentação; ressarcimento nas hipóteses previstas.",
      original(28,"18.15."),support=[original(29,"recebimento da documentação","CLÁUSULA 19.")])
  put("extensoes_cobertura","Penhora online: adiantamento após 15 dias dos documentos, sujeito às condições.",
      original(10,"III) COBERTURA PARA INDISPONIBILIDADE"),support=[synth(lines[12])])
  put("base_cobertura","À base de reclamações com notificação.",
      original(9,"CLÁUSULA 3. OBJETIVO","CLÁUSULA 4."),
      support=[original(24,"14.2.","14.5.")])
  put("periodo_estendido_notificacao",
      "60 dias automáticos salvo especificação; extensão opcional de 12 meses pagos dentro de 30 dias após término, com hipóteses e exceções.",
      original(15,"III) COBERTURA PARA PRAZO ADICIONAL"),
      support=[original(16,"Não será concedido o prazo","IV) COBERTURA VITALÍCIA"),synth(lines[15])])
  put("exclusoes",
      "Conduta: decisão final judicial/arbitral, confissão inclusive delação ou reconhecimento de órgão oficial; confirmar demais cláusulas.",
      original(16,"I) CLÁUSULA DE EXCLUSÃO DE CONDUTA"),"AMBIGUOUS",
      "Comparação textual da cláusula de conduta. 30.1(g) menciona decisão administrativa irrecorrível; "
      "não se conclui prioridade entre cláusulas nem vantagem.",
      [original(17,"PARA FINS DE APLICAÇÃO","II)"),original(34,"G)","H)")])
  put("prazo_aviso_sinistro","Avisar imediatamente sinistro/reclamação; observar demais condições.",
      original(27,"17.1.5.","17.2."),support=[original(38,"AVISO DE SINISTRO:","BENEFICIÁRIO:")])
  put("controle_defesa","Livre escolha; seguradora exige três propostas de honorários.",
      original(27,"18.4.","18.5."))
  put("consentimento_acordo","Acordo exige consentimento escrito, não negado ou atrasado sem justo motivo.",
      original(28,"18.13.","18.14."),support=[original(28,"18.6.","18.7.")])
  put("rateio","Alocação justa por acordo; sem acordo, pagamento da parcela coberta e das pessoas abrangidas.",
      original(29,"CLÁUSULA 21. ALOCAÇÃO","CLÁUSULA 22."))
  put("jurisdicao_lei","Lei brasileira; foro do segurado/beneficiário com alternativas previstas.",
      original(36,"CLÁUSULA 34. LEI APLICÁVEL","CLÁUSULA 35."))
  put("territorialidade","Brasil nesta especificação sintética; wording remete à especificação.",
      synth(lines[10]),support=[original(36,"CLÁUSULA 36.","CLÁUSULA 37.")])
  put("cancelamento_renovacao","Cancelamento por exaustão ou acordo; neste último, retenção proporcional ao tempo decorrido.",
      original(34,"31.1.","31.2."),support=[original(30,"22.2.","CLÁUSULA 23.")])
  put("definicoes_relevantes","Empresa: tomador, cotomadores e subsidiárias/controladas conforme especificação.",
      original(41,"EMPRESA(S):","EMPRESA-ALVO:"),"AMBIGUOUS",
      "Excerto parcial de definição; não conclui o conjunto de pessoas seguradas.")
 if set(fields)!=set(PolicyExtraction.model_fields): raise ValueError("Require all 27 fields")
 for name,entry in fields.items():
  for citation in [entry,*entry["supporting_evidence"]]:
   if citation["source_page"] is not None and normal(citation["evidence_excerpt"]) not in normal(
       demo[citation["source_page"]-1].get_text()): raise ValueError("Invalid quote: "+name)
 gt={"schema_version":1,"dataset_version":VERSION,"demo":True,"source_name":filename,
     "document_sha256":digest((output/filename).read_bytes()),
     "original_source_file":c["source"],"original_source_sha256":c["sha"],
     "metadata":{"insurer":c["insurer"],"display_name":c["label"],"branch":"D&O",
       "product_name":"Demonstração acadêmica D&O","company_type":"Capital fechado",
       "document_type":"Documento de demonstração (especificação sintética + excertos)",
       "period":"01/01/2026 a 31/12/2026","currency":"BRL","susep_process":c["process"],
       "wording_version":c["version"]},
     "specification":{"insurer":c["insurer"],"policy_number":c["number"],
       "insured":"ALPHA TECNOLOGIA S.A.","effective_date":"2026-01-01","expiration_date":"2026-12-31",
       "currency":"BRL","premium":c["premium"],"LMG":c["limit"],"retention":c["retention"],
       "retroactive_date":c["retroactive"]},
     "synthetic_fields":["insurer","policy_number","insured","effective_date","expiration_date",
       "currency","premium","LMG","retention","retroactive_date","selected_coverages",
       "selected_extensions","territory"],
     "fields":fields}
 save_json(output/("ground_truth_"+key+".json"),gt)
 save_json(output/("page_map_"+key+".json"),{"schema_version":1,"dataset_version":VERSION,
     "source_document":filename,"original_source_document":c["source"],
     "original_source_sha256":c["sha"],"pages":mapping})
 demo.close(); source.close()
 return gt
EXPLANATIONS={
 "seguradora":"Identidades distintas; não há preferência por seguradora.",
 "numero_apolice":"Identificadores fictícios distintos; não são números de emissão real.",
 "tomador_segurado":"Mesmo risco fictício: ALPHA TECNOLOGIA S.A., de capital fechado.",
 "vigencia_inicio":"Mesmo início de vigência fictício.",
 "vigencia_fim":"Mesmo fim de vigência fictício.",
 "moeda":"Mesma moeda BRL, sem conversão cambial.",
 "limite_maximo_garantia":"Allianz tem R$ 10 milhões a mais de limite nominal nesta simulação. Isso não equivale a maior cobertura integral.",
 "premio":"Porto tem prêmio fictício R$ 15 mil menor. O valor isolado não ordena a qualidade da proteção.",
 "retencao_franquia":"Porto tem retenção Side B fictícia R$ 150 mil menor. Conferir demais condições de incidência.",
 "data_retroativa":"Allianz inicia retroatividade fictícia dois anos antes; continuidade e fatos conhecidos precisam ser avaliados. Sem vantagem integral segura.",
 "sublimites":"Especificação demo não define valores por cobertura. Não concluir ausência de sublimites no contrato integral.",
 "side_a":"Gatilhos de Side A têm wording distinto. Ambas preveem adiantamento quando a empresa não indeniza em 30 dias. Sem concluir ausência de proteção na Allianz nem vantagem global.",
 "side_b":"Equivalentes apenas no mecanismo de reembolso à empresa por perda coberta paga ao segurado; demais condições podem diferir.",
 "side_c":"Não selecionada nas duas especificações fictícias; isso não afirma inexistência nos produtos.",
 "custos_defesa":"Ambos preveem adiantamento. Allianz explicita 30 dias da documentação; Porto antes da decisão final. Conferir consentimento e ressarcimento, sem vantagem segura.",
 "extensoes_cobertura":"Penhora online: Porto aguarda 20 dias e Allianz 15 dias dos documentos. A diferença afeta fluxo de caixa; elegibilidade e condições impedem ranking integral.",
 "base_cobertura":"Ambos usam reclamações com mecanismo de notificação; equivalência limitada à base, sem igualar regras completas.",
 "periodo_estendido_notificacao":"Allianz prevê 60 dias automáticos salvo especificação; Porto remete prazo-base à especificação, não preenchida na demo. Extensão opcional de 12 meses não contratada: Porto exige pagamento antes do término, Allianz após. Não comparar duração total nem vantagem.",
 "exclusoes":"Gatilhos de conduta diferem. Porto explicita decisão administrativa sem recurso; Allianz menciona órgão oficial, mas outras disposições exigem irrecorribilidade. Confirmar alcance jurídico; sem vantagem segura.",
 "prazo_aviso_sinistro":"Porto diz o mais rápido possível no excerto; Allianz comunicação imediata. Ambos exigem diligência, sem evidência de prazos idênticos.",
 "controle_defesa":"Ambas garantem livre escolha de defesa. Allianz explicita três propostas de honorários; o excerto Porto admite advogados distintos em conflito. Providência operacional distinta, sem concluir que Porto não possa exigir documentos adicionais.",
 "consentimento_acordo":"Ambos exigem anuência. Allianz explicita consentimento não negado/atrasado sem justo motivo; demais regras diferem, sem vantagem segura.",
 "rateio":"Ambos limitam pagamento ao aspecto coberto, com regras de alocação distintas. Sem equivalência integral ou direção segura.",
 "jurisdicao_lei":"Lei brasileira em ambos; Porto domicílio do tomador, Allianz segurado/beneficiário e alternativas. Conveniência depende do caso.",
 "territorialidade":"Porto mundial com restrições; Allianz Brasil apenas na especificação fictícia, pois wording remete a ela. Não afirma que todo produto Allianz seja nacional.",
 "cancelamento_renovacao":"Ambos preveem cancelamento por exaustão e por acordo. Porto, a pedido do tomador, calcula a retenção pela Tabela de Prazo Curto; Allianz, no acordo, retém proporcionalmente ao tempo decorrido. Sem calcular restituição nem atribuir vantagem.",
 "definicoes_relevantes":"Destaques de conceitos distintos (diretor e empresa), sem conclusão segura sobre todas as pessoas seguradas.",
}
EQUAL={"tomador_segurado","vigencia_inicio","vigencia_fim","moeda","side_b","side_c","base_cobertura"}
DIRECTION={"limite_maximo_garantia":"mais_favoravel_B","premio":"mais_favoravel_A",
           "retencao_franquia":"mais_favoravel_A"}
def build(output):
 output.mkdir(parents=True,exist_ok=True)
 a=build_policy("porto",output); b=build_policy("allianz",output); rows=[]
 for name in PolicyExtraction.model_fields:
  x,y=a["fields"][name],b["fields"][name]
  cls=DIRECTION.get(name,"igual" if name in EQUAL else "diferente_nao_comparavel")
  rows.append({"field_name":name,"label":LABELS[name],
   "expected_difference":"equivalente neste aspecto" if cls=="igual" else
    "diferença no aspecto descrito, limitada às evidências",
   "direction_if_safe":"referência" if cls=="mais_favoravel_A" else
                      "candidato" if cls=="mais_favoravel_B" else None,
   "classification":cls,"business_explanation":EXPLANATIONS[name],
   "value_a":x["expected_value"],"value_b":y["expected_value"],
   "source_evidence_a":{k:x[k] for k in ("source_document","source_page","evidence_excerpt","origin","source_type")},
   "source_evidence_b":{k:y[k] for k in ("source_document","source_page","evidence_excerpt","origin","source_type")},
   "supporting_evidence_a":x["supporting_evidence"],"supporting_evidence_b":y["supporting_evidence"],
   "information_insufficient":name in {"sublimites","periodo_estendido_notificacao","exclusoes","definicoes_relevantes"},
   "no_global_ranking":True})
 save_json(output/"ground_truth_comparison.json",{
  "schema_version":1,"dataset_version":VERSION,"demo":True,"no_global_ranking":True,
  "source_a":a["source_name"],"document_id_a":a["document_sha256"],
  "source_b":b["source_name"],"document_id_b":b["document_sha256"],
  "compared_at":"2026-10-04T00:00:00Z","fields":rows,
  "executive_summary":"DOCUMENTO DE DEMONSTRAÇÃO / DADOS FICTÍCIOS / SEM VALIDADE CONTRATUAL. Resultado pré-processado offline para o mesmo risco fictício. Compare limite, prêmio, retenção e retroatividade; confira wording de penhora online, defesa e prazo adicional. Entre as condições de negócio: 14 diferenças, 3 equivalências e 4 itens sem conclusão segura. Identificação permanece nos 27 campos. Não há ranking global.",
  "risk_highlights":["Prazo-base Porto sem duração na especificação fictícia.",
                     "Conduta exige confirmar o alcance das cláusulas.",
                     "Sublimites e definição completa de segurados não concluídos."]})
 files=["porto_demo_policy.pdf","allianz_demo_policy.pdf","ground_truth_porto.json",
        "ground_truth_allianz.json","ground_truth_comparison.json","page_map_porto.json","page_map_allianz.json"]
 save_json(output/"manifest.json",{
  "schema_version":1,"dataset_version":VERSION,"demo":True,"provider_http_requests":0,
  "warning":"DOCUMENTO DE DEMONSTRAÇÃO / DADOS FICTÍCIOS / SEM VALIDADE CONTRATUAL",
  "assets":{n:digest((output/n).read_bytes()) for n in files},
  "documents":[{"key":k,"pdf":k+"_demo_policy.pdf","ground_truth":"ground_truth_"+k+".json",
    "page_map":"page_map_"+k+".json","pages":len(c["selection"])+2,"source_file":c["source"],
    "source_sha256":c["sha"],"source_pages":c["source_pages"],"source_version":c["version"]}
    for k,c in CONFIG.items()],
  "default_reference":"porto",
  "limits":["Especificação fictícia, não emitida e sem validade contratual.",
   "Fontes Porto2022/Allianz2025; sem claim de atualização comercial.",
   "Excertos selecionados não equivalem à integralidade dos produtos.",
   "Sublimites, prazo total Porto e alcance da exclusão de conduta requerem confirmação."]})
 print(json.dumps({"dataset_version":VERSION,"provider_http":0,
                   "pages":{k:len(c["selection"])+2 for k,c in CONFIG.items()}}))
if __name__=="__main__":
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument("--output-dir",type=Path,default=ROOT/"data"/"demo")
 build(parser.parse_args().output_dir)
