#!/bin/sh
# Aplica as migrações e sobe a API.
#
# db/apply.py é idempotente (CREATE ... IF NOT EXISTS, ON CONFLICT DO UPDATE),
# então rodar a cada start é seguro e evita o deploy que sobe com o código novo
# e o schema velho. Para desligar — por exemplo se o banco for gerido à parte —
# defina APLICAR_MIGRACOES=0.
set -e

if [ "${APLICAR_MIGRACOES:-1}" = "1" ]; then
    echo "aplicando migrações..."
    python3 db/apply.py
fi

echo "subindo a API em ${INGESTAO_HOST:-0.0.0.0}:${INGESTAO_PORT:-8088}"
exec python3 api/servidor.py
