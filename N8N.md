# Ligando o n8n a esta API

O robô do Chatwoot ("atualiza base de dados") aponta o webhook para o n8n. Então
o caminho é:

```
Chatwoot (Agent Bot)  ──assinado com o Segredo do Webhook──▶  n8n
                                                               │
                                        agente de IA extrai os campos da conversa
                                                               │
                                                    HTTP Request  ──X-API-Key──▶  esta API  ──▶  Postgres
```

Cada peça tem um papel:

| Peça | Papel |
|---|---|
| **Segredo do Webhook** (robô) | o n8n valida que o evento veio mesmo do Chatwoot |
| **Token de acesso** (robô) | ler etiquetas da conversa e escrever de volta no Chatwoot |
| **`INGESTAO_API_KEY`** (.env) | o n8n se autentica nesta API |

## 1. Validar a assinatura no n8n

O Chatwoot assina cada entrega ([lib/webhooks/trigger.rb](https://raw.githubusercontent.com/chatwoot/chatwoot/develop/lib/webhooks/trigger.rb)):

```
X-Chatwoot-Signature: sha256=<hex>
X-Chatwoot-Timestamp: <unix>
X-Chatwoot-Delivery:  <uuid>

sha256 = HMAC-SHA256(segredo, "<timestamp>.<corpo cru>")
```

Num nó **Code** logo depois do Webhook:

```javascript
const crypto = require('crypto');
const segredo = 'COLE_AQUI_O_SEGREDO_DO_WEBHOOK';

const cru = JSON.stringify($input.first().json.body);   // ver a ressalva abaixo
const ts  = $input.first().json.headers['x-chatwoot-timestamp'];
const rec = $input.first().json.headers['x-chatwoot-signature'];

const esperado = 'sha256=' + crypto
  .createHmac('sha256', segredo)
  .update(ts + '.' + cru)
  .digest('hex');

if (rec !== esperado) throw new Error('assinatura inválida');
return $input.all();
```

**Ressalva importante:** o HMAC é calculado sobre o corpo **cru**, byte a byte.
`JSON.stringify` reserializa e pode mudar espaçamento ou ordem de chaves,
invalidando a comparação. No nó Webhook do n8n, ative **Raw Body** e use o corpo
cru; sem isso a validação vai falhar de forma intermitente e confusa.

Se preferir não lidar com isso no n8n, aponte o webhook do robô direto para
`POST /webhook/chatwoot` desta API, que já faz a validação (17 testes cobrindo
assinatura errada, segredo diferente, entrega velha e ausência de cabeçalho) —
veja a seção 5.

## 2. O que o n8n recebe do Chatwoot

No evento `message_created`, os campos que importam:

| Campo no payload | Para quê |
|---|---|
| `account.id` | **obrigatório** — o id da conversa é sequencial por conta |
| `conversation.display_id` | o id da conversa (é o que o Chatwoot mostra como "id") |
| `conversation.uuid` | único global; mande sempre que vier |
| `inbox.id` | caixa de entrada |
| `sender.id` / `sender.name` / `sender.phone_number` | o contato |
| `id` | id da mensagem — serve de chave de idempotência |
| `message_type` | processe só `incoming` (mensagem do cliente) |
| `content` | o texto que o agente de IA vai interpretar |

## 3. O nó HTTP Request

- **Method:** `POST`
- **URL:** `https://SEU-ENDERECO/entrevistas`
- **Headers:** `X-API-Key: <INGESTAO_API_KEY>` e `Content-Type: application/json`
- **Body (JSON):**

```json
{
  "chatwoot_account_id":       {{ $json.body.account.id }},
  "chatwoot_conversation_id":  {{ $json.body.conversation.display_id }},
  "chatwoot_conversation_uuid": "{{ $json.body.conversation.uuid }}",
  "chatwoot_inbox_id":         {{ $json.body.inbox.id }},
  "chatwoot_contact_id":       {{ $json.body.sender.id }},
  "mensagem_id":               {{ $json.body.id }},
  "contato": {
    "chatwoot_contact_id": {{ $json.body.sender.id }},
    "nome":                "{{ $json.body.sender.name }}",
    "telefone":            "{{ $json.body.sender.phone_number }}"
  },
  "etiquetas": {{ JSON.stringify($json.etiquetas || []) }},
  "campos": {{ JSON.stringify($json.campos_extraidos) }}
}
```

Ajuste os prefixos de expressão ao seu fluxo (`$json.body...` vale para o nó
Webhook; se houver nós no meio, use `$('Nome do Nó').item.json...`).

`campos_extraidos` é o que o agente de IA produz — só os campos que ele
identificou na mensagem, com os nomes do roteiro. Exemplo:

```json
{"RECL_NOME": "Ana Souza", "FUNCAO": "Auxiliar de limpeza",
 "DATA_ADMISSAO": "01/03/2019", "SALARIO": "R$ 2.148,22",
 "tem_adic_noturno": "sim, trabalhava de madrugada"}
```

Os valores vão **como o cliente falou**. A conversão para data, número e booleano
acontece nesta API. O agente não deve normalizar nada — nem inventar valor para
campo que o cliente não informou.

Os nomes válidos dos campos estão em `GET /openapi.json` (75 campos documentados,
com descrição de cada um) ou em `GET /roteiro`.

## 4. O que o agente de IA deve fazer com a resposta

```json
{
  "entrevista_id": 12, "status": "em_andamento",
  "aceitos": {"salario": "2148.22", "data_admissao": "2019-03-01"},
  "erros": [{"campo": "DATA_RESCISAO", "valor": "faz uns dois anos",
             "motivo": "não entendi a data: 'faz uns dois anos'"}],
  "ignorados": [],
  "campos_preenchidos": 12, "campos_no_roteiro": 75,
  "proxima_pergunta": {"campo": "RECL_CPF", "pergunta": "Qual o seu CPF?"},
  "vinculo": {"resolvida_por": "conversa"}
}
```

Três regras para o prompt do agente:

1. **`proxima_pergunta.pergunta`** é o que ele deve perguntar em seguida. Vem
   pronta, na ordem do roteiro.
2. **`erros`** não foi gravado. Ele precisa perguntar de novo, de forma mais
   específica ("o último dia de trabalho foi em que mês e ano?"). Nunca insistir
   com o mesmo valor.
3. **`ignorados`** é campo que não existe no roteiro. Fica guardado à parte, não
   entra na petição. Se aparecer com frequência, o prompt está inventando nomes
   de campo.

E ao fim da entrevista: `POST /entrevistas/{id}/concluir`.

## 5. Alternativa: Chatwoot chamando esta API direto

O receptor `POST /webhook/chatwoot` existe e está testado. Ele **não interpreta o
texto** — isso é do agente. O que faz é garantir a identificação: registra o
contato e amarra a conversa ao caso.

Isso resolve um risco concreto do arranjo com n8n: se o fluxo falhar, ou se o
agente esquecer de mandar os ids, o vínculo conversa↔caso não se forma. Com o
Chatwoot chamando os dois destinos, a identificação fica garantida pelo próprio
Chatwoot, e o n8n cuida só da parte de IA.

Para usar, adicione um segundo robô no Chatwoot apontando para
`https://SEU-ENDERECO/webhook/chatwoot` e ponha o segredo dele em
`CHATWOOT_WEBHOOK_SECRET` no `.env`.

## 6. Token de acesso do robô: já configurado

`CHATWOOT_BASE_URL=https://maneger.nexusdevhub.com` e `CHATWOOT_API_TOKEN` com o
token do robô "atualiza base de dados" (id 2) já estão no `.env`, **testados
contra o Chatwoot de produção**: `conversations#show` e `labels#index` respondem.

Ou seja, **a API lê as etiquetas sozinha** — o n8n não precisa passar `etiquetas`.
Se passar, o que vier do n8n tem precedência.

Usei o token do robô, não o token pessoal: os dois funcionam, mas o pessoal dá
acesso total à conta (apagar conversas, contatos, tudo), enquanto o do robô fica
restrito à lista branca abaixo. Para um serviço que só precisa ler etiquetas, o
pessoal é privilégio demais.

O Chatwoot restringe token de robô a uma lista branca
([access_token_auth_helper.rb](https://raw.githubusercontent.com/chatwoot/chatwoot/develop/app/controllers/concerns/access_token_auth_helper.rb)),
o que é bom: menos privilégio que um token de admin, e o que precisamos está lá.

```
conversations             -> show, update, custom_attributes, toggle_status,
                             toggle_priority, toggle_typing_status, create
conversations/labels      -> index, create
conversations/messages    -> create
conversations/assignments -> create
```

Confira o token com:

```bash
python3 api/teste_chatwoot.py
```

**Cuidado com `labels`:** o POST **sobrescreve a lista inteira** da conversa
([docs](https://developers.chatwoot.com/api-reference/conversations/add-labels)).
As funções `adicionar_etiqueta`/`remover_etiqueta` em `api/chatwoot.py` leem antes
e reescrevem o conjunto completo — mandar só a etiqueta nova apagaria as que o
time colocou. Se for mexer em etiquetas pelo n8n, faça o mesmo.
