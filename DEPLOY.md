# Subir no Coolify

A API de ingestão é um processo Python de biblioteca padrão + driver do Postgres.
Não escreve em disco, não tem fila, não tem cache: todo o estado está no banco.
Isso torna o deploy simples — um container sem volume, escalável na horizontal.

## O que vai no container

| Arquivo | Papel |
|---|---|
| `Dockerfile` | imagem `python:3.12-slim` + `psycopg[binary]`, usuário sem root |
| `requirements.txt` | a única dependência, na mesma versão testada localmente |
| `docker-entrypoint.sh` | aplica as migrações e sobe a API |
| `.dockerignore` | mantém `.env`, `.mcp.json` e `.pylibs/` fora da imagem |
| `docker-compose.yaml` | alternativa ao build pack Dockerfile, e o jeito de rodar local |

`.pylibs/` fica de fora de propósito: existe porque este ambiente não tem pip.
No container o `pip install` resolve, e mandar as duas coisas junto só criaria
duas cópias do driver disputando o `sys.path`.

## Passo a passo

### 1. Repositório

O Coolify puxa de um repositório git. O projeto ainda não é um:

```bash
git init && git add . && git commit -m "API de ingestão das entrevistas"
```

Confira antes que `.env` **não** entrou (`git status` não deve listá-lo — ele
está no `.gitignore` junto com `.mcp.json`). Depois crie o repositório remoto e
faça o push.

### 2. Criar o recurso no Coolify

- **New Resource → Application → Public/Private Repository**
- **Build Pack:** `Dockerfile`
- **Port:** `8088`
- **Health Check Path:** `/saude`

`/saude` consulta o banco antes de responder: devolve `503` se o Postgres não
atende. Container no ar com banco fora não passa por saudável, que é o
comportamento que se quer num healthcheck.

### 3. Variáveis de ambiente

Na aba **Environment Variables**. As duas primeiras são obrigatórias:

| Variável | Observação |
|---|---|
| `DATABASE_URL` | `postgres://usuario:senha@host:5432/postgres` |
| `INGESTAO_API_KEY` | a chave que o n8n manda em `X-API-Key` |
| `CHATWOOT_BASE_URL` | `https://maneger.nexusdevhub.com` |
| `CHATWOOT_API_TOKEN` | token do robô "atualiza base de dados" |
| `CHATWOOT_ACCOUNT_ID` | `1` |
| `ETIQUETA_RETOMAR` | `retomar-entrevista` |
| `CHATWOOT_WEBHOOK_SECRET` | só se o webhook do robô apontar direto para cá |
| `APLICAR_MIGRACOES` | `0` desliga as migrações no start (padrão: `1`) |

**Sem `INGESTAO_API_KEY` as rotas de gravação respondem `503`.** É proposital: o
serviço grava dado de cliente e não pode ficar aberto porque alguém esqueceu a
variável no deploy. `GET /saude` e `GET /openapi.json` seguem abertos — o
healthcheck e o agente do n8n precisam deles sem chave.

**Sobre o `DATABASE_URL`:** hoje aponta para `72.60.61.18:5432`. Se o Postgres
for um recurso do próprio Coolify, troque pelo hostname interno da rede do
Coolify — o tráfego para de sair para a internet e você não depende do firewall
liberar a porta 5432.

### 4. Migrações

O entrypoint roda `db/apply.py` a cada start. Os `.sql` são idempotentes
(`CREATE ... IF NOT EXISTS`, `ON CONFLICT DO UPDATE`), então reaplicar é seguro
e evita o caso chato: deploy com código novo e schema velho.

Se preferir controlar isso à mão, defina `APLICAR_MIGRACOES=0` e rode
`python3 db/apply.py` você mesmo antes de publicar.

### 5. Depois de subir

```bash
curl -s https://SEU-DOMINIO/saude
```

Deve responder `{"ok": true, "banco": "conectado"}`.

Aí é apontar o nó HTTP Request do n8n para `https://SEU-DOMINIO/entrevistas` e
trocar a URL da ferramenta do agente para `https://SEU-DOMINIO/openapi.json`.

## Rodar local com Docker

```bash
docker compose up --build
```

Ele lê as variáveis do `.env` do diretório. Sem `DATABASE_URL` ou
`INGESTAO_API_KEY` o compose falha na hora, com o nome da variável que falta,
em vez de subir um container quebrado.
