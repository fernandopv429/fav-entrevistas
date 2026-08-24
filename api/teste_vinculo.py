#!/usr/bin/env python3
"""Testa a amarração entrevista <-> conversa do Chatwoot.

O cenário que motiva este arquivo: no Chatwoot a conversa é uma sessão de
atendimento. Ela é resolvida e, quando o cliente volta a falar, abre OUTRA
conversa com novo display_id. A entrevista tem 75 campos e não se preenche numa
sentada — então o caso precisa sobreviver à troca de conversa.

    python3 api/teste_vinculo.py
"""
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / ".pylibs"))
sys.path.insert(0, str(RAIZ / "api"))

from teste_api import chamar, conferir, falhas  # noqa: E402
import teste_api  # noqa: E402
import dados  # noqa: E402

CONTA = 7
CONTATO = 88_0001
CONTATO_B = 88_0002
CONV_1, CONV_2, CONV_3 = 88_1001, 88_1002, 88_1003


def limpar():
    with dados.conectar() as conn, conn.cursor() as cur:
        cur.execute("""
            DELETE FROM peticoes.entrevistas WHERE contato_id IN (
                SELECT id FROM peticoes.contatos
                WHERE chatwoot_account_id = %s AND chatwoot_contact_id IN (%s,%s))
        """, (CONTA, CONTATO, CONTATO_B))
        cur.execute("""
            DELETE FROM peticoes.contatos
            WHERE chatwoot_account_id = %s AND chatwoot_contact_id IN (%s,%s)
        """, (CONTA, CONTATO, CONTATO_B))
        conn.commit()


def main():
    limpar()
    print(f"testando amarração em {teste_api.BASE}\n")

    print("conta obrigatória junto com a conversa")
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_conversation_id": CONV_1,
        "campos": {"RECL_NOME": "Sem Conta"}})
    conferir("conversa sem account_id é recusada (422)", s == 422, d.get("erro", "")[:60])

    print("\nconversa 1: início da entrevista")
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_account_id": CONTA, "chatwoot_conversation_id": CONV_1,
        "chatwoot_contact_id": CONTATO,
        "contato": {"chatwoot_contact_id": CONTATO, "nome": "Carlos Retomada",
                    "telefone": "+5511900000001"},
        "etiquetas": [],
        "campos": {"RECL_NOME": "Carlos Retomada", "FUNCAO": "Porteiro",
                   "escala": "12x36"}})
    conferir("entrevista criada", s == 201 and d.get("criada") is True, s)
    eid = d.get("entrevista_id")
    conferir("resolvida_por = nenhuma (primeira vez)",
             d.get("vinculo", {}).get("resolvida_por") == "nenhuma",
             d.get("vinculo"))

    print("\nmesma conversa, mais respostas")
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_account_id": CONTA, "chatwoot_conversation_id": CONV_1,
        "etiquetas": [], "campos": {"RECL_CPF": "222.333.444-55"}})
    conferir("reusa a entrevista pela conversa",
             d.get("entrevista_id") == eid
             and d.get("vinculo", {}).get("resolvida_por") == "conversa",
             d.get("vinculo"))

    print("\nCONVERSA NOVA, mesmo contato (a conversa anterior foi resolvida)")
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_account_id": CONTA, "chatwoot_conversation_id": CONV_2,
        "chatwoot_contact_id": CONTATO, "etiquetas": [],
        "campos": {"RECL_RG": "33.444.555-6"}})
    conferir("continua a MESMA entrevista (não perde as respostas)",
             d.get("entrevista_id") == eid, (d.get("entrevista_id"), eid))
    conferir("resolvida pelo caso aberto do contato",
             d.get("vinculo", {}).get("resolvida_por") == "contato_caso_aberto",
             d.get("vinculo"))
    conferir("criada = False", d.get("criada") is False)

    with dados.conectar() as conn, conn.cursor() as cur:
        cur.execute("""SELECT count(*) AS n FROM peticoes.entrevista_conversas
                       WHERE entrevista_id = %s""", (eid,))
        conferir("as duas conversas ficaram vinculadas ao caso",
                 cur.fetchone()["n"] == 2)

    print("\nconversa já vinculada não é roubada por outro caso")
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_account_id": CONTA, "chatwoot_conversation_id": CONV_1,
        "chatwoot_contact_id": CONTATO_B, "etiquetas": [],
        "campos": {"RECL_NOME": "Outro Cliente"}})
    conferir("conversa 1 continua apontando para o caso original",
             d.get("entrevista_id") == eid, d.get("entrevista_id"))

    print("\ncaso concluído: sem etiqueta abre caso novo")
    s, d = chamar("POST", f"/entrevistas/{eid}/concluir")
    conferir("caso concluído", d.get("status") == "concluida")
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_account_id": CONTA, "chatwoot_conversation_id": CONV_3,
        "chatwoot_contact_id": CONTATO, "etiquetas": ["urgente"],
        "campos": {"RECL_NOME": "Carlos Retomada", "FUNCAO": "Vigia"}})
    novo = d.get("entrevista_id")
    conferir("sem a etiqueta, abre caso NOVO (não mexe no concluído)",
             s == 201 and novo != eid, (s, novo, eid))
    conferir("motivo explica a decisão",
             d.get("vinculo", {}).get("resolvida_por") == "caso_novo_apos_fechado",
             d.get("vinculo"))

    with dados.conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT status::text FROM peticoes.entrevistas WHERE id = %s", (eid,))
        conferir("o caso concluído continua concluído",
                 cur.fetchone()["status"] == "concluida")
        # o caso novo também é concluído, para testar a retomada por etiqueta
        cur.execute("""UPDATE peticoes.entrevistas SET status = 'concluida',
                       concluida_em = now() WHERE id = %s""", (novo,))
        conn.commit()

    print("\ncaso concluído: COM a etiqueta, retoma de onde parou")
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_account_id": CONTA, "chatwoot_conversation_id": 88_1004,
        "chatwoot_contact_id": CONTATO,
        "etiquetas": ["retomar-entrevista"],
        "campos": {"FUNCAO": "Vigilante corrigido", "RECL_CPF": "999.888.777-66"}})
    conferir("retoma o caso concluído em vez de criar outro",
             d.get("entrevista_id") == novo, (d.get("entrevista_id"), novo))
    conferir("motivo aponta a etiqueta",
             d.get("vinculo", {}).get("resolvida_por") == "contato_caso_retomado_por_etiqueta",
             d.get("vinculo"))
    conferir("voltou para em_andamento", d.get("status") == "em_andamento", d.get("status"))
    conferir("etiqueta que autorizou fica registrada",
             d.get("vinculo", {}).get("retomada_por_etiqueta") == "retomar-entrevista")
    conferir("campo errado foi sobrescrito",
             d.get("aceitos", {}).get("funcao") == "Vigilante corrigido",
             d.get("aceitos"))

    with dados.conectar() as conn, conn.cursor() as cur:
        cur.execute("""SELECT reaberturas, reaberta_etiqueta FROM peticoes.entrevistas
                       WHERE id = %s""", (novo,))
        r = cur.fetchone()
        conferir("reabertura contabilizada", r["reaberturas"] == 1, r)
        cur.execute("""SELECT qtd_conversas FROM peticoes.vw_entrevista_conversas
                       WHERE entrevista_id = %s""", (eid,))
        conferir("view mostra as conversas do caso", cur.fetchone()["qtd_conversas"] == 2)

    print("\netiquetas indeterminadas (agente não informou, sem token do Chatwoot)")
    with dados.conectar() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE peticoes.entrevistas SET status = 'concluida'
                       WHERE id = %s""", (novo,))
        conn.commit()
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_account_id": CONTA, "chatwoot_conversation_id": 88_1005,
        "chatwoot_contact_id": CONTATO,
        "campos": {"RECL_NOME": "Carlos Retomada"}})
    conferir("sem saber as etiquetas, NÃO sobrescreve o concluído",
             d.get("entrevista_id") not in (novo, eid), d.get("entrevista_id"))
    conferir("resposta explica que não deu para ler as etiquetas",
             "etiqueta" in (d.get("vinculo", {}).get("observacao") or ""),
             d.get("vinculo", {}).get("observacao"))

    limpar()
    print("  (dados de teste removidos)")
    print(f"\n{teste_api.ok} passaram, {len(falhas)} falharam")
    if falhas:
        for f in falhas:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
