#!/usr/bin/env python3
"""Roda um arquivo .sql (ou SQL na linha de comando) no banco de DATABASE_URL.

    python3 db/rodar_sql.py db/teste_smoke.sql
    python3 db/rodar_sql.py -c "SELECT count(*) FROM peticoes.perguntas"
"""
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ.parent / ".pylibs"))

import psycopg  # noqa: E402

from apply import dsn  # noqa: E402  reaproveita a leitura do .env


def imprimir(cur) -> None:
    if cur.description is None:
        return
    cols = [d.name for d in cur.description]
    linhas = cur.fetchall()
    print(" | ".join(cols))
    print("-" * 60)
    for linha in linhas:
        print(" | ".join("" if v is None else str(v) for v in linha))
    print(f"({len(linhas)} linha(s))\n")


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "-c":
        sql = sys.argv[2]
    elif len(sys.argv) >= 2:
        sql = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        sys.exit(__doc__)

    with psycopg.connect(dsn()) as conn, conn.cursor() as cur:
        # múltiplos comandos num só execute -> percorre os resultados com nextset()
        cur.execute(sql)
        while True:
            imprimir(cur)
            if not cur.nextset():
                break
        conn.commit()


if __name__ == "__main__":
    main()
