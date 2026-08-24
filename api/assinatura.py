"""Validação da assinatura de webhook do Chatwoot (Agent Bot).

Quando o Chatwoot entrega um evento de robô com "Segredo do Webhook"
configurado, ele assina a requisição (lib/webhooks/trigger.rb):

    X-Chatwoot-Signature: sha256=<hex>
    X-Chatwoot-Timestamp: <unix>
    X-Chatwoot-Delivery:  <uuid>

    assinatura = HMAC-SHA256(segredo, f"{timestamp}.{corpo_cru}")

Duas sutilezas que decidem se a validação funciona:

1. O HMAC é sobre o CORPO CRU, byte a byte. Reserializar o JSON (mudando espaços
   ou ordem de chaves) invalida a assinatura. Por isso a função recebe bytes.
2. O timestamp entra na assinatura, então dá para recusar entregas antigas — é o
   que impede reenviar uma requisição capturada (replay).
"""
import hashlib
import hmac
import time

JANELA_PADRAO = 300      # 5 minutos


class AssinaturaInvalida(Exception):
    pass


def conferir(corpo_cru: bytes, cabecalhos, segredo: str,
             janela_segundos: int = JANELA_PADRAO) -> str:
    """Valida a assinatura. Devolve o delivery id; levanta AssinaturaInvalida.

    `cabecalhos` é qualquer objeto com .get() insensível a caixa (o do
    http.server serve).
    """
    if not segredo:
        raise AssinaturaInvalida("segredo do webhook não configurado no serviço")

    recebida = (cabecalhos.get("X-Chatwoot-Signature") or "").strip()
    timestamp = (cabecalhos.get("X-Chatwoot-Timestamp") or "").strip()
    entrega = (cabecalhos.get("X-Chatwoot-Delivery") or "").strip()

    if not recebida:
        raise AssinaturaInvalida("cabeçalho X-Chatwoot-Signature ausente")
    if not timestamp:
        raise AssinaturaInvalida("cabeçalho X-Chatwoot-Timestamp ausente")

    try:
        idade = abs(time.time() - float(timestamp))
    except ValueError as e:
        raise AssinaturaInvalida(f"timestamp inválido: {timestamp!r}") from e
    if janela_segundos and idade > janela_segundos:
        raise AssinaturaInvalida(
            f"entrega velha demais ({int(idade)}s); janela é {janela_segundos}s")

    mensagem = timestamp.encode("utf-8") + b"." + corpo_cru
    esperada = "sha256=" + hmac.new(
        segredo.encode("utf-8"), mensagem, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(recebida, esperada):
        raise AssinaturaInvalida("assinatura não corresponde ao corpo recebido")

    return entrega or ""


def extrair_do_evento(evento: dict) -> dict:
    """Traduz um evento de webhook do Chatwoot para o corpo do nosso upsert.

    Extrai só identificação — quem interpreta o texto da mensagem e decide quais
    campos foram informados é o agente de IA, não isto.

    Cuidado com os ids: no payload, `conversation.id` já é o display_id (o
    serializer popula id a partir de conversation.display_id), e `display_id`
    também vem. Usamos display_id quando existir.
    """
    conversa = evento.get("conversation") or {}
    conta = (evento.get("account") or {}).get("id") or evento.get("account_id")
    remetente = evento.get("sender") or conversa.get("meta", {}).get("sender") or {}
    contato = conversa.get("contact") or {}

    corpo = {
        "chatwoot_account_id": conta,
        "chatwoot_conversation_id": conversa.get("display_id") or conversa.get("id"),
        "chatwoot_conversation_uuid": conversa.get("uuid"),
        "chatwoot_inbox_id": (evento.get("inbox") or {}).get("id")
                             or conversa.get("inbox_id"),
        "mensagem_id": evento.get("id") if evento.get("event", "").startswith("message") else None,
    }

    cid = remetente.get("id") or contato.get("id")
    if cid:
        corpo["chatwoot_contact_id"] = cid
        corpo["contato"] = {
            "chatwoot_contact_id": cid,
            "nome": remetente.get("name") or contato.get("name"),
            "telefone": remetente.get("phone_number"),
            "email": remetente.get("email"),
            "identifier": remetente.get("identifier"),
        }

    if isinstance(conversa.get("labels"), list):
        corpo["etiquetas"] = conversa["labels"]

    return {k: v for k, v in corpo.items() if v is not None}
