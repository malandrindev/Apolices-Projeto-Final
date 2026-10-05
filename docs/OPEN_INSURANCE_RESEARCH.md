# Pesquisa de Open Insurance para descoberta de fontes D&O

Data de verificação: **04/10/2026**, America/Sao_Paulo. Escopo: documentação e especificações públicas oficiais da SUSEP e da Estrutura de Governança do Open Insurance Brasil. Este registro não implementa uma integração.

A pesquisa consultou páginas públicas e leu três YAMLs oficiais em memória, sem autenticação. Não consultou dados particulares, não criou consentimentos e não chamou operações de seguradoras ou provedores de IA. Exemplos contidos nas especificações não são produtos ou documentos reais capturados.

## Resultado para o MVP

A SUSEP separa dados públicos de canais/produtos das informações pessoais sobre clientes, contratos e utilização, cujo compartilhamento depende de consentimento. Logo, dados abertos de produtos podem apoiar a descoberta de condições gerais; não dão acesso automático à apólice individual de um cliente. [SUSEP — Open Insurance](https://www.gov.br/susep/pt-br/assuntos/open-insurance/)

A Fase 1 publica características de produtos e serviços das participantes. A Fase 2 compartilha dados pessoais autorizados entre instituições, com finalidade e prazo determinados, e permite revogação. [OPIN — Fase 1](https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/753678), [OPIN — Fase 2](https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/786475)

**Conclusão de engenharia:** a evolução mínima possível é descobrir metadados e eventuais URLs de condições gerais pela Fase 1 e submeter documentos encontrados à validação de download já existente. Não há evidência suficiente nesta pesquisa para preencher campos contratuais do MVP diretamente a partir dos dados abertos, certificar equivalência de escopo ou construir um catálogo de apólices particulares.

## Versões verificadas

| Documento/recurso | Versão/status observado | Evidência oficial |
| --- | --- | --- |
| Manual de Escopo de Dados e Serviços | 7.2; atualização publicada em 26/05/2026 | [SUSEP — documentos de referência](https://www.gov.br/susep/pt-br/assuntos/open-insurance/documentos_de_referencia) |
| Manual de APIs | 1.5; 28/08/2023 | [SUSEP — PDF do manual](https://www.gov.br/susep/pt-br/assuntos/open-insurance/arquivos/ManualdeAPIs1_5.pdf/@@download/file) |
| Manual de Segurança | 1.5; atualização publicada em 08/11/2023 | [SUSEP — documentos de referência](https://www.gov.br/susep/pt-br/assuntos/open-insurance/documentos_de_referencia) |
| Produtos e Serviços — D&O | 2.0.0, **Current**, base `products-services/v2` | [Tabela oficial da Fase 1](https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/753678), [YAML Current](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/current/directors-officers-liability.yaml) |
| Produtos e Serviços — D&O | 3.0.0, **Release Candidate**, base `products-services/v3` | [Tabela oficial da Fase 1](https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/753678), [YAML Release Candidate](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/directors-officers-liability.yaml) |
| Dados particulares — InsuranceResponsibility | 2.0.0, **Current** na tabela da Fase 2 | [Tabela oficial da Fase 2](https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/786475), [YAML Current](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/current/insurance-responsibility.yaml) |

Nos três YAMLs, `openapi: 3.0.0` identifica a linguagem OAS, enquanto `info.version` identifica a versão da API. O Manual de APIs, seção 4.1, também especifica OAS 3.0.0. [Manual de APIs, páginas editoriais 8–9](https://www.gov.br/susep/pt-br/assuntos/open-insurance/arquivos/ManualdeAPIs1_5.pdf/@@download/file)

O navegador de pesquisa devolveu páginas GitHub antigas, mostrando 1.4.0/2.0.0. A leitura direta dos URLs raw confirmou 2.0.0/3.0.0, coerentes com a tabela do portal. Os hashes abaixo identificam os bytes efetivamente verificados, sem preservar exemplos de conteúdo.

| YAML | Bytes | SHA-256 |
| --- | ---: | --- |
| D&O Current 2.0.0 | 26935 | `612bf443e62a3a13d74a60999bb0812d1a16c4d76e9ba6847fd5e700a4748523` |
| D&O Release Candidate 3.0.0 | 27553 | `698ec30aad7f63e04f4c75915a674088da4ce0b503617e7e757f13d4ea9f9df7` |
| InsuranceResponsibility Current 2.0.0 | 85686 | `00c789f9555e505270b46b8635821323997463bd96a29dd8bff890fd081fcde8` |

Status do portal e especificação não comprovam disponibilidade ou conformidade de cada implementação de seguradora.

## Recurso público exato e autenticação

O recurso específico é **D&O**, separado de `financial-risk`:

`GET /open-insurance/products-services/v2/directors-officers-liability`

O YAML Current contém apenas o path `/directors-officers-liability`, sobre base `https://api.organizacao.com.br/open-insurance/products-services/v2`. Esse host é um **exemplo de servidor**, não uma API central da SUSEP. O endpoint efetivo depende da participante. A resposta 200 é `application/json`, schema `ResponseDirectorsOfficersLiabilityList`, com `data.brand.companies[].products[]`, `links` e `meta`. Há paginação: `page` inicia em 1 e `page-size` tem padrão 10. [OAS D&O Current](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/current/directors-officers-liability.yaml)

Nos dois contratos D&O examinados não há `security`, `securitySchemes`, cabeçalho `Authorization`, OAuth ou chave de API declarados. **Acesso anônimo é o contrato observado**; funcionamento anônimo de uma seguradora específica não foi testado. HTTPS e limitação de tráfego continuam pertinentes. [OAS D&O Current](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/current/directors-officers-liability.yaml), [OAS D&O Release Candidate](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/directors-officers-liability.yaml)

## Campos observados: metadados, texto e eventual URL

Os seletores abaixo existem nos YAMLs 2.0.0 e 3.0.0. São campos de **produto**, sem número de apólice individual ou evidência de página PDF:

| Seletor relativo ao produto/empresa | Conteúdo observado |
| --- | --- |
| Empresa: `name`, `cnpjNumber`; produto: `name`, `code` | Identificação comercial |
| `coverages[].coverage`, `coverageDescription`, `allowApartPurchase` | Coberturas e contratação separada |
| `coverages[].coverageAttributes` | `maxLMI`, `maxLA`, participação e base |
| `coverages[].coverageAttributes.insuredParticipation`, `coverages[].coverageAttributes.insuredParticipationDescription` | Franquia/POS e descrição |
| `coverages[].coverageAttributes.indenizationBasis`, `coverages[].coverageAttributes.indenizationBasisOthers` | Ocorrência/reclamação/outras; grafia oficial |
| `maxLMG`, `maxLMGDescription` | Limite admitido pelo produto |
| `maxLMG.amount.amount`, `maxLMG.amount.unitType`, `maxLMG.amount.unit` | Valor, unidade e moeda |
| `validity`, `premiumPayment` | Prazos e formas de pagamento |
| `traits`, `targetAudiences`, `minimumRequirements` | Grandes riscos, público, contratação |
| `termsAndConditions.susepProcessNumber` | Processo SUSEP, opcional no schema |
| `termsAndConditions.definition` | String obrigatória, até 1024 caracteres |

`definition` é campo livre que **pode conter uma URL**. Não possui `format: uri` nem garante PDF, binário, download acessível ou texto integral. [OAS D&O 2.0.0](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/current/directors-officers-liability.yaml), [OAS D&O 3.0.0](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/directors-officers-liability.yaml)

Diferenças concretas da candidata 3.0.0: `maxLA` passa de objeto para array; `maxLMG` ganha `indexOthers`. Isso exige parser por versão, sem trocar automaticamente o contrato Current. [OAS candidata 3.0.0](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/directors-officers-liability.yaml)

**Limite de interpretação:** um LMG máximo admitido pelo produto não comprova o LMG contratado. Franquia/POS descritiva não garante um valor individual. A existência de cobertura em produto não demonstra aplicabilidade a um segurado. Uma URL encontrada precisa de verificação documental; Side A/B/C, exclusões e escopo equivalentes não podem ser inventados pelo mapeamento.

## Descoberta pública das participantes

A documentação do Diretório identifica `https://data.directory.opinbrasil.com.br/participants` como recurso público, sem OAuth ou certificado de cliente/mTLS. Ele fornece participantes, servidores e recursos para descoberta. `https://web.directory.opinbrasil.com.br/config/apiresources` descreve famílias, versões e estruturas esperadas de URLs. A documentação recomenda cache conforme `Cache-Control`; menciona atualização de participants a cada 15 minutos. Estes endpoints foram **identificados na documentação**, não consumidos nesta pesquisa. [OPIN — APIs do Diretório e boas práticas](https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/341180426/APIs%2Bdo%2BDiret%2Brio%2B-%2BBoas%2BPr%2Bticas)

Os endpoints protegidos `/organisations`, `/authorisationservers` e afins não devem ser usados como atalho para descoberta pública. A mesma fonte distingue APIs protegidas com mTLS/token das rotas públicas. [OPIN — APIs do Diretório e boas práticas](https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/341180426/APIs%2Bdo%2BDiret%2Brio%2B-%2BBoas%2BPr%2Bticas)

## Apólices particulares: contrato separado

O YAML `insurance-responsibility` 2.0.0 documenta dados de apólice, prêmio e sinistro. Seus GETs são:

- `/insurance-responsibility`
- `/insurance-responsibility/{policyId}/policy-info`
- `/insurance-responsibility/{policyId}/premium`
- `/insurance-responsibility/{policyId}/claim`

Todos exigem `Authorization`, consentimento válido `AUTHORISED`, permissões próprias e escopos `openid`, `consent:consentId`, `insurance-responsibility`. O schema inclui `policyId`, segurados, vigência e LMG; também identifica `hasComplementaryContract` como aplicável a D&O. Esses dados não constituem um catálogo público. [OAS InsuranceResponsibility](https://raw.githubusercontent.com/br-openinsurance/areadesenvolvedor/main/documentation/source/files/swagger/current/insurance-responsibility.yaml)

O guia de receptores descreve cadastro da organização/aplicativo no Diretório e credenciais de transporte. Uma caixa de consentimento local do MVP não substitui a jornada de autorização do ecossistema. [OPIN — guia das instituições receptoras](https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/5472307/Guia%2Bdo%2BUsu%2Brio%2Bpara%2BInstitui%2Bes%2BReceptores%2Bde%2BDados)

## Versionamento e limites da pesquisa

O manual distingue major, minor, patch e release candidate, e atribui ao portal a publicação de versões/cronogramas. A norma trata convivência de 180 dias; a orientação operacional de 2026 diferencia as três APIs consumidas pelo Open Finance das demais e indica 45/60/90 dias segundo release. **Não foi estabelecida aqui uma data de migração D&O**, nem resolvida uma interpretação regulatória entre essas referências. [Manual de APIs, seção 4.2](https://www.gov.br/susep/pt-br/assuntos/open-insurance/arquivos/ManualdeAPIs1_5.pdf/@@download/file), [OPIN — multiversionamento](https://opinbrasil.atlassian.net/wiki/spaces/RDD/pages/19562497/Multiversionamento%2Bde%2BAPIs)

Faltam comprovar em etapa futura: hosts D&O ativos, respostas reais por participante, conteúdo efetivo de `definition`, documentos acessíveis, correspondência de versão/processo e um conjunto comparável de condições. O PDF do manual de escopo 7.2 não foi extraído nesta pesquisa; sua versão/data foram verificadas na página oficial. Não há integração Open Insurance implementada ou teste de endpoints de dados registrado por este documento.

**Próxima ação proposta, sem implementação nesta fase:** avaliar descoberta somente de dados abertos, com cache e versão explícita, preservando URL/data/hash de captura. PDFs eventualmente localizados devem seguir intake/OCR e validação de compatibilidade existentes. Qualquer acesso a dados pessoais exige um projeto próprio de integração autorizada; não é necessário para o MVP acadêmico.
