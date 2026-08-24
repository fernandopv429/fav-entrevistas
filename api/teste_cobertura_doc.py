#!/usr/bin/env python3
"""Trava o contrato com a documentação da API de Petições 0.8.2.

Por que existe: o banco só serve para uma coisa — virar o corpo do POST
/peca/da-entrevista. Um campo que a doc lista e o schema não tem não dá erro
em lugar nenhum: a peça simplesmente sai pior, e ninguém percebe. Este teste
manda os 63 campos documentados por dentro do mesmo caminho que o agente usa
(dados.gravar) e confere que todos voltam em peticoes.montar_payload.

Não precisa do servidor no ar, só do banco. Nada fica gravado: roda em
transação e dá rollback no fim.

    python3 api/teste_cobertura_doc.py
"""
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / ".pylibs"))
sys.path.insert(0, str(RAIZ / "api"))

import dados  # noqa: E402

CONTA, CONVERSA = 99, 99_0001

# Os 63 campos do objeto `entrevista` na documentação, na ordem em que ela os
# apresenta. Valores plausíveis: o teste é de cobertura e de coerção junto.
DOCUMENTADOS = {
    # identificação do reclamante
    "RECL_NOME": "MARCOS MOREIRA PAULO",
    "RECL_CPF": "123.456.789-00",
    "RECL_RG": "12.345.678-9",
    "RECL_PIS": "123.45678.90-1",
    "RECL_CTPS": "1234567",
    "RECL_SERIE": "0001-SP",
    "RECL_NASC": "1990-03-14",
    "RECL_FILIACAO": "José Paulo e Maria Moreira",
    "RECL_ENDERECO": "Rua das Flores, 100, Centro, São Paulo/SP",
    "RECL_CEP": "01000-000",
    "RECL_NACIONALIDADE": "brasileiro",
    "RECL_ESTADOCIVIL": "solteiro",
    "email": "marcos@exemplo.com",
    # reclamadas
    "RECL1_NOME": "VIGSEG VIGILANCIA LTDA",
    "RECL1_CNPJ": "04.542.518/0002-99",
    "RECL1_LOGRADOURO": "Av. Paulista, 1000",
    "RECL1_ENDCOMPL": "São Paulo/SP, CEP 01310-100",
    "RECL2_NOME": "GLP REGIS",
    "RECL2_CNPJ": "11.222.333/0001-44",
    "RECL2_LOGRADOURO": "Rod. Regis Bittencourt, km 279",
    "RECL2_ENDCOMPL": "Itapecerica da Serra/SP, CEP 06877-115",
    "RECL3_NOME": "TERCEIRA S/A",
    "RECL3_CNPJ": "55.666.777/0001-88",
    "RECL3_LOGRADOURO": "Rua Terceira, 3",
    "RECL3_ENDCOMPL": "Cotia/SP, CEP 06700-000",
    # contrato e remuneração
    "DATA_ADMISSAO": "2025-04-14",
    "DATA_RESCISAO": "2025-12-07",
    "SALARIO": "R$ 2.148,22",
    "FUNCAO": "Vigilante",
    "tipo_dispensa": "nulidade_pedido_demissao",
    # jornada
    "escala": "12x36",
    "JORNADA_HORARIO": "19h às 7h",
    "tem_adic_noturno": True,
    "finais_semana": True,
    "intervalo_suprimido": True,
    "INTERVALO_GOZADO": "15 a 20 minutos",
    "media_horas_extras": "Até 1 hora",
    "periodo_antecedente": "30 minutos",
    "periodo_sucedente": "20 minutos",
    # folgas trabalhadas
    "folgas_trabalhadas": True,
    "FT_QTD_MEDIA": "5 a 6",
    "VAL_FT": "R$ 180 a R$ 200",
    "ft_pagamento": "PIX",
    # benefícios
    "vale_refeicao": True,
    "vale_alimentacao": True,
    "vale_transporte": True,
    "VALOR_AUX_ALIMENTACAO": "R$ 25,00",
    "VAL_CONDUCAO": "R$ 9,60",
    # teses
    "acumulo_funcao": True,
    "funcoes_acumuladas": "abastecia caminhões e conferia carga",
    "gratificacao": True,
    "gratificacao_qual": "gratificação de função, R$ 300",
    "assiduidade": True,
    "assiduidade_prometido": "R$ 200",
    "assiduidade_pago": "R$ 120",
    "tem_periculosidade": True,
    "tem_insalubridade": False,
    "tem_doenca": False,
    "desconto_indevido": True,
    "desconto_qual": "desconto de uniforme",
    # documentos e narrativa
    "holerites": True,
    "espelho_ponto": False,
    "fatos_narrados": "Trabalhava em escala 12x36 no posto da Regis Bittencourt.",
}

# "Campos coletados que o motor ignora": entram por `extras` e viajam no payload
# (a doc garante que enviar não causa erro). Coletar agora evita reentrevistar
# o cliente quando armamento_colete/produtos/epi passarem a contar.
IGNORADOS_PELO_MOTOR = {
    "modelo_peticao": "trabalhista",
    "titulo": "Reclamação Trabalhista",
    "telefone": "11 99999-0000",
    "ferias": False,
    "ferias_quantidade": "2 períodos",
    "ft_comprovante": True,
    "horas_extras": True,
    "armamento_colete": True,
    "rescisao_contratual": False,
    "produtos": "desengraxante",
    "epi": True,
    "testemunha": "João, colega de posto",
}

# As opções do corpo do POST, fora de `entrevista`.
OPCOES = ["codigo", "salario", "municipio", "redigir_ia", "gerar_pdf", "persistir",
          "consultar_cct", "consultar_cnpj", "incluir_pdf_base64"]

ok = 0
falhas = []


def conferir(condicao, descricao):
    global ok
    if condicao:
        ok += 1
        print(f"  ok   {descricao}")
    else:
        falhas.append(descricao)
        print(f"  FALHA {descricao}")


def main():
    print("conferindo o schema contra a documentação 0.8.2\n")
    tudo = dict(DOCUMENTADOS, **IGNORADOS_PELO_MOTOR)

    with dados.conectar() as conn:
        with conn.cursor() as cur:
            estado = dados.gravar(cur, {
                "chatwoot_account_id": CONTA,
                "chatwoot_conversation_id": CONVERSA,
                "campos": tudo,
            })
            cur.execute("SELECT peticoes.montar_payload(%s) AS p",
                        (estado["entrevista_id"],))
            payload = cur.fetchone()["p"]
        conn.rollback()          # nada é gravado de verdade

    entrevista = payload.get("entrevista", {})

    print("ingestão")
    conferir(not estado["erros"], f"nenhum campo recusado na coerção {estado['erros']}")
    conferir(not estado["ignorados"],
             f"nenhum campo caiu em nao_mapeados {estado['ignorados']}")

    print("\ncampos documentados no payload")
    ausentes = [c for c in DOCUMENTADOS if c not in entrevista]
    conferir(not ausentes, f"os {len(DOCUMENTADOS)} campos da doc viajam no payload")
    if ausentes:
        print(f"       faltaram: {ausentes}")

    print("\ncampos coletados que o motor ignora")
    ausentes = [c for c in IGNORADOS_PELO_MOTOR if c not in entrevista]
    conferir(not ausentes, f"os {len(IGNORADOS_PELO_MOTOR)} campos extras viajam junto")
    if ausentes:
        print(f"       faltaram: {ausentes}")

    print("\nformato exigido pela API")
    conferir(entrevista.get("SALARIO") == "R$ 2.148,22",
             "SALARIO sai como texto no padrão BR")
    conferir(entrevista.get("DATA_ADMISSAO") == "2025-04-14",
             "datas saem em ISO")
    conferir(entrevista.get("tipo_dispensa") == "nulidade_pedido_demissao",
             "tipo_dispensa aceita nulidade_pedido_demissao")
    conferir(entrevista.get("periodo_antecedente") == "30 minutos",
             "periodo_antecedente leva a unidade junto")
    conferir(entrevista.get("gratificacao") is True,
             "gratificacao é booleano")
    conferir(entrevista.get("gratificacao_qual") == "gratificação de função, R$ 300",
             "gratificacao_qual guarda o texto que desambigua a verba")
    conferir(entrevista.get("tem_insalubridade") is False,
             "false é preservado (não some junto com os nulos)")

    print("\nopções do POST")
    faltando = [o for o in OPCOES if o not in payload and o != "municipio"]
    conferir(not faltando, f"as opções do corpo estão presentes {faltando}")
    conferir(payload.get("consultar_cnpj") is True,
             "consultar_cnpj vai ligado (é o que resolve a categoria pelo CNAE)")

    print(f"\n{ok} passaram, {len(falhas)} falharam")
    if falhas:
        for f in falhas:
            print(f"  - {f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
