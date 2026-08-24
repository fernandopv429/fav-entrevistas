#!/usr/bin/env python3
"""Aplica as migrações .sql do diretório db/ no Postgres apontado por DATABASE_URL.

Uso:
    python3 db/apply.py            # aplica tudo, em ordem
    python3 db/apply.py --check    # só conecta e mostra o que já existe
"""
import os
import pathlib
import sys

RAIZ = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ.parent / ".pylibs"))  # driver extraído localmente

try:
    import psycopg  # psycopg 3
    CONNECT = psycopg.connect
except ModuleNotFoundError:
    try:
        import psycopg2
        CONNECT = psycopg2.connect
    except ModuleNotFoundError:
        sys.exit("Falta o driver. Rode: pip install 'psycopg[binary]'")



def dsn() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env = RAIZ.parent / ".env"
    if env.exists():
        for linha in env.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha.startswith("DATABASE_URL="):
                return linha.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("DATABASE_URL não definida (nem no ambiente, nem em .env)")


def inventario(cur) -> None:
    cur.execute("""
        SELECT table_name,
               (SELECT count(*) FROM information_schema.columns c
                 WHERE c.table_schema = t.table_schema
                   AND c.table_name = t.table_name) AS colunas
        FROM information_schema.tables t
        WHERE t.table_schema = 'peticoes'
        ORDER BY t.table_type, t.table_name
    """)
    linhas = cur.fetchall()
    if not linhas:
        print("  (schema peticoes vazio ou inexistente)")
        return
    for nome, colunas in linhas:
        print(f"  {nome:<28} {colunas:>3} colunas")


def main() -> None:
    somente_check = "--check" in sys.argv
    with CONNECT(dsn()) as conn:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute("SELECT version(), current_database(), current_user")
            versao, banco, usuario = cur.fetchone()
            print(f"conectado: {banco} como {usuario}")
            print(f"servidor:  {versao.split(',')[0]}\n")

            if somente_check:
                print("objetos no schema peticoes:")
                inventario(cur)
                return

            for arquivo in sorted(RAIZ.glob("[0-9][0-9][0-9]_*.sql")):
                sql = arquivo.read_text(encoding="utf-8")
                print(f"aplicando {arquivo.name} ...", end=" ", flush=True)
                cur.execute(sql)
                print("ok")
            conn.commit()

            print("\nobjetos no schema peticoes:")
            inventario(cur)


if __name__ == "__main__":
    main()
