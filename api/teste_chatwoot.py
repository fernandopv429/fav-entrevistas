#!/usr/bin/env python3
"""Confere o Token de acesso do Agent Bot contra a API do Chatwoot.

Rode depois de preencher CHATWOOT_BASE_URL e CHATWOOT_API_TOKEN no .env
(o token é o "Token de acesso" na tela Robôs > Alterar Robô):

    python3 api/teste_chatwoot.py                    # só checa a configuração
    python3 api/teste_chatwoot.py CONTA CONVERSA     # testa numa conversa real

Não escreve nada no Chatwoot: só faz leituras.
"""
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "api"))

import chatwoot  # noqa: E402


def main():
    base = chatwoot.env("CHATWOOT_BASE_URL")
    token = chatwoot.env("CHATWOOT_API_TOKEN")

    print("configuração")
    print(f"  CHATWOOT_BASE_URL  = {base or '(vazio)'}")
    print(f"  CHATWOOT_API_TOKEN = {'*' * 12 + token[-4:] if token else '(vazio)'}")

    if not chatwoot.configurado():
        print("\nFalta configurar. Sem isso a API não lê etiquetas por conta")
        print("própria — o n8n precisa mandar 'etiquetas' em cada requisição.")
        print("\nOnde achar: Chatwoot > Configurações > Robôs > Alterar Robô >")
        print("Token de acesso (botão Copiar).")
        return 1

    if len(sys.argv) < 3:
        print("\nPara testar de verdade, passe uma conta e uma conversa reais:")
        print("    python3 api/teste_chatwoot.py 1 42")
        print("\n(o número da conversa é o que aparece na URL do Chatwoot)")
        return 0

    conta, conversa = int(sys.argv[1]), int(sys.argv[2])
    print(f"\nlendo a conversa {conversa} da conta {conta}")

    ok, dados = chatwoot._chamar(
        "GET", f"/api/v1/accounts/{conta}/conversations/{conversa}")
    if ok:
        estado = (dados or {}).get("status")
        print(f"  ok   conversations#show respondeu (status da conversa: {estado})")
    else:
        print(f"  ERRO conversations#show: {dados}")
        if isinstance(dados, dict) and dados.get("http_status") == 401:
            print("       401 = token inválido ou de outra conta")
        elif isinstance(dados, dict) and dados.get("http_status") == 404:
            print("       404 = conversa não existe nessa conta")
        return 1

    etiquetas = chatwoot.etiquetas_da_conversa(conta, conversa)
    if etiquetas is None:
        print("  ERRO labels#index não respondeu — a retomada por etiqueta não vai")
        print("       funcionar por consulta; o n8n terá que mandar 'etiquetas'")
        return 1
    print(f"  ok   labels#index respondeu: {etiquetas or '(nenhuma etiqueta)'}")

    alvo = chatwoot.env("ETIQUETA_RETOMAR", "retomar-entrevista")
    if alvo in (etiquetas or []):
        print(f"  ->   esta conversa TEM a etiqueta '{alvo}': um caso concluído")
        print("       deste contato seria retomado, não duplicado")
    else:
        print(f"  ->   esta conversa não tem '{alvo}': caso concluído não é mexido")

    print("\nToken funcionando. A API já consegue ler etiquetas sozinha.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
