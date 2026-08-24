# API de ingestão das entrevistas trabalhistas.
#
# O servidor é biblioteca padrão do Python: a única dependência real é o driver
# do Postgres. Por isso a imagem é slim e não precisa de toolchain — o wheel
# binário do psycopg já traz o libpq compilado.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    INGESTAO_HOST=0.0.0.0 \
    INGESTAO_PORT=8088

WORKDIR /app

# Dependências primeiro: muda muito menos que o código, então a camada é
# reaproveitada em todo deploy que só mexe em .py.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api/ ./api/
COPY db/  ./db/
COPY docker-entrypoint.sh .
RUN chmod +x docker-entrypoint.sh

# Sem root: o processo não escreve em disco, não há motivo para ter permissão.
RUN useradd --system --create-home --uid 10001 ingestao \
    && chown -R ingestao:ingestao /app
USER ingestao

EXPOSE 8088

# O Coolify tem healthcheck próprio; este cobre `docker run` avulso e o
# `docker compose` local. /saude devolve 503 se o banco não responder, então
# container no ar com banco fora não passa por saudável.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8088/saude', timeout=4).status==200 else 1)"

ENTRYPOINT ["./docker-entrypoint.sh"]
