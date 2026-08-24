#!/usr/bin/env python3
"""Testa a API de ingestão de ponta a ponta, contra um servidor já rodando.

    python3 api/servidor.py &          # em outro terminal
    python3 api/teste_api.py

Cria uma entrevista de teste, exercita todos os endpoints e apaga o que criou.
"""
import json
import pathlib
import sys
import urllib.error
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / ".pylibs"))
sys.path.insert(0, str(RAIZ / "api"))

CONVERSA_TESTE = 999_4242

falhas = []
ok = 0


def env(chave, padrao=None):
    arq = RAIZ / ".env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                if k.strip() == chave:
                    return v.strip().strip('"').strip("'") or padrao
    return padrao


BASE = f"http://127.0.0.1:{env('INGESTAO_PORT', '8088')}"
CHAVE = env("INGESTAO_API_KEY")


def chamar(metodo, rota, corpo=None, chave=CHAVE):
    req = urllib.request.Request(
        BASE + rota,
        data=json.dumps(corpo, ensure_ascii=False).encode() if corpo is not None else None,
        method=metodo,
        headers={"Content-Type": "application/json", **({"X-API-Key": chave} if chave else {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def conferir(descricao, condicao, detalhe=""):
    global ok
    if condicao:
        ok += 1
        print(f"  ok   {descricao}")
    else:
        falhas.append(f"{descricao} {detalhe}")
        print(f"  FALHA {descricao} {detalhe}")


def main():
    print(f"testando {BASE}\n")

    print("saúde e autenticação")
    s, d = chamar("GET", "/saude", chave=None)
    conferir("GET /saude responde 200 sem chave", s == 200 and d.get("ok"), d)
    s, _ = chamar("GET", "/roteiro", chave="chave-errada")
    conferir("chave inválida devolve 401", s == 401)
    s, d = chamar("GET", "/roteiro")
    conferir("GET /roteiro traz o roteiro", s == 200 and d.get("total", 0) >= 55, d.get("total"))
    s, d = chamar("GET", "/naoexiste")
    conferir("rota inexistente devolve 404", s == 404)

    print("\nconta é obrigatória junto com a conversa (display_id é por conta)")
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_conversation_id": 1234, "campos": {"RECL_NOME": "Sem Conta"}})
    conferir("conversa sem account_id é recusada (422)", s == 422, s)

    print("\ncriação")
    s, d = chamar("POST", "/entrevistas", {"campos": {"FUNCAO": "Vigilante"}})
    conferir("criar sem RECL_NOME é recusado (422)", s == 422, d.get("erro", "")[:40])

    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_account_id": 1, "chatwoot_conversation_id": CONVERSA_TESTE,
        "contato": {"chatwoot_contact_id": 99777, "nome": "Teste Automatizado"},
        "campos": {"RECL_NOME": "Teste Automatizado"},
    })
    conferir("criar com RECL_NOME devolve 201", s == 201 and d.get("criada") is True, s)
    eid = d.get("entrevista_id")
    conferir("resposta traz a próxima pergunta",
             bool(d.get("proxima_pergunta", {}).get("pergunta")))

    print("\nidempotência da conversa")
    s, d = chamar("POST", "/entrevistas", {
        "chatwoot_account_id": 1, "chatwoot_conversation_id": CONVERSA_TESTE,
        "campos": {"RECL_RG": "11.111.111-1"},
    })
    conferir("segunda chamada reusa a entrevista (não cria outra)",
             s == 200 and d.get("criada") is False and d.get("entrevista_id") == eid, s)

    print("\nconversão de linguagem natural")
    s, d = chamar("POST", f"/entrevistas/{eid}", {"campos": {
        "RECL_NASC": "12/04/1988",
        "DATA_ADMISSAO": "1 de março de 2019",
        "SALARIO": "R$ 2.148,22",
        "tipo_dispensa": "indireta",
        "tem_adic_noturno": "sim, de madrugada",
        "vale_transporte": "nunca recebi",
        "periodo_antecedente": "uns 15 minutos antes",
    }})
    a = d.get("aceitos", {})
    conferir("data DD/MM/AAAA", str(a.get("recl_nasc")) == "1988-04-12", a.get("recl_nasc"))
    conferir("data por extenso", str(a.get("data_admissao")) == "2019-03-01", a.get("data_admissao"))
    conferir("moeda BR", str(a.get("salario")) == "2148.22", a.get("salario"))
    conferir("enum parcial", a.get("tipo_dispensa") == "rescisao_indireta", a.get("tipo_dispensa"))
    conferir("booleano afirmativo", a.get("tem_adic_noturno") is True, a.get("tem_adic_noturno"))
    conferir("booleano negativo", a.get("vale_transporte") is False, a.get("vale_transporte"))
    conferir("número em frase", a.get("periodo_antecedente") == 15, a.get("periodo_antecedente"))

    print("\nvalores ambíguos são recusados, não chutados")
    s, d = chamar("POST", f"/entrevistas/{eid}", {"campos": {
        "DATA_RESCISAO": "faz uns dois anos",
        "SALARIO": "um salário mínimo",
        "intervalo_suprimido": "às vezes",
    }})
    recusados = {e["campo"] for e in d.get("erros", [])}
    conferir("data vaga recusada", "DATA_RESCISAO" in recusados, recusados)
    conferir("salário vago recusado", "SALARIO" in recusados, recusados)
    conferir("booleano vago recusado", "intervalo_suprimido" in recusados, recusados)
    conferir("salário anterior preservado", d.get("aceitos", {}).get("salario") is None)

    print("\ncampo fora do roteiro")
    s, d = chamar("POST", f"/entrevistas/{eid}", {"campos": {"CAMPO_INVENTADO": "xyz"}})
    conferir("campo desconhecido vai para ignorados",
             "CAMPO_INVENTADO" in d.get("ignorados", []), d.get("ignorados"))
    s, d = chamar("GET", f"/entrevistas/{eid}/payload")
    conferir("campo desconhecido fica FORA do payload da API de petições",
             "CAMPO_INVENTADO" not in d.get("payload", {}).get("entrevista", {}))

    print("\nreclamadas")
    s, d = chamar("POST", f"/entrevistas/{eid}", {"campos": {
        "RECL1_NOME": "Empregadora Teste Ltda", "RECL1_CNPJ": "22.333.444/0001-55",
        "RECL2_NOME": "Tomadora Teste S/A", "RECL2_ENDCOMPL": "São Paulo/SP",
    }})
    ordens = sorted(r["ordem"] for r in d.get("reclamadas", []))
    conferir("RECLn_* viram linhas na tabela filha", ordens == [1, 2], ordens)
    s, d = chamar("GET", f"/entrevistas/{eid}/payload")
    e = d.get("payload", {}).get("entrevista", {})
    conferir("payload reprojeta ordem -> RECL1_*/RECL2_*",
             e.get("RECL1_NOME") == "Empregadora Teste Ltda"
             and e.get("RECL2_ENDCOMPL") == "São Paulo/SP")

    print("\nbusca e ciclo de vida")
    s, d = chamar("GET", f"/entrevistas?conversa={CONVERSA_TESTE}&conta=1")
    conferir("achar pela conversa do Chatwoot", s == 200 and d.get("entrevista_id") == eid, s)
    s, d = chamar("GET", f"/entrevistas/{eid}/proxima-pergunta")
    conferir("proxima-pergunta responde", s == 200 and d.get("proxima_pergunta"))
    s, d = chamar("POST", f"/entrevistas/{eid}/pergunta-pendente", {"campo": "RECL_CPF"})
    conferir("marcar pendente muda para aguardando_cliente",
             d.get("status") == "aguardando_cliente", d.get("status"))
    s, d = chamar("POST", f"/entrevistas/{eid}", {"campos": {"RECL_CPF": "111.222.333-44"}})
    conferir("responder volta para em_andamento", d.get("status") == "em_andamento", d.get("status"))
    s, d = chamar("POST", f"/entrevistas/{eid}/documentos", {
        "tipo": "holerite", "nome_arquivo": "h.pdf", "url": "https://exemplo/h.pdf",
        "mime": "application/pdf", "tamanho_bytes": 1024, "chatwoot_message_id": 555001})
    conferir("registrar anexo devolve 201", s == 201 and d.get("documento_id"), (s, d))
    s, d = chamar("POST", f"/entrevistas/{eid}/concluir")
    conferir("concluir marca concluida", d.get("status") == "concluida", d.get("status"))
    conferir("concluir avisa dos campos em branco", bool(d.get("aviso")))
    conferir("pronta_para_peticao", d.get("pronta_para_peticao") is True)

    print("\nentrevista inexistente")
    s, d = chamar("GET", "/entrevistas/99999999")
    conferir("id inexistente devolve 404", s == 404)
    s, d = chamar("GET", "/entrevistas/abc")
    conferir("id não numérico devolve 422", s == 422)

    print("\na entrevista entra na fila de geração")
    import dados  # noqa: E402
    with dados.conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM peticoes.vw_fila_geracao WHERE id = %s", (eid,))
        conferir("aparece em vw_fila_geracao", cur.fetchone() is not None)
        cur.execute("SELECT count(*) AS n FROM peticoes.ingestao_log WHERE entrevista_id = %s", (eid,))
        conferir("requisições ficaram no ingestao_log", cur.fetchone()["n"] > 0)

        # limpeza
        cur.execute("DELETE FROM peticoes.entrevistas WHERE id = %s", (eid,))
        cur.execute("DELETE FROM peticoes.contatos WHERE chatwoot_contact_id = 99777")
        conn.commit()
    print("  (dados de teste removidos)")

    print(f"\n{ok} passaram, {len(falhas)} falharam")
    if falhas:
        for f in falhas:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
