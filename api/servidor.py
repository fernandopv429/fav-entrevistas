#!/usr/bin/env python3
"""API de ingestão das entrevistas — consumida pelo agente de IA do Chatwoot.

    python3 api/servidor.py            # sobe em 0.0.0.0:8088

Autenticação: cabeçalho X-API-Key, conferido contra INGESTAO_API_KEY do ambiente
(ou do .env). Sem a variável, as rotas autenticadas respondem 503 — falha fechada:
o serviço grava dado de cliente e não pode ficar aberto por esquecimento no deploy.
GET /saude e GET /openapi.json seguem abertos.

O endpoint principal é POST /entrevistas: aceita qualquer subconjunto dos campos,
grava o que entendeu e devolve o que ainda falta e qual a próxima pergunta — o
agente não precisa de uma segunda chamada para saber o que dizer em seguida.

Biblioteca padrão só, sem dependência de framework (este ambiente não tem pip).
"""
import datetime
import decimal
import hmac
import json
import os
import pathlib
import sys
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = pathlib.Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(RAIZ / ".pylibs"))

import assinatura  # noqa: E402
import chatwoot  # noqa: E402
import dados  # noqa: E402

LIMITE_CORPO = 1 << 20        # 1 MB


def env(chave, padrao=None):
    if (v := os.environ.get(chave)):
        return v
    arq = RAIZ / ".env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                if k.strip() == chave:
                    return v.strip().strip('"').strip("'") or padrao
    return padrao


def json_serial(o):
    if isinstance(o, (datetime.date, datetime.datetime)):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return str(o)
    return str(o)


DOCS = {
    "servico": "API de ingestão de entrevistas trabalhistas",
    "auth": "cabeçalho X-API-Key",
    "observacao": ("Nenhum campo além de RECL_NOME é obrigatório. Mande os campos "
                   "aos poucos, conforme o cliente responde: cada chamada grava o "
                   "que veio e devolve a próxima pergunta."),
    "endpoints": [
        {"rota": "POST /entrevistas",
         "para": "criar ou atualizar uma entrevista com os campos que já se sabe",
         "corpo": {"chatwoot_conversation_id": 9001, "chatwoot_account_id": 1,
                   "campos": {"RECL_NOME": "João da Silva", "FUNCAO": "Vigilante",
                              "escala": "12x36", "tem_adic_noturno": "sim",
                              "DATA_ADMISSAO": "01/03/2019", "SALARIO": "R$ 2.148,22"}},
         "devolve": "estado da entrevista, campos aceitos/ignorados/com erro, faltando, proxima_pergunta"},
        {"rota": "GET /entrevistas/{id}", "para": "estado atual de uma entrevista"},
        {"rota": "GET /entrevistas?conversa={id}&conta={id}",
         "para": "achar a entrevista de uma conversa do Chatwoot"},
        {"rota": "GET /entrevistas/{id}/proxima-pergunta",
         "para": "só a próxima pergunta do roteiro"},
        {"rota": "POST /entrevistas/{id}/pergunta-pendente",
         "para": "marcar que a pergunta foi enviada e aguarda resposta",
         "corpo": {"campo": "RECL_CPF"}},
        {"rota": "POST /entrevistas/{id}/documentos",
         "para": "registrar anexo recebido na conversa",
         "corpo": {"tipo": "holerite", "nome_arquivo": "holerite.pdf", "url": "https://...", "chatwoot_message_id": 9912}},
        {"rota": "POST /entrevistas/{id}/concluir",
         "para": "marcar a entrevista como concluída (libera a geração da peça)"},
        {"rota": "GET /entrevistas/{id}/payload",
         "para": "conferir o JSON que será enviado à API de petições"},
        {"rota": "GET /roteiro", "para": "todas as perguntas, na ordem"},
        {"rota": "GET /saude", "para": "healthcheck (sem autenticação)"},
        {"rota": "GET /openapi.json", "para": "especificação OpenAPI para ligar como ferramenta do agente"},
    ],
    "formatos_aceitos": {
        "booleano": "sim/não, true/false, 'recebia', 'nunca'",
        "data": "AAAA-MM-DD, DD/MM/AAAA, '12 de abril de 1988'",
        "moeda": "'R$ 2.148,22', '2148.22', 2148.22",
        "numero": "'15 minutos' -> 15",
        "escolha": "aceita o valor da lista ou parte dele ('indireta' -> rescisao_indireta)",
    },
}


class Handler(BaseHTTPRequestHandler):
    server_version = "IngestaoEntrevistas/1.0"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- utilidades
    def _responder(self, status, corpo, eid=None, corpo_req=None):
        dados_bytes = json.dumps(corpo, ensure_ascii=False, default=json_serial,
                                 indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(dados_bytes)))
        self.end_headers()
        self.wfile.write(dados_bytes)
        ms = int((time.monotonic() - self._inicio) * 1000)
        if self.path != "/saude":
            self._logar(eid, status, corpo_req, corpo, ms)

    def _logar(self, eid, status, corpo_req, resposta, ms):
        try:
            with dados.conectar() as conn, conn.cursor() as cur:
                dados.logar(cur, eid, self.command, self.path, status,
                            corpo_req, resposta, self.client_address[0], ms)
                conn.commit()
        except Exception:
            pass

    def _painel(self, resto, query):
        """Página do painel e os dados que ela lê."""
        esperado = env("PAINEL_TOKEN")
        if not esperado:
            return self._erro(503, "PAINEL_TOKEN não configurado no ambiente do serviço")
        if not hmac.compare_digest((query.get("t") or [""])[0], esperado):
            return self._erro(401, "token do painel ausente ou inválido")

        if not resto:                       # GET /painel  -> a página
            try:
                html = (AQUI / "painel.html").read_bytes()
            except OSError as e:
                return self._erro(500, f"não consegui ler painel.html: {e}")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            # o Chatwoot embute num iframe: sem isto o navegador recusa
            self.send_header("X-Frame-Options", "ALLOWALL")
            self.end_headers()
            self.wfile.write(html)
            return

        if resto != ["dados"]:
            return self._erro(404, "rota do painel desconhecida")

        try:
            conta = int((query.get("conta") or ["0"])[0])
            conversa = int((query.get("conversa") or ["0"])[0])
        except ValueError:
            return self._erro(422, "conta e conversa precisam ser números")
        if not (conta and conversa):
            return self._erro(422, "informe conta e conversa")

        with dados.conectar() as conn, conn.cursor() as cur:
            return self._responder(200, dados.painel(cur, conta, conversa))

    def _erro(self, status, mensagem, extra=None, corpo_req=None):
        corpo = {"erro": mensagem}
        if extra:
            corpo.update(extra)
        self._responder(status, corpo, corpo_req=corpo_req)

    def _ler_corpo(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > LIMITE_CORPO:
            raise ValueError("corpo maior que 1 MB")
        self._corpo_bruto = b""
        if not tamanho:
            return {}
        bruto = self.rfile.read(tamanho)
        self._corpo_bruto = bruto
        try:
            corpo = json.loads(bruto.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON inválido: {e}") from e
        if not isinstance(corpo, dict):
            raise ValueError("o corpo deve ser um objeto JSON")
        return corpo

    def _autorizado(self):
        recebida = self.headers.get("X-API-Key") or ""
        return hmac.compare_digest(recebida, env("INGESTAO_API_KEY") or "")

    def log_message(self, formato, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), formato % args))

    # ----------------------------------------------------------------- rotas
    def do_GET(self):
        self._despachar("GET")

    def do_POST(self):
        self._despachar("POST")

    def _despachar(self, metodo):
        self._inicio = time.monotonic()
        rota = urllib.parse.urlparse(self.path)
        partes = [p for p in rota.path.strip("/").split("/") if p]
        query = urllib.parse.parse_qs(rota.query)

        # rotas abertas: docs, healthcheck e a spec (o agente carrega por URL)
        if metodo == "GET" and partes == ["openapi.json"]:
            try:
                spec = json.loads((AQUI / "openapi.json").read_text(encoding="utf-8"))
                return self._responder(200, spec)
            except Exception as e:
                return self._erro(500, f"não consegui ler openapi.json: {e}")

        if metodo == "GET" and (not partes or partes == ["saude"]):
            if partes == ["saude"]:
                try:
                    with dados.conectar() as conn, conn.cursor() as cur:
                        cur.execute("SELECT 1 AS ok")
                        cur.fetchone()
                    return self._responder(200, {"ok": True, "banco": "conectado"})
                except Exception as e:
                    return self._responder(503, {"ok": False, "banco": str(e)})
            return self._responder(200, DOCS)

        if metodo == "POST" and partes == ["webhook", "chatwoot"]:
            return self._webhook_chatwoot()

        # Painel embutido no Chatwoot. Fica antes da checagem de X-API-Key
        # porque quem carrega é o navegador do atendente, dentro de um iframe —
        # não dá para mandar cabeçalho. A porta é o PAINEL_TOKEN na querystring.
        if metodo == "GET" and partes and partes[0] == "painel":
            return self._painel(partes[1:], query)

        # Sem chave no ambiente a API recusa, em vez de ficar aberta: este
        # serviço grava dado de cliente, e um deploy que esqueceu a variável não
        # pode virar endpoint público de escrita.
        if not env("INGESTAO_API_KEY"):
            return self._erro(503, "INGESTAO_API_KEY não configurada no ambiente do serviço")

        if not self._autorizado():
            return self._erro(401, "X-API-Key ausente ou inválida")

        try:
            corpo = self._ler_corpo() if metodo == "POST" else {}
        except ValueError as e:
            return self._erro(422, str(e))

        try:
            with dados.conectar() as conn, conn.cursor() as cur:
                resultado = self._rotear(metodo, partes, query, corpo, cur)
                if resultado is None:
                    conn.rollback()
                    return self._erro(404, f"rota não encontrada: {metodo} {rota.path}")
                status, resposta, eid = resultado
                if status < 400:
                    conn.commit()
                else:
                    conn.rollback()
                return self._responder(status, resposta, eid=eid, corpo_req=corpo)
        except ValueError as e:
            return self._erro(422, str(e), corpo_req=corpo)
        except Exception as e:
            traceback.print_exc()
            return self._erro(500, "erro interno", {"detalhe": str(e)}, corpo_req=corpo)

    def _webhook_chatwoot(self):
        """Recebe eventos do Agent Bot do Chatwoot.

        Não interpreta o texto da mensagem — isso é do agente de IA. O que faz é
        garantir a identificação: registra o contato e amarra a conversa ao caso
        existente. Assim o vínculo conversa<->caso é estabelecido pelo próprio
        Chatwoot, sem depender de o agente lembrar de mandar os ids.
        """
        try:
            corpo = self._ler_corpo()
        except ValueError as e:
            return self._erro(422, str(e))

        segredo = env("CHATWOOT_WEBHOOK_SECRET")
        try:
            entrega = assinatura.conferir(self._corpo_bruto, self.headers, segredo)
        except assinatura.AssinaturaInvalida as e:
            return self._erro(401, f"assinatura do webhook inválida: {e}")

        evento = corpo.get("event") or "?"
        dados_ident = assinatura.extrair_do_evento(corpo)

        # só mensagem de entrada do cliente interessa; o resto é ruído
        if evento == "message_created" and corpo.get("message_type") not in (
                "incoming", 0, None):
            return self._responder(200, {"ignorado": True, "evento": evento,
                                         "motivo": "mensagem não é do cliente"})

        try:
            with dados.conectar() as conn, conn.cursor() as cur:
                dados.garantir_contato(cur, dados_ident)
                entrevista, motivo = dados.achar_entrevista(cur, dados_ident)
                ids = dados.ids_chatwoot(dados_ident)

                resposta = {"evento": evento, "entrega": entrega,
                            "resolvida_por": motivo}
                if entrevista is not None and motivo != "contato_caso_fechado":
                    dados.vincular_conversa(cur, entrevista["id"], ids,
                                            dados_ident.get("etiquetas"))
                    resposta.update(vinculada=True, entrevista_id=entrevista["id"],
                                    status=entrevista["status"])
                else:
                    resposta.update(
                        vinculada=False,
                        observacao=("nenhum caso em aberto para este contato; a "
                                    "entrevista será criada quando o agente "
                                    "enviar RECL_NOME"))
                conn.commit()
                return self._responder(200, resposta,
                                       eid=entrevista["id"] if entrevista else None,
                                       corpo_req=corpo)
        except Exception as e:
            traceback.print_exc()
            # 200 de propósito: erro nosso não deve virar tempestade de reentrega
            return self._responder(200, {"erro_interno": str(e), "evento": evento})

    def _rotear(self, metodo, partes, query, corpo, cur):
        """Devolve (status, resposta, entrevista_id) ou None se a rota não existe."""

        if metodo == "GET" and partes == ["roteiro"]:
            cat = dados.roteiro(cur)
            return 200, {"total": len(cat["lista"]), "perguntas": cat["lista"]}, None

        if partes and partes[0] == "entrevistas":
            # POST /entrevistas
            if metodo == "POST" and len(partes) == 1:
                estado = dados.gravar(cur, corpo)
                return (201 if estado["criada"] else 200), estado, estado["entrevista_id"]

            # GET /entrevistas?conversa=&conta=
            if metodo == "GET" and len(partes) == 1:
                busca = {}
                if query.get("conversa"):
                    busca["chatwoot_conversation_id"] = query["conversa"][0]
                if query.get("conta"):
                    busca["chatwoot_account_id"] = query["conta"][0]
                if query.get("codigo"):
                    busca["codigo"] = query["codigo"][0]
                if not busca:
                    return 422, {"erro": "informe ?conversa= (e opcionalmente "
                                         "&conta=) ou ?codigo="}, None
                e, motivo = dados.achar_entrevista(cur, busca)
                if not e:
                    return 404, {"erro": "nenhuma entrevista para esses parâmetros",
                                 "encontrada": False}, None
                estado = dados.ler_estado(cur, e["id"])
                estado["vinculo"] = {"resolvida_por": motivo}
                return 200, estado, e["id"]

            if len(partes) >= 2:
                try:
                    eid = int(partes[1])
                except ValueError:
                    return 422, {"erro": f"id inválido: {partes[1]!r}"}, None

                estado = dados.ler_estado(cur, eid)
                if estado is None:
                    return 404, {"erro": f"entrevista {eid} não encontrada"}, None

                if metodo == "GET" and len(partes) == 2:
                    return 200, estado, eid

                if metodo == "POST" and len(partes) == 2:
                    corpo["entrevista_id"] = eid
                    return 200, dados.gravar(cur, corpo), eid

                if metodo == "GET" and partes[2] == "proxima-pergunta":
                    return 200, {"entrevista_id": eid,
                                 "proxima_pergunta": estado["proxima_pergunta"],
                                 "faltando": len(estado["faltando"])}, eid

                if metodo == "GET" and partes[2] == "payload":
                    cur.execute("SELECT peticoes.montar_payload(%s) AS p", (eid,))
                    return 200, {"entrevista_id": eid,
                                 "payload": cur.fetchone()["p"]}, eid

                if metodo == "POST" and partes[2] == "pergunta-pendente":
                    campo = corpo.get("campo")
                    if not campo:
                        return 422, {"erro": "informe o campo da pergunta enviada"}, eid
                    dados.marcar_pendente(cur, eid, campo)
                    return 200, dados.ler_estado(cur, eid), eid

                if metodo == "POST" and partes[2] == "concluir":
                    dados.concluir(cur, eid)
                    novo = dados.ler_estado(cur, eid)
                    novo["aviso"] = (
                        None if not novo["faltando"] else
                        f"concluída com {len(novo['faltando'])} campo(s) em branco; "
                        "a peça sai assim mesmo, a API de petições aponta o efeito de cada ausência")
                    return 200, novo, eid

                if metodo == "POST" and partes[2] == "documentos":
                    doc_id = dados.registrar_documento(cur, eid, corpo)
                    return 201, {"documento_id": doc_id, "entrevista_id": eid}, eid

        return None


def main():
    porta = int(env("INGESTAO_PORT", "8088"))
    host = env("INGESTAO_HOST", "0.0.0.0")
    if not env("INGESTAO_API_KEY"):
        print("AVISO: INGESTAO_API_KEY não definida — as rotas de gravação vão "
              "responder 503 até a variável ser configurada", file=sys.stderr)
    servidor = ThreadingHTTPServer((host, porta), Handler)
    servidor.daemon_threads = True
    print(f"ingestão de entrevistas em http://{host}:{porta}  (Ctrl-C para parar)")
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrando")
        servidor.shutdown()


if __name__ == "__main__":
    main()
