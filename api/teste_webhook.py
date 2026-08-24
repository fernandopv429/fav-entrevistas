#!/usr/bin/env python3
"""Testa o receptor de webhook do Agent Bot (POST /webhook/chatwoot).

Rota opcional: com o webhook do robô apontando para o n8n, quem recebe o evento
do Chatwoot é o n8n. Este endpoint serve para o caso de o Chatwoot chamar este
serviço direto — aí a identificação (contato + vínculo da conversa) é feita pelo
próprio Chatwoot, sem depender de o agente lembrar de mandar os ids.

    python3 api/teste_webhook.py
"""
import hashlib
import hmac
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / ".pylibs"))
sys.path.insert(0, str(RAIZ / "api"))

import teste_api  # noqa: E402
from teste_api import chamar, conferir, env, falhas  # noqa: E402
import dados  # noqa: E402

CONTA = 9
CONTATO = 77_0001
CONV = 77_1001
SEGREDO = env("CHATWOOT_WEBHOOK_SECRET")


def evento_message_created(conversa=CONV, etiquetas=None, tipo="incoming"):
    """Payload no formato que o Chatwoot manda (message.webhook_data)."""
    conversa_obj = {
        "id": conversa, "display_id": conversa,
        "uuid": "3f1c9e9a-0000-4000-8000-%012d" % conversa,
        "inbox_id": 3,
    }
    if etiquetas is not None:
        conversa_obj["labels"] = etiquetas
    return {
        "event": "message_created",
        "id": 55_000 + conversa,
        "content": "Bom dia, fui demitido e quero entrar com processo",
        "message_type": tipo,
        "account": {"id": CONTA, "name": "Escritório"},
        "inbox": {"id": 3, "name": "WhatsApp"},
        "conversation": conversa_obj,
        "sender": {"id": CONTATO, "name": "Webhook Teste",
                   "phone_number": "+5511977770001"},
    }


def enviar(evento, segredo=SEGREDO, timestamp=None, corromper=False):
    corpo = json.dumps(evento, ensure_ascii=False).encode("utf-8")
    ts = str(int(timestamp if timestamp is not None else time.time()))
    mac = hmac.new(segredo.encode(), ts.encode() + b"." + corpo, hashlib.sha256)
    sig = "sha256=" + mac.hexdigest()
    if corromper:
        sig = "sha256=" + "0" * 64
    req = urllib.request.Request(
        teste_api.BASE + "/webhook/chatwoot", data=corpo, method="POST",
        headers={"Content-Type": "application/json",
                 "X-Chatwoot-Signature": sig,
                 "X-Chatwoot-Timestamp": ts,
                 "X-Chatwoot-Delivery": "entrega-de-teste"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def limpar():
    with dados.conectar() as conn, conn.cursor() as cur:
        cur.execute("""
            DELETE FROM peticoes.entrevistas WHERE contato_id IN (
                SELECT id FROM peticoes.contatos
                WHERE chatwoot_account_id = %s AND chatwoot_contact_id = %s)
        """, (CONTA, CONTATO))
        cur.execute("""DELETE FROM peticoes.contatos
                       WHERE chatwoot_account_id = %s AND chatwoot_contact_id = %s""",
                    (CONTA, CONTATO))
        conn.commit()


def main():
    limpar()
    print(f"testando o receptor de webhook em {teste_api.BASE}\n")

    print("assinatura")
    s, d = enviar(evento_message_created(), corromper=True)
    conferir("assinatura errada é recusada (401)", s == 401, (s, d.get("erro", "")[:50]))

    s, d = enviar(evento_message_created(), segredo="outro-segredo")
    conferir("segredo diferente é recusado (401)", s == 401, s)

    s, d = enviar(evento_message_created(), timestamp=time.time() - 3600)
    conferir("entrega velha é recusada (proteção contra replay)", s == 401,
             (s, d.get("erro", "")[:60]))

    print("\nsem assinatura nenhuma")
    corpo = json.dumps(evento_message_created()).encode()
    req = urllib.request.Request(teste_api.BASE + "/webhook/chatwoot", data=corpo,
                                method="POST",
                                headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            s = r.status
    except urllib.error.HTTPError as e:
        s = e.code
    conferir("sem cabeçalho de assinatura é recusado (401)", s == 401, s)

    print("\nevento válido, mas ainda sem caso para o contato")
    s, d = enviar(evento_message_created())
    conferir("assinatura correta é aceita (200)", s == 200, s)
    conferir("registra que ainda não há caso", d.get("vinculada") is False, d)
    conferir("devolve o delivery id", d.get("entrega") == "entrega-de-teste", d.get("entrega"))

    with dados.conectar() as conn, conn.cursor() as cur:
        cur.execute("""SELECT count(*) AS n FROM peticoes.contatos
                       WHERE chatwoot_account_id = %s AND chatwoot_contact_id = %s""",
                    (CONTA, CONTATO))
        conferir("contato do Chatwoot foi registrado", cur.fetchone()["n"] == 1)

    print("\nagora o agente cria o caso; o webhook passa a vinculá-lo")
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_account_id": CONTA, "chatwoot_conversation_id": CONV,
        "chatwoot_contact_id": CONTATO, "etiquetas": [],
        "campos": {"RECL_NOME": "Webhook Teste"}})
    eid = d.get("entrevista_id")
    conferir("caso criado pelo agente", s == 201 and eid, s)

    s, d = enviar(evento_message_created())
    conferir("webhook vincula ao caso existente",
             d.get("vinculada") is True and d.get("entrevista_id") == eid, d)

    print("\nconversa NOVA chegando pelo webhook (a anterior foi resolvida)")
    s, d = enviar(evento_message_created(conversa=77_1002))
    conferir("conversa nova cai no mesmo caso",
             d.get("entrevista_id") == eid, (d.get("entrevista_id"), eid))
    conferir("resolvida pelo contato",
             d.get("resolvida_por") == "contato_caso_aberto", d.get("resolvida_por"))

    with dados.conectar() as conn, conn.cursor() as cur:
        cur.execute("""SELECT count(*) AS n, count(chatwoot_conversation_uuid) AS com_uuid
                       FROM peticoes.entrevista_conversas WHERE entrevista_id = %s""",
                    (eid,))
        r = cur.fetchone()
        conferir("duas conversas vinculadas", r["n"] == 2, r)
        conferir("uuid da conversa foi guardado", r["com_uuid"] == 2, r)

    print("\nmensagem do atendente (outgoing) é ignorada")
    s, d = enviar(evento_message_created(tipo="outgoing"))
    conferir("outgoing não é processada", d.get("ignorado") is True, d)

    print("\netiquetas vindas no payload são aproveitadas")
    s, d = enviar(evento_message_created(conversa=77_1003, etiquetas=["urgente", "trabalhista"]))
    conferir("evento com etiquetas aceito", s == 200, s)
    with dados.conectar() as conn, conn.cursor() as cur:
        cur.execute("""SELECT etiquetas FROM peticoes.entrevista_conversas
                       WHERE chatwoot_conversation_id = %s""", (77_1003,))
        r = cur.fetchone()
        conferir("etiquetas gravadas no vínculo",
                 r and sorted(r["etiquetas"] or []) == ["trabalhista", "urgente"], r)

    limpar()
    print("  (dados de teste removidos)")
    print(f"\n{teste_api.ok} passaram, {len(falhas)} falharam")
    if falhas:
        for f in falhas:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
