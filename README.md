# Entrevistas trabalhistas → API de Petições

Banco de dados + API de ingestão. O agente de IA do Chatwoot conversa com o
cliente e vai gravando as respostas aqui; mais tarde esses dados alimentam o
`POST /peca/da-entrevista` da API de Petições (`https://peticoes.nexusdevhub.com`).

**A geração da petição não faz parte do fluxo atual** — por ora só se armazena.
O `gerar_peticao.py` existe e funciona, mas é ferramenta de conferência.

Tudo vive no schema **`peticoes`** do Postgres em `72.60.61.18:5432/postgres`
(credenciais em `.env`, que está no `.gitignore`).

## Estado atual

Banco aplicado e API de ingestão no ar, testados contra o Postgres real
(PostgreSQL 18.4): **75 verificações automatizadas passando** (36 da API + 22 da
amarração com o Chatwoot + 17 do receptor de webhook).

O robô do Chatwoot ("atualiza base de dados") aponta para o n8n, que é onde vive o
agente de IA; o n8n chama esta API. A configuração do lado do n8n está em
[N8N.md](N8N.md).

A ligação com o Chatwoot (`https://maneger.nexusdevhub.com`, conta 1) está
configurada e testada em produção com o token do robô: a API lê as etiquetas
sozinha.

Falta: publicar a API num endereço que o n8n alcance, criar a etiqueta
`retomar-entrevista` no Chatwoot, e — só quando for gerar peça — preencher
`PETICOES_API_KEY`.

## A API de ingestão

```bash
python3 api/servidor.py
```

Sobe em `0.0.0.0:8088`. Autenticação por `X-API-Key` (chave em `INGESTAO_API_KEY`
no `.env`). Biblioteca padrão só — sem framework, porque este ambiente não tem pip.

O endpoint principal é um só, e é tolerante de propósito: um agente de IA acerta
mais com **um endpoint que aceita qualquer subconjunto dos campos** do que com
quinze verbos.

```bash
curl -s -X POST http://localhost:8088/entrevistas \
  -H "X-API-Key: $INGESTAO_API_KEY" -H "Content-Type: application/json" \
  -d '{"chatwoot_account_id":1,"chatwoot_conversation_id":42,
       "chatwoot_contact_id":777,"etiquetas":[],
       "campos":{"RECL_NOME":"Ana Souza","SALARIO":"R$ 2.148,22",
                 "DATA_ADMISSAO":"01/03/2019","tem_adic_noturno":"sim"}}'
```

A resposta traz o que foi gravado **e a próxima pergunta a fazer** — o agente não
precisa de uma segunda chamada para saber o que dizer em seguida:

```json
{
  "entrevista_id": 12, "status": "em_andamento", "criada": true,
  "aceitos": {"salario": "2148.22", "data_admissao": "2019-03-01",
              "tem_adic_noturno": true},
  "erros": [], "ignorados": [],
  "campos_preenchidos": 4, "campos_no_roteiro": 75,
  "proxima_pergunta": {"campo": "RECL_CPF", "pergunta": "Qual o seu CPF?"},
  "vinculo": {"resolvida_por": "conversa"}
}
```

| Rota | Para quê |
|---|---|
| `POST /entrevistas` | criar/atualizar com os campos que já se sabe |
| `GET /entrevistas/{id}` | estado atual |
| `GET /entrevistas?conversa=&conta=` | achar pela conversa do Chatwoot |
| `GET /entrevistas/{id}/proxima-pergunta` | só a próxima pergunta |
| `POST /entrevistas/{id}/pergunta-pendente` | marcar pergunta enviada, aguardando resposta |
| `POST /entrevistas/{id}/documentos` | registrar anexo (holerite, espelho de ponto) |
| `POST /entrevistas/{id}/concluir` | liberar o caso para a geração da peça |
| `GET /entrevistas/{id}/payload` | conferir o JSON que iria para a API de petições |
| `GET /roteiro` | as 75 perguntas, na ordem |
| `GET /saude` | healthcheck, sem chave |
| `GET /openapi.json` | spec para ligar como ferramenta do agente, sem chave |
| `POST /webhook/chatwoot` | recebe eventos do Agent Bot (autenticado por assinatura HMAC) |

### Linguagem natural entra; palpite não sai

O agente repassa o que o cliente escreveu, e a conversão para o tipo da coluna
acontece no serviço: `"12/04/1988"` e `"1 de março de 2019"` viram datas,
`"R$ 2.148,22"` vira numérico, `"sim, de madrugada"` e `"nunca recebi"` viram
booleanos, `"uns 15 minutos antes"` vira `15`, `"indireta"` vira
`rescisao_indireta`.

O que é ambíguo **não é gravado** — volta em `erros` com o motivo:

```json
{"campo": "DATA_RESCISAO", "valor": "faz uns dois anos",
 "motivo": "não entendi a data: 'faz uns dois anos'"}
```

Gravar a data errada é pior do que deixar o campo vazio: a petição sai com avos e
FGTS calculados sobre um período que não existiu. Melhor o agente perguntar de novo.

Campo que não existe no roteiro vai para `ignorados` e fica guardado em
`nao_mapeados` — nada se perde, mas **não entra no payload da petição**.

Há um meio-termo para os campos que a API documenta como coletados mas que o
motor hoje ignora (`armamento_colete`, `produtos`, `epi`, `ferias_quantidade`…):
no roteiro eles têm `coluna = 'extras.<chave>'`, o que os grava no jsonb `extras`
em vez de numa coluna própria. `montar_entrevista` funde `extras` no payload — a
documentação garante que enviar campo desconhecido não causa erro. Coletar agora
custa uma pergunta; descobrir depois que falta custa reentrevistar o cliente.

### Amarração com a conversa

Assunto com armadilhas de verdade (o `id` da conversa no Chatwoot é o
`display_id`, e ele é sequencial **por conta**). A análise completa, com as fontes,
está em [VINCULO.md](VINCULO.md). O essencial:

- **O caso pertence ao contato, não à conversa.** Conversa resolvida e cliente
  voltando noutra conversa continua a mesma entrevista — as respostas anteriores
  não se perdem.
- **`chatwoot_account_id` é obrigatório** junto com o id da conversa (422 sem ele).
- **Caso concluído fica congelado.** Só é retomado se a conversa tiver a etiqueta
  `retomar-entrevista` (configurável em `ETIQUETA_RETOMAR`).

### Testes

```bash
python3 api/teste_api.py
```

```bash
python3 api/teste_vinculo.py
```

```bash
python3 api/teste_webhook.py
```

Os três criam dados de teste e apagam o que criaram no fim. Há também
`python3 api/teste_chatwoot.py`, que confere o Token de acesso do robô contra a
API do Chatwoot (só leitura).

## Como rodar

```bash
python3 db/apply.py            # aplica/reaplica as migrações (idempotente)
```

```bash
python3 db/apply.py --check    # só inspeciona o que já existe no banco
```

```bash
python3 db/rodar_sql.py -c "SELECT * FROM peticoes.vw_fila_geracao"
```

Este Python (3.12 do sistema) não tem `pip`, `venv` nem `ensurepip`, e o PEP 668
bloqueia instalação. O driver `psycopg` 3.3.4 foi extraído das wheels do PyPI para
`.pylibs/`, e os scripts inserem essa pasta no `sys.path`. Nada foi alterado no
Python do sistema. Para recriar `.pylibs` em outra máquina, use um venv normal
(`pip install "psycopg[binary]"`) se lá houver pip.

## Desenho

### O caminho dos dados

```
agente de IA no Chatwoot
   │  POST /entrevistas  (campos aos poucos)          ingestao_log (auditoria)
   ▼
contatos ──┐                    ┌─ entrevista_conversas  (N conversas por caso)
            ├─ entrevistas ─────┼─ entrevista_reclamadas (1ª empregadora, 2ª/3ª tomadoras)
perguntas ──┤   (o caso)        ├─ entrevista_respostas  (o que o cliente escreveu, cru)
(roteiro)   │                   ├─ entrevista_documentos (holerites, espelho de ponto)
            │                   └─ entrevista_blocos     (capítulos revisados → `blocos`)
            │
            └─ peticoes ────────┬─ peticao_verbas                      ┐ depois,
               (1 por           └─ peticao_campos_ausentes ──┐         │ fora do
                tentativa)         api_chamadas              │         ┘ fluxo atual
                                                            └──→ volta a virar pergunta
```

O caso pertence ao **contato**, e as conversas do Chatwoot penduram nele — é o que
faz a entrevista sobreviver quando a conversa é resolvida e o cliente volta noutra.
Detalhes em [VINCULO.md](VINCULO.md).

### Tabelas

| Tabela | Papel |
|---|---|
| `contatos` | espelho mínimo do contato do Chatwoot |
| `entrevistas` | o caso. Uma coluna por campo do objeto `entrevista` + as opções do pedido |
| `entrevista_reclamadas` | as rés, por `ordem` 1–3 — evita triplicar `RECL1_*`/`RECL2_*`/`RECL3_*` |
| `perguntas` | roteiro do bot: 75 perguntas, uma por campo da API, com o efeito de cada ausência |
| `entrevista_respostas` | resposta crua + normalizada, com o `chatwoot_message_id` |
| `entrevista_documentos` | anexos recebidos na conversa |
| `entrevista_blocos` | capítulos corrigidos pelo advogado, reenviados em `blocos` |
| `entrevista_conversas` | as conversas do Chatwoot que alimentaram o caso (N por entrevista) |
| `ingestao_log` | auditoria das requisições recebidas do agente |
| `peticoes` | uma linha por chamada à API: status, valor da causa, rito, payload, resposta, PDF |
| `peticao_verbas` | rubricas calculadas, com total e fundamento |
| `peticao_campos_ausentes` | o que a API apontou como faltante, com flag `reperguntado` |
| `api_chamadas` | auditoria HTTP crua |

Uma decisão que vale explicar: **as reclamadas ficam numa tabela filha**, não em
30 colunas `RECLn_*`. A função `montar_entrevista` reprojeta `ordem → RECL1_*,
RECL2_*, RECL3_*` na hora de montar o JSON, então o banco fica normalizado e a API
recebe o formato que espera.

### Funções

- **`peticoes.montar_entrevista(id) → jsonb`** — monta o objeto `entrevista`.
  `jsonb_strip_nulls` remove o que está vazio, então campo não preenchido é campo
  omitido — e a API responde em `campos_ausentes` o efeito de cada omissão.
  `false` é preservado (é informação, não ausência).
- **`peticoes.montar_payload(id) → jsonb`** — o corpo completo do POST: `entrevista`,
  `codigo`, `salario`, `municipio`, as cinco flags e `blocos` quando houver.
- **`peticoes.registrar_resposta(peticao_id, jsonb)`** — grava o retorno da API,
  explode `verbas` e `campos_ausentes`, e normaliza `rito` (`"ordinário"` → `ordinario`).
- **`peticoes.fmt_brl(numeric) → text`** — `2148.22` → `'R$ 2.148,22'`, formato que a
  API espera no salário.

### Views

| View | Para quê |
|---|---|
| `vw_fila_geracao` | entrevistas `concluida` sem peça aprovada, **com o payload pronto** |
| `vw_entrevistas_progresso` | quantos campos preenchidos vs. 75 do roteiro |
| `vw_peticoes_ultimas` | a última tentativa de cada entrevista, com totais |
| `vw_campos_a_reperguntar` | campo faltante + a pergunta do roteiro, pronta para o chat |
| `vw_entrevista_conversas` | quantas conversas cada caso atravessou, e reaberturas |

## Gerar a petição — fora do fluxo atual

Ferramenta de conferência, não parte do caminho do agente. **Falta a chave**
(`PETICOES_API_KEY` no `.env`) para gerar uma peça de verdade.

```bash
python3 gerar_peticao.py --lista
```

```bash
python3 gerar_peticao.py --id 1 --dry-run
```

```bash
python3 gerar_peticao.py --id 1 --so-calculo
```

`--so-calculo` manda `redigir_ia: false` e `gerar_pdf: false` — devolve valor da
causa e verbas sem custo de redação. Sem essa flag o script pede
`Accept: application/pdf`, salva em `pdfs/CODIGO.pdf` e lê status, valor da causa
e rito dos cabeçalhos `X-Status`, `X-Valor-Causa` e `X-Rito`. Toda chamada grava
uma linha em `peticoes` e outra em `api_chamadas`, incluindo as que falham.

O payload nunca é montado no Python: sai de `peticoes.montar_payload(id)`, então a
requisição reflete exatamente o que está no banco.

## Duas premissas que a documentação 0.8.2 resolveu

Ficam registradas porque explicam o que a migração `007` mexeu:

- **`gratificacao`** estava `text` por não haver definição de tipo. A referência
  separa os dois: `gratificacao` é booleano e `gratificacao_qual` é o texto que
  desambigua gratificação de função × prêmio de assiduidade — verbas com cálculos
  diferentes. A `007` converte a coluna e preserva o texto já coletado em `_qual`.
- **`periodo_antecedente`/`periodo_sucedente`** continuam `integer` em minutos:
  é o que dá para validar e somar. Mas a doc os descreve como texto (`"30 minutos"`),
  então `montar_entrevista` agora manda a unidade junto, em vez de um `30` solto
  que o motor teria de adivinhar.

## Teste de fumaça

```bash
python3 db/rodar_sql.py db/teste_smoke.sql
```

Cria `SMOKE-001` (vigilante, 12x36, rescisão indireta, duas rés) e imprime o payload.
Os dados de teste já foram removidos do banco — rode de novo quando quiser conferir.

E o contrato com a documentação da API de Petições:

```bash
python3 api/teste_cobertura_doc.py
```

Manda os 63 campos documentados pelo mesmo caminho que o agente usa e confere que
todos voltam em `montar_payload`. Não precisa do servidor no ar e não grava nada
(roda em transação, com rollback). É o teste que quebra quando a API 0.8.2 ganhar
um campo novo — que é justamente a falha que não daria erro em lugar nenhum:
a peça só sairia pior.

## Próximo passo

1. **Publicar a API** num endereço que o n8n alcance (hoje escuta em `:8088`, em
   primeiro plano). Falta supervisor (systemd) e proxy com TLS.
2. ~~Preencher `CHATWOOT_BASE_URL` e `CHATWOOT_API_TOKEN`~~ — feito, com o token
   do robô "atualiza base de dados". Confira quando quiser:
   `python3 api/teste_chatwoot.py 1 81`.
3. **Configurar o nó HTTP Request do n8n** conforme [N8N.md](N8N.md), passando os
   identificadores do Chatwoot junto com os campos extraídos pela IA.
4. **Criar a etiqueta `retomar-entrevista`** no Chatwoot — hoje a conta não tem
   nenhuma etiqueta cadastrada. Sem ela, caso concluído nunca é retomado (sempre
   abre caso novo), que é o comportamento seguro mas não o desejado.
