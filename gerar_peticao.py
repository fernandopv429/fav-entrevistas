#!/usr/bin/env python3
"""Consome o banco e chama a API de Petições.

    python3 gerar_peticao.py --lista               # entrevistas na fila
    python3 gerar_peticao.py --id 1 --dry-run      # mostra o payload, não chama a API
    python3 gerar_peticao.py --id 1 --so-calculo   # redigir_ia=false, gerar_pdf=false
    python3 gerar_peticao.py --id 1                # gera a peça e salva o PDF

O payload é montado pelo próprio Postgres (peticoes.montar_payload), então a
requisição reflete exatamente o que está gravado no banco.
"""
import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ / ".pylibs"))  # driver extraído localmente

import psycopg  # noqa: E402

ENDPOINT = "/peca/da-entrevista"


def ambiente() -> dict:
    env = {}
    arq = RAIZ / ".env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, v = linha.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k in (
        "DATABASE_URL", "PETICOES_BASE_URL", "PETICOES_API_KEY")})
    return env


def listar(cur) -> None:
    cur.execute("""
        SELECT id, codigo, recl_nome, tentativas, concluida_em
        FROM peticoes.vw_fila_geracao
    """)
    linhas = cur.fetchall()
    if not linhas:
        print("fila vazia (nenhuma entrevista com status 'concluida' pendente)")
        return
    print(f"{'id':>5}  {'codigo':<14} {'reclamante':<34} tent.")
    for id_, codigo, nome, tent, _ in linhas:
        print(f"{id_:>5}  {codigo or '-':<14} {(nome or '')[:34]:<34} {tent}")


def chamar_api(base_url: str, api_key: str, corpo: dict, pdf: bool):
    dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + ENDPOINT,
        data=dados,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": api_key,
            "Accept": "application/pdf" if pdf else "application/json",
        },
    )
    inicio = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            return resp.status, dict(resp.headers), resp.read(), int((time.monotonic() - inicio) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read(), int((time.monotonic() - inicio) * 1000)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--id", type=int, help="id da entrevista")
    p.add_argument("--lista", action="store_true", help="mostra a fila de geração")
    p.add_argument("--dry-run", action="store_true", help="só imprime o payload")
    p.add_argument("--so-calculo", action="store_true",
                   help="redigir_ia=false e gerar_pdf=false (sem custo de redação)")
    p.add_argument("--json", action="store_true",
                   help="pede JSON em vez do PDF direto")
    args = p.parse_args()

    env = ambiente()
    with psycopg.connect(env["DATABASE_URL"]) as conn, conn.cursor() as cur:
        if args.lista or not args.id:
            listar(cur)
            return

        cur.execute("SELECT peticoes.montar_payload(%s)", (args.id,))
        corpo = cur.fetchone()[0]

        if args.so_calculo:
            corpo["redigir_ia"] = False
            corpo["gerar_pdf"] = False

        if args.dry_run:
            print(json.dumps(corpo, ensure_ascii=False, indent=2))
            return

        api_key = env.get("PETICOES_API_KEY", "")
        if not api_key:
            sys.exit("PETICOES_API_KEY não preenchida no .env")

        quer_pdf = bool(corpo.get("gerar_pdf")) and not args.json
        base = env.get("PETICOES_BASE_URL", "https://peticoes.nexusdevhub.com")

        cur.execute("""
            INSERT INTO peticoes.peticoes (entrevista_id, tentativa, status, payload_enviado)
            VALUES (%s,
                    COALESCE((SELECT max(tentativa)+1 FROM peticoes.peticoes
                              WHERE entrevista_id = %s), 1),
                    'enviando', %s)
            RETURNING id, tentativa
        """, (args.id, args.id, json.dumps(corpo)))
        peticao_id, tentativa = cur.fetchone()
        conn.commit()

        print(f"peça #{peticao_id} (tentativa {tentativa}) — chamando a API, "
              f"pode levar algumas dezenas de segundos...")
        status, headers, bruto, ms = chamar_api(base, api_key, corpo, quer_pdf)
        tipo = headers.get("Content-Type", "")

        resposta_json, pdf_arquivo = None, None
        if "application/pdf" in tipo:
            pdf_arquivo = str(RAIZ / "pdfs" / f"{corpo.get('codigo', peticao_id)}.pdf")
            pathlib.Path(pdf_arquivo).parent.mkdir(exist_ok=True)
            pathlib.Path(pdf_arquivo).write_bytes(bruto)
            resposta_json = {
                "codigo": corpo.get("codigo"),
                "status": headers.get("X-Status"),
                "valor_causa": headers.get("X-Valor-Causa"),
                "rito": headers.get("X-Rito"),
            }
        else:
            try:
                resposta_json = json.loads(bruto.decode("utf-8"))
            except Exception:
                resposta_json = {"status": "erro", "_bruto": bruto[:2000].decode("utf-8", "replace")}

        cur.execute("""
            UPDATE peticoes.peticoes
               SET http_status = %s, duracao_ms = %s,
                   pdf_arquivo = %s, pdf_gerado = %s,
                   status = CASE WHEN %s >= 400 THEN 'falha_http'::peticoes.status_peticao
                                 ELSE status END,
                   erro = CASE WHEN %s >= 400 THEN %s ELSE erro END
             WHERE id = %s
        """, (status, ms, pdf_arquivo, pdf_arquivo is not None,
              status, status, json.dumps(resposta_json)[:4000], peticao_id))

        cur.execute("SELECT peticoes.registrar_resposta(%s, %s)",
                    (peticao_id, json.dumps(resposta_json)))
        cur.execute("""
            INSERT INTO peticoes.api_chamadas
                (entrevista_id, peticao_id, url, accept, http_status, duracao_ms,
                 requisicao, resposta)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (args.id, peticao_id, base + ENDPOINT,
              "application/pdf" if quer_pdf else "application/json",
              status, ms, json.dumps(corpo), json.dumps(resposta_json)))
        conn.commit()

        print(f"HTTP {status} em {ms} ms")
        print(f"status: {resposta_json.get('status')}  "
              f"valor da causa: {resposta_json.get('valor_causa')}  "
              f"rito: {resposta_json.get('rito')}")
        if pdf_arquivo:
            print(f"PDF: {pdf_arquivo}")
        for c in resposta_json.get("campos_ausentes") or []:
            print(f"  faltou {c.get('campo')}: {c.get('efeito')}")


if __name__ == "__main__":
    main()
