"""Converte o que o agente de IA manda para os tipos das colunas.

O agente conversa em português e repassa o que o cliente escreveu. Então
"sim", "12/04/1988" e "R$ 2.148,22" precisam virar boolean, date e numeric.
Quando não dá para entender com segurança, devolve erro em vez de chutar —
gravar a data errada é pior do que deixar o campo vazio.
"""
import datetime
import re
import unicodedata

VERDADEIRO = {
    "sim", "s", "true", "1", "verdadeiro", "v", "yes", "y", "tinha", "havia",
    "recebia", "trabalhava", "positivo", "com certeza", "certo", "isso",
    "exato", "afirmativo", "tenho", "tem", "possuo",
}
FALSO = {
    "nao", "n", "false", "0", "falso", "f", "no", "nunca", "jamais",
    "negativo", "nenhum", "nenhuma", "sem", "nada", "não tinha", "nao tinha",
    "nao recebia", "não recebia", "nao tenho", "não tenho",
}

MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5,
    "junho": 6, "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10,
    "novembro": 11, "dezembro": 12,
}


class ErroCoercao(ValueError):
    """Valor recebido não pôde ser convertido com segurança."""


def sem_acento(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def normalizar(texto: str) -> str:
    return sem_acento(str(texto)).strip().lower()


def para_booleano(valor):
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    t = normalizar(valor)
    if t in VERDADEIRO:
        return True
    if t in FALSO:
        return False
    # frases: pega a primeira palavra reconhecível
    for palavra in re.split(r"[\s,.;!?]+", t):
        if palavra in VERDADEIRO:
            return True
        if palavra in FALSO:
            return False
    raise ErroCoercao(f"não entendi como sim/não: {valor!r}")


def para_data(valor):
    if isinstance(valor, datetime.date):
        return valor
    t = str(valor).strip()

    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", t)
    if m:
        a, mes, d = (int(x) for x in m.groups())
    else:
        m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", t)
        if m:
            d, mes, a = (int(x) for x in m.groups())
            if a < 100:                       # 24 -> 2024, 88 -> 1988
                a += 2000 if a <= 30 else 1900
        else:
            # "12 de abril de 1988"
            m = re.match(r"^(\d{1,2})\s*de\s*([a-zç]+)\s*de\s*(\d{4})$", normalizar(t))
            if m and normalizar(m.group(2)) in MESES:
                d = int(m.group(1))
                mes = MESES[normalizar(m.group(2))]
                a = int(m.group(3))
            else:
                # "abril de 2019" -> dia 1, melhor que nada para admissão antiga
                m = re.match(r"^([a-zç]+)\s*de\s*(\d{4})$", normalizar(t))
                if m and m.group(1) in MESES:
                    d, mes, a = 1, MESES[m.group(1)], int(m.group(2))
                else:
                    raise ErroCoercao(f"não entendi a data: {valor!r}")
    try:
        return datetime.date(a, mes, d)
    except ValueError as e:
        raise ErroCoercao(f"data inválida: {valor!r} ({e})") from e


def para_moeda(valor):
    """'R$ 2.148,22' | '2148.22' | 'uns 2 mil e 148' -> Decimal-compatível."""
    if isinstance(valor, (int, float)):
        return round(float(valor), 2)
    t = re.sub(r"(?i)r\$|reais|por m[eê]s|mensais|/m[eê]s", " ", str(valor))
    t = re.sub(r"[^\d,.\-]", "", t).strip()
    if not t:
        raise ErroCoercao(f"não achei um valor em: {valor!r}")
    if "," in t and "." in t:
        # o separador que aparece por último é o decimal
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".")
    elif t.count(".") == 1 and len(t.split(".")[1]) == 3:
        t = t.replace(".", "")          # '2.148' é milhar, não decimal
    try:
        return round(float(t), 2)
    except ValueError as e:
        raise ErroCoercao(f"não entendi o valor: {valor!r}") from e


def para_numero(valor):
    """'15 minutos' -> 15 ; 'meia hora' fica sem entender (melhor errar aqui)."""
    if isinstance(valor, bool):
        raise ErroCoercao(f"esperava um número, veio {valor!r}")
    if isinstance(valor, int):
        return valor
    if isinstance(valor, float):
        return int(round(valor))
    m = re.search(r"-?\d+", str(valor))
    if not m:
        raise ErroCoercao(f"não achei um número em: {valor!r}")
    return int(m.group(0))


def para_escolha(valor, opcoes):
    t = normalizar(valor)
    if not opcoes:
        return str(valor).strip()
    for o in opcoes:
        if normalizar(o) == t:
            return o
    for o in opcoes:                       # match parcial: 'indireta' -> 'rescisao_indireta'
        no = normalizar(o).replace("_", " ")
        if t and (t in no or no in t):
            return o
    raise ErroCoercao(f"{valor!r} não é uma das opções: {', '.join(opcoes)}")


def para_texto(valor):
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    t = str(valor).strip()
    if not t:
        raise ErroCoercao("texto vazio")
    return t


def converter(valor, tipo: str, opcoes=None):
    """Aplica a conversão conforme perguntas.tipo. Devolve None para valor nulo."""
    if valor is None:
        return None
    if isinstance(valor, str) and not valor.strip():
        return None
    if tipo == "booleano":
        return para_booleano(valor)
    if tipo == "data":
        return para_data(valor)
    if tipo == "moeda":
        return para_moeda(valor)
    if tipo == "numero":
        return para_numero(valor)
    if tipo == "escolha":
        return para_escolha(valor, opcoes)
    return para_texto(valor)               # texto, texto_longo, faixa, anexo
