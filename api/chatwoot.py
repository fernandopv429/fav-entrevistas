"""Conversa com a API do Chatwoot usando o token do Agent Bot.

Por que existe: a etiqueta é o que autoriza retomar um caso já concluído, mas o
payload do webhook message_created NÃO traz as etiquetas da conversa. Então quem
precisa saber, pergunta.

Use o "Token de acesso" do robô (tela Robôs > Alterar Robô) em
CHATWOOT_API_TOKEN. Ele é melhor que um token de admin: o Chatwoot restringe
tokens de robô a uma lista branca (BOT_ACCESSIBLE_ENDPOINTS em
app/controllers/concerns/access_token_auth_helper.rb), que é justo o de que
precisamos:

    conversations            -> show, update, custom_attributes, toggle_status,
                                toggle_priority, toggle_typing_status, create
    conversations/labels     -> index, create
    conversations/messages   -> create
    conversations/assignments-> create

Cabeçalho de autenticação: api_access_token.

ATENÇÃO ao POST de etiquetas: ele SOBRESCREVE a lista inteira da conversa. Por
isso `adicionar_etiqueta`/`remover_etiqueta` leem antes e reescrevem o conjunto
completo — mandar só a etiqueta nova apagaria as que o time colocou.
"""
import json
import os
import pathlib
import urllib.error
import urllib.parse
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parent.parent
_TEMPO_LIMITE = 8


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


def configurado() -> bool:
    return bool(env("CHATWOOT_BASE_URL") and env("CHATWOOT_API_TOKEN"))


def _chamar(metodo, caminho, corpo=None):
    """Chamada crua à API do Chatwoot. Devolve (ok, dados)."""
    if not configurado():
        return False, None
    base = env("CHATWOOT_BASE_URL").rstrip("/")
    req = urllib.request.Request(
        base + caminho,
        data=json.dumps(corpo, ensure_ascii=False).encode() if corpo is not None else None,
        method=metodo,
        headers={
            "api_access_token": env("CHATWOOT_API_TOKEN"),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_TEMPO_LIMITE) as r:
            bruto = r.read().decode("utf-8") or "null"
            return True, json.loads(bruto)
    except urllib.error.HTTPError as e:
        corpo_erro = ""
        try:
            corpo_erro = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        return False, {"http_status": e.code, "detalhe": corpo_erro}
    except (urllib.error.URLError, ValueError, OSError) as e:
        return False, {"erro": str(e)}


def escrever_custom_attributes(account_id, conversation_display_id, atributos):
    """Grava atributos personalizados na conversa — o time vê no próprio Chatwoot.

    Útil para deixar o id da entrevista visível ao lado da conversa, em vez de
    ficar só no nosso banco.
    """
    if not (account_id and conversation_display_id and atributos):
        return False
    ok, _ = _chamar(
        "POST",
        f"/api/v1/accounts/{int(account_id)}/conversations/"
        f"{int(conversation_display_id)}/custom_attributes",
        {"custom_attributes": atributos},
    )
    return ok


def definir_etiquetas(account_id, conversation_display_id, etiquetas):
    """Substitui a lista de etiquetas da conversa (é o que o Chatwoot faz)."""
    ok, _ = _chamar(
        "POST",
        f"/api/v1/accounts/{int(account_id)}/conversations/"
        f"{int(conversation_display_id)}/labels",
        {"labels": list(etiquetas)},
    )
    return ok


def adicionar_etiqueta(account_id, conversation_display_id, etiqueta):
    """Acrescenta uma etiqueta preservando as demais (lê, une, reescreve)."""
    atuais = etiquetas_da_conversa(account_id, conversation_display_id)
    if atuais is None:
        return False
    if etiqueta in atuais:
        return True
    return definir_etiquetas(account_id, conversation_display_id, atuais + [etiqueta])


def remover_etiqueta(account_id, conversation_display_id, etiqueta):
    """Remove uma etiqueta preservando as demais.

    Serve para consumir a etiqueta de retomada depois de honrá-la: a autorização
    passa a valer uma vez, em vez de ficar valendo para sempre.
    """
    atuais = etiquetas_da_conversa(account_id, conversation_display_id)
    if atuais is None:
        return False
    restantes = [e for e in atuais if e != etiqueta]
    if len(restantes) == len(atuais):
        return True
    return definir_etiquetas(account_id, conversation_display_id, restantes)


def etiquetas_da_conversa(account_id, conversation_display_id):
    """Etiquetas da conversa, ou None quando não foi possível determinar.

    None e [] são coisas diferentes: [] significa "consultei, não há etiqueta";
    None significa "não sei" — e aí não se retoma caso concluído.
    """
    if not configurado() or not (account_id and conversation_display_id):
        return None

    base = env("CHATWOOT_BASE_URL").rstrip("/")
    url = (f"{base}/api/v1/accounts/{int(account_id)}"
           f"/conversations/{int(conversation_display_id)}/labels")
    req = urllib.request.Request(url, headers={
        "api_access_token": env("CHATWOOT_API_TOKEN"),
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=_TEMPO_LIMITE) as r:
            dados = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        return None

    # a resposta é {"payload": ["etiqueta-a", "etiqueta-b"]}
    if isinstance(dados, dict):
        payload = dados.get("payload")
        if isinstance(payload, list):
            return [str(x) for x in payload]
    if isinstance(dados, list):
        return [str(x) for x in dados]
    return None
