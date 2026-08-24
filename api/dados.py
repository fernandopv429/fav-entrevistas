"""Acesso ao banco: upsert de entrevista e leitura do estado.

O mapa campo-da-API -> coluna vem da própria tabela peticoes.perguntas, então
acrescentar um campo novo é um INSERT no roteiro, não uma alteração de código.
"""
import json
import os
import pathlib
import re
import sys
import threading
import time

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / ".pylibs"))

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402

import chatwoot  # noqa: E402
from coercao import ErroCoercao, converter, normalizar  # noqa: E402

RECLAMADA_RX = re.compile(r"^RECL([123])_(NOME|CNPJ|LOGRADOURO|ENDCOMPL)$", re.I)

_roteiro_cache = {"quando": 0.0, "dados": None}
_lock = threading.Lock()


def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    arq = RAIZ / ".env"
    if arq.exists():
        for linha in arq.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha.startswith("DATABASE_URL="):
                return linha.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("DATABASE_URL não configurada")


def conectar():
    return psycopg.connect(dsn(), row_factory=dict_row)


def roteiro(cur, forcar=False):
    """Catálogo de perguntas, em cache de 60s (muda raramente)."""
    with _lock:
        if not forcar and _roteiro_cache["dados"] and time.monotonic() - _roteiro_cache["quando"] < 60:
            return _roteiro_cache["dados"]
    cur.execute("""
        SELECT campo, coluna, secao, ordem, texto, tipo::text AS tipo, opcoes,
               obrigatorio, efeito_ausencia
        FROM peticoes.perguntas WHERE ativo ORDER BY ordem
    """)
    linhas = cur.fetchall()
    dados = {
        "lista": linhas,
        "por_campo": {r["campo"]: r for r in linhas},
        "por_campo_norm": {normalizar(r["campo"]): r for r in linhas},
    }
    with _lock:
        _roteiro_cache.update(quando=time.monotonic(), dados=dados)
    return dados


# ----------------------------------------------------------------- entrevistas

def ids_chatwoot(corpo):
    """Extrai e valida os identificadores do Chatwoot vindos no corpo."""
    conta = corpo.get("chatwoot_account_id") or corpo.get("conta_id")
    conversa = corpo.get("chatwoot_conversation_id") or corpo.get("conversa_id")
    return {
        "conta": int(conta) if conta not in (None, "") else None,
        # display_id: o que o Chatwoot chama de "id" da conversa
        "conversa": int(conversa) if conversa not in (None, "") else None,
        "uuid": (corpo.get("chatwoot_conversation_uuid")
                 or corpo.get("conversa_uuid") or None),
        "inbox": corpo.get("chatwoot_inbox_id") or corpo.get("inbox_id") or None,
        "contato": corpo.get("chatwoot_contact_id")
                   or (corpo.get("contato") or {}).get("chatwoot_contact_id"),
        "telefone": (corpo.get("contato") or {}).get("telefone"),
    }


def achar_entrevista(cur, corpo):
    """Resolve a entrevista em cascata, da âncora mais forte para a mais fraca.

    1. entrevista_id explícito
    2. codigo explícito
    3. uuid da conversa (único global)
    4. (conta, conversa) — o display_id só identifica junto com a conta
    5. caso aberto do contato — é o que faz a entrevista sobreviver à troca de
       conversa quando o cliente volta a falar depois de a conversa ser resolvida

    Devolve (entrevista, motivo). Não cria nada.
    """
    if corpo.get("entrevista_id"):
        cur.execute("SELECT * FROM peticoes.entrevistas WHERE id = %s",
                    (int(corpo["entrevista_id"]),))
        if (r := cur.fetchone()):
            return r, "entrevista_id"

    if corpo.get("codigo"):
        cur.execute("SELECT * FROM peticoes.entrevistas WHERE codigo = %s",
                    (str(corpo["codigo"]),))
        if (r := cur.fetchone()):
            return r, "codigo"

    ids = ids_chatwoot(corpo)

    if ids["uuid"]:
        cur.execute("""
            SELECT e.* FROM peticoes.entrevistas e
            JOIN peticoes.entrevista_conversas c ON c.entrevista_id = e.id
            WHERE c.chatwoot_conversation_uuid = %s::uuid
        """, (str(ids["uuid"]),))
        if (r := cur.fetchone()):
            return r, "uuid_da_conversa"

    if ids["conversa"] and ids["conta"]:
        cur.execute("""
            SELECT e.* FROM peticoes.entrevistas e
            JOIN peticoes.entrevista_conversas c ON c.entrevista_id = e.id
            WHERE c.chatwoot_account_id = %s AND c.chatwoot_conversation_id = %s
        """, (ids["conta"], ids["conversa"]))
        if (r := cur.fetchone()):
            return r, "conversa"

    # conversa nova, mesmo contato: retoma o caso em aberto
    if ids["conta"] and (ids["contato"] or ids["telefone"]):
        cur.execute("""
            SELECT e.* FROM peticoes.entrevistas e
            JOIN peticoes.contatos ct ON ct.id = e.contato_id
            WHERE ct.chatwoot_account_id = %s
              AND (ct.chatwoot_contact_id = %s::bigint
                   OR (%s::text IS NOT NULL AND ct.telefone = %s::text))
              AND e.status NOT IN ('concluida', 'cancelada')
            ORDER BY e.atualizado_em DESC LIMIT 1
        """, (ids["conta"], ids["contato"], ids["telefone"], ids["telefone"]))
        if (r := cur.fetchone()):
            return r, "contato_caso_aberto"

    # último caso do contato, ainda que fechado — quem chama decide se retoma
    # (só com a etiqueta) ou se abre um caso novo
    if ids["conta"] and (ids["contato"] or ids["telefone"]):
        cur.execute("""
            SELECT e.* FROM peticoes.entrevistas e
            JOIN peticoes.contatos ct ON ct.id = e.contato_id
            WHERE ct.chatwoot_account_id = %s
              AND (ct.chatwoot_contact_id = %s::bigint
                   OR (%s::text IS NOT NULL AND ct.telefone = %s::text))
            ORDER BY e.atualizado_em DESC LIMIT 1
        """, (ids["conta"], ids["contato"], ids["telefone"], ids["telefone"]))
        if (r := cur.fetchone()):
            return r, "contato_caso_fechado"

    return None, "nenhuma"


def vincular_conversa(cur, eid, ids, etiquetas=None):
    """Registra que esta conversa alimentou esta entrevista.

    UNIQUE (conta, conversa) garante que uma conversa serve a um caso só.
    """
    if not (ids["conta"] and ids["conversa"]):
        return
    cur.execute("""
        INSERT INTO peticoes.entrevista_conversas
            (entrevista_id, chatwoot_account_id, chatwoot_conversation_id,
             chatwoot_conversation_uuid, chatwoot_inbox_id, etiquetas)
        VALUES (%s,%s,%s,%s::uuid,%s,%s)
        ON CONFLICT (chatwoot_account_id, chatwoot_conversation_id) DO UPDATE SET
            ultima_em = now(),
            chatwoot_conversation_uuid =
                COALESCE(EXCLUDED.chatwoot_conversation_uuid,
                         peticoes.entrevista_conversas.chatwoot_conversation_uuid),
            etiquetas = COALESCE(EXCLUDED.etiquetas,
                                 peticoes.entrevista_conversas.etiquetas)
    """, (eid, ids["conta"], ids["conversa"],
          str(ids["uuid"]) if ids["uuid"] else None, ids["inbox"], etiquetas))


def garantir_contato(cur, corpo):
    contato = corpo.get("contato") or {}
    conta = corpo.get("chatwoot_account_id") or corpo.get("conta_id")
    cid = contato.get("chatwoot_contact_id") or corpo.get("chatwoot_contact_id")
    if not (conta and cid):
        return None
    cur.execute("""
        INSERT INTO peticoes.contatos
            (chatwoot_account_id, chatwoot_contact_id, nome, telefone, email, identifier)
        VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT (chatwoot_account_id, chatwoot_contact_id) DO UPDATE SET
            nome       = COALESCE(EXCLUDED.nome, peticoes.contatos.nome),
            telefone   = COALESCE(EXCLUDED.telefone, peticoes.contatos.telefone),
            email      = COALESCE(EXCLUDED.email, peticoes.contatos.email),
            identifier = COALESCE(EXCLUDED.identifier, peticoes.contatos.identifier)
        RETURNING id
    """, (conta, cid, contato.get("nome"), contato.get("telefone"),
          contato.get("email"), contato.get("identifier")))
    return cur.fetchone()["id"]


def criar_entrevista(cur, corpo, nome_reclamante, contato_id):
    cur.execute("""
        INSERT INTO peticoes.entrevistas
            (codigo, status, contato_id, chatwoot_account_id,
             chatwoot_conversation_id, chatwoot_inbox_id, recl_nome)
        VALUES (%s, 'em_andamento', %s, %s, %s, %s, %s)
        RETURNING *
    """, (corpo.get("codigo"), contato_id,
          corpo.get("chatwoot_account_id") or corpo.get("conta_id"),
          corpo.get("chatwoot_conversation_id") or corpo.get("conversa_id"),
          corpo.get("chatwoot_inbox_id") or corpo.get("inbox_id"),
          nome_reclamante))
    return cur.fetchone()


def separar_campos(campos, cat):
    """Classifica o que veio: colunas da entrevista, extras, reclamadas ou desconhecido.

    Uma pergunta cujo `coluna` é 'extras.<chave>' não vira coluna: vai para o
    jsonb `extras`, que montar_entrevista funde no payload. É como entram os
    campos que o motor hoje ignora (armamento_colete, epi, produtos...) sem
    poluir a tabela com coluna por campo que não calcula nada.
    """
    colunas, extras, reclamadas, erros, ignorados = {}, {}, {}, [], []

    for chave, valor in campos.items():
        if chave in ("entrevista_id", "codigo", "contato",
                     "chatwoot_account_id", "chatwoot_conversation_id",
                     "chatwoot_inbox_id", "chatwoot_contact_id", "conta_id",
                     "conversa_id", "inbox_id", "campos", "entrevista",
                     "status", "municipio", "mensagem_id", "reclamadas",
                     "etiquetas", "labels", "chatwoot_conversation_uuid",
                     "conversa_uuid"):
            continue

        m = RECLAMADA_RX.match(chave)
        if m:
            ordem, atributo = int(m.group(1)), m.group(2).lower()
            try:
                texto = converter(valor, "texto")
            except ErroCoercao as e:
                erros.append({"campo": chave, "valor": valor, "motivo": str(e)})
                continue
            if texto is not None:
                reclamadas.setdefault(ordem, {})[atributo] = texto
            continue

        p = cat["por_campo"].get(chave) or cat["por_campo_norm"].get(normalizar(chave))
        if not p or not p["coluna"]:
            ignorados.append(chave)
            continue

        try:
            convertido = converter(valor, p["tipo"], p["opcoes"])
        except ErroCoercao as e:
            erros.append({"campo": chave, "valor": valor, "motivo": str(e)})
            continue

        if p["coluna"].startswith("extras."):
            if convertido is not None:
                extras[p["coluna"].split(".", 1)[1]] = convertido
        else:
            colunas[p["coluna"]] = convertido

    return colunas, extras, reclamadas, erros, ignorados


def etiqueta_retomar():
    return (chatwoot.env("ETIQUETA_RETOMAR", "retomar-entrevista") or "").strip()


def resolver_etiquetas(corpo, ids):
    """Descobre as etiquetas da conversa e se alguma autoriza retomar o caso.

    Ordem: o que o agente mandou no corpo; se não mandou, consulta a API do
    Chatwoot (o webhook não traz etiquetas). Não conseguindo determinar, devolve
    None — e aí o caminho conservador é abrir caso novo, não sobrescrever.
    """
    alvo = normalizar(etiqueta_retomar())
    brutas = corpo.get("etiquetas") or corpo.get("labels")

    if brutas is None:
        brutas = chatwoot.etiquetas_da_conversa(ids["conta"], ids["conversa"])
        origem = "api_chatwoot" if brutas is not None else "indeterminada"
    else:
        origem = "informada_pelo_agente"

    if brutas is None:
        return None, None, ("não foi possível ler as etiquetas da conversa "
                            "(agente não informou e a API do Chatwoot não está "
                            "configurada em CHATWOOT_BASE_URL/CHATWOOT_API_TOKEN)")

    etiquetas = [str(x).strip() for x in brutas if str(x).strip()]
    retomada = next((e for e in etiquetas if normalizar(e) == alvo), None)
    return etiquetas, retomada, f"etiquetas de origem {origem}"


def _normalizado(pergunta, colunas, extras, valor):
    """O valor já convertido, venha ele de uma coluna, de `extras` ou cru."""
    coluna = pergunta["coluna"] if pergunta else None
    if not coluna:
        return valor
    if coluna.startswith("extras."):
        return extras.get(coluna.split(".", 1)[1], valor)
    return colunas.get(coluna)


def gravar(cur, corpo):
    """Upsert principal: grava o que vier e devolve o estado da entrevista."""
    cat = roteiro(cur)

    # aceita os campos na raiz, em "campos" ou em "entrevista" — o agente
    # pode mandar de qualquer uma das três formas
    campos = {}
    for origem in (corpo.get("entrevista"), corpo.get("campos"), corpo):
        if isinstance(origem, dict):
            campos.update(origem)

    colunas, extras, reclamadas, erros, ignorados = separar_campos(campos, cat)

    for r in corpo.get("reclamadas") or []:
        if isinstance(r, dict) and r.get("ordem"):
            alvo = reclamadas.setdefault(int(r["ordem"]), {})
            for k in ("nome", "cnpj", "logradouro", "endcompl"):
                if r.get(k):
                    alvo[k] = str(r[k]).strip()

    ids = ids_chatwoot(corpo)

    # multi-conta: o display_id da conversa é sequencial POR CONTA
    # (conv_dpid_seq_{account_id}), então conversa sem conta pode apontar para o
    # caso de outro cliente. Melhor recusar do que gravar no caso errado.
    if ids["conversa"] and not ids["conta"]:
        raise ValueError(
            "informe chatwoot_account_id junto com chatwoot_conversation_id: "
            "o id da conversa é sequencial por conta e não identifica sozinho")

    entrevista, motivo = achar_entrevista(cur, corpo)
    etiquetas, retomada, nota = resolver_etiquetas(corpo, ids)

    # caso fechado só é retomado com a etiqueta; sem ela, abre caso novo
    if entrevista is not None and motivo == "contato_caso_fechado":
        if retomada:
            cur.execute("""
                UPDATE peticoes.entrevistas
                   SET status = 'em_andamento', reaberta_em = now(),
                       reaberta_etiqueta = %s, reaberturas = reaberturas + 1
                 WHERE id = %s RETURNING *
            """, (retomada, entrevista["id"]))
            entrevista = cur.fetchone()
            motivo = "contato_caso_retomado_por_etiqueta"
        else:
            entrevista = None
            motivo = "caso_novo_apos_fechado"

    if not entrevista:
        nome = colunas.pop("recl_nome", None)
        if not nome:
            raise ValueError(
                "para criar uma entrevista é preciso o nome do reclamante "
                "(RECL_NOME); depois disso os outros campos podem vir aos poucos")
        contato_id = garantir_contato(cur, corpo)
        entrevista = criar_entrevista(cur, corpo, nome, contato_id)
        criada = True
    else:
        criada = False
        if (cid := garantir_contato(cur, corpo)) and not entrevista["contato_id"]:
            colunas["contato_id"] = cid

    eid = entrevista["id"]
    vincular_conversa(cur, eid, ids, etiquetas)

    if corpo.get("municipio"):
        colunas["municipio"] = str(corpo["municipio"]).strip()
    if corpo.get("status") in ("rascunho", "em_andamento", "aguardando_cliente",
                               "aguardando_revisao", "concluida", "cancelada"):
        colunas["status"] = corpo["status"]

    aceitos = {}
    if colunas:
        sets = ", ".join(f"{c} = %s" for c in colunas)
        valores = list(colunas.values())
        if "status" in colunas:
            sql = f"UPDATE peticoes.entrevistas SET {sets} WHERE id = %s RETURNING *"
        else:
            # gravar dado significa que o cliente respondeu: sai de 'rascunho' e
            # de 'aguardando_cliente'. 'concluida'/'cancelada' não são mexidos.
            sql = (f"UPDATE peticoes.entrevistas SET {sets}, "
                   "status = CASE WHEN status IN ('rascunho','aguardando_cliente') "
                   "THEN 'em_andamento'::peticoes.status_entrevista ELSE status END, "
                   "pergunta_pendente = NULL "
                   "WHERE id = %s RETURNING *")
        cur.execute(sql, valores + [eid])
        entrevista = cur.fetchone()
        aceitos = {c: colunas[c] for c in colunas if c not in ("contato_id", "status")}

    if extras:
        # || funde: chave nova entra, chave repetida é sobrescrita, o resto fica.
        # A mesma transição de status das colunas, senão uma entrevista que só
        # recebeu campos de `extras` ficaria parada em 'rascunho'.
        cur.execute("""
            UPDATE peticoes.entrevistas
               SET extras = extras || %s::jsonb,
                   status = CASE WHEN status IN ('rascunho','aguardando_cliente')
                                 THEN 'em_andamento'::peticoes.status_entrevista
                                 ELSE status END,
                   pergunta_pendente = NULL
             WHERE id = %s RETURNING *
        """, (json.dumps(extras, default=str), eid))
        entrevista = cur.fetchone()
        aceitos.update({f"extras.{k}": v for k, v in extras.items()})

    for ordem, atributos in sorted(reclamadas.items()):
        cols = ["entrevista_id", "ordem"] + list(atributos)
        cur.execute(f"""
            INSERT INTO peticoes.entrevista_reclamadas ({", ".join(cols)})
            VALUES ({", ".join(["%s"] * len(cols))})
            ON CONFLICT (entrevista_id, ordem) DO UPDATE SET
                {", ".join(f"{k} = COALESCE(EXCLUDED.{k}, peticoes.entrevista_reclamadas.{k})"
                           for k in atributos)}
        """, [eid, ordem] + list(atributos.values()))

    if ignorados:
        cur.execute("""
            UPDATE peticoes.entrevistas
               SET nao_mapeados = nao_mapeados || %s::jsonb
             WHERE id = %s
        """, (json.dumps({k: campos[k] for k in ignorados}, default=str), eid))

    # trilha do que o cliente respondeu, cru, para auditoria
    for chave, valor in campos.items():
        p = cat["por_campo"].get(chave) or cat["por_campo_norm"].get(normalizar(chave))
        if p or RECLAMADA_RX.match(chave):
            cur.execute("""
                INSERT INTO peticoes.entrevista_respostas
                    (entrevista_id, campo, valor_bruto, valor_normalizado, chatwoot_message_id)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (chatwoot_message_id, campo) DO NOTHING
            """, (eid, chave, str(valor)[:4000],
                  str(_normalizado(p, colunas, extras, valor))[:4000],
                  corpo.get("mensagem_id")))

    estado = ler_estado(cur, eid)
    # tipos nativos no JSON (true, 15, "2019-03-01") — o agente lê de volta o que gravou
    estado.update(criada=criada, aceitos=aceitos, ignorados=ignorados, erros=erros,
                  vinculo={"resolvida_por": motivo, "etiquetas": etiquetas,
                           "retomada_por_etiqueta": retomada, "observacao": nota})
    return estado


def ler_estado(cur, eid):
    """Estado da entrevista + o que falta + a próxima pergunta a fazer."""
    cur.execute("""
        SELECT e.id, e.codigo, e.status::text AS status, e.recl_nome,
               e.chatwoot_conversation_id, e.pergunta_pendente,
               e.criado_em, e.atualizado_em,
               peticoes.montar_entrevista(e.id) AS entrevista
        FROM peticoes.entrevistas e WHERE e.id = %s
    """, (eid,))
    e = cur.fetchone()
    if not e:
        return None

    preenchido = e.pop("entrevista") or {}
    cat = roteiro(cur)

    faltando = []
    for p in cat["lista"]:
        if p["campo"] in preenchido:
            continue
        m = RECLAMADA_RX.match(p["campo"])
        if m and int(m.group(1)) > 1:
            continue          # 2ª e 3ª rés são opcionais: não entram em "faltando"
        faltando.append(p["campo"])

    proxima = None
    for p in cat["lista"]:
        if p["campo"] in faltando:
            proxima = {"campo": p["campo"], "pergunta": p["texto"],
                       "tipo": p["tipo"], "opcoes": p["opcoes"],
                       "secao": p["secao"], "efeito_se_faltar": p["efeito_ausencia"]}
            break

    cur.execute("""
        SELECT ordem, nome, cnpj, logradouro, endcompl
        FROM peticoes.entrevista_reclamadas WHERE entrevista_id = %s ORDER BY ordem
    """, (eid,))
    rec = cur.fetchall()

    return {
        "entrevista_id": e["id"],
        "codigo": e["codigo"],
        "status": e["status"],
        "reclamante": e["recl_nome"],
        "chatwoot_conversation_id": e["chatwoot_conversation_id"],
        "campos_preenchidos": len(preenchido),
        "campos_no_roteiro": len(cat["lista"]),
        "faltando": faltando,
        "proxima_pergunta": proxima,
        "reclamadas": rec,
        "pronta_para_peticao": bool(e["recl_nome"]) and e["status"] == "concluida",
        "atualizado_em": e["atualizado_em"].isoformat() if e["atualizado_em"] else None,
    }


def painel(cur, conta, conversa):
    """O caso de uma conversa, do jeito que o painel do Chatwoot mostra.

    Junta numa leitura só o que hoje está espalhado: a entrevista, o estado da
    coleta e o retorno da última tentativa de gerar a peça — inclusive o motivo
    de o gate ter barrado, que é o que o atendente precisa ver para agir.
    """
    cur.execute("""
        SELECT e.id, e.status::text AS status, e.municipio, e.atualizado_em,
               peticoes.montar_entrevista(e.id) AS entrevista,
               (SELECT count(*) FROM peticoes.perguntas WHERE ativo) AS campos_roteiro
          FROM peticoes.entrevistas e
         WHERE e.chatwoot_account_id = %s AND e.chatwoot_conversation_id = %s
         ORDER BY e.id DESC LIMIT 1
    """, (conta, conversa))
    e = cur.fetchone()
    if not e:
        return {"encontrado": False}

    ent = e["entrevista"] or {}
    # A última tentativa nem sempre é a que tem resposta útil: uma falha de
    # terceiro (a API caiu, a CCT saiu do ar) grava tentativa sem `validacao` e
    # apagaria da tela o resultado bom da tentativa anterior. Prefere-se a mais
    # recente que a API de fato respondeu.
    cur.execute("""
        SELECT status::text, valor_causa, rito::text, resposta
          FROM peticoes.peticoes
         WHERE entrevista_id = %s
         ORDER BY (resposta ? 'validacao') DESC, tentativa DESC
         LIMIT 1
    """, (e["id"],))
    p = cur.fetchone()

    peticao = None
    if p:
        r = p["resposta"] or {}
        v = r.get("validacao") or {}
        peticao = {
            "aprovado": v.get("aprovado") is True,
            "valor_causa": peticoes_brl(p["valor_causa"]),
            "rito": p["rito"],
            "registro_id": r.get("registro_id"),
            "bloqueios": [f"{x.get('codigo')}: {x.get('detalhe')}"
                          for x in (v.get("problemas") or []) if x.get("bloqueia")],
            "verbas": [{"rubrica": x.get("rubrica"), "total": peticoes_brl(x.get("total"))}
                       for x in (r.get("verbas") or [])],
        }

    return {
        "encontrado": True,
        "entrevista_id": e["id"],
        "status": e["status"],
        "municipio": e["municipio"],
        "campos": len(ent),
        "campos_roteiro": e["campos_roteiro"],
        "entrevista": ent,
        "peticao": peticao,
        "atualizado_em": e["atualizado_em"].strftime("%d/%m %H:%M") if e["atualizado_em"] else None,
    }


def peticoes_brl(v):
    """1850 -> 'R$ 1.850,00'. O painel mostra dinheiro como o cliente lê."""
    if v in (None, ""):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    return "R$ " + f"{n:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def marcar_pendente(cur, eid, campo):
    cur.execute("""
        UPDATE peticoes.entrevistas
           SET pergunta_pendente = %s,
               status = CASE WHEN status IN ('rascunho','em_andamento')
                             THEN 'aguardando_cliente'::peticoes.status_entrevista
                             ELSE status END
         WHERE id = %s RETURNING id
    """, (campo, eid))
    return cur.fetchone() is not None


def concluir(cur, eid):
    cur.execute("""
        UPDATE peticoes.entrevistas
           SET status = 'concluida', concluida_em = now(), pergunta_pendente = NULL
         WHERE id = %s RETURNING id
    """, (eid,))
    return cur.fetchone() is not None


def registrar_documento(cur, eid, doc):
    cur.execute("""
        INSERT INTO peticoes.entrevista_documentos
            (entrevista_id, tipo, nome_arquivo, url, mime, tamanho_bytes,
             chatwoot_message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (eid, doc.get("tipo"), doc.get("nome_arquivo") or doc.get("nome"),
          doc.get("url"), doc.get("mime"), doc.get("tamanho_bytes"),
          doc.get("chatwoot_message_id") or doc.get("mensagem_id")))
    return cur.fetchone()["id"]


def logar(cur, eid, metodo, rota, status, corpo, resposta, ip, ms):
    try:
        cur.execute("""
            INSERT INTO peticoes.ingestao_log
                (entrevista_id, metodo, rota, http_status, corpo, resposta,
                 origem_ip, duracao_ms)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """, (eid, metodo, rota, status,
              json.dumps(corpo, default=str)[:100000] if corpo is not None else None,
              json.dumps(resposta, default=str)[:100000] if resposta is not None else None,
              ip, ms))
    except Exception:
        pass      # log nunca derruba a requisição
