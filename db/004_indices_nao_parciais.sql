-- Troca os índices únicos parciais por índices comuns: NULL não conflita em
-- índice único, e o predicado impedia o ON CONFLICT de inferir o índice
-- (erro "no unique or exclusion constraint matching the ON CONFLICT specification").

SET search_path TO peticoes, public;

DROP INDEX IF EXISTS peticoes.entrevistas_conversation_uk;
CREATE UNIQUE INDEX IF NOT EXISTS entrevistas_conversation_uk
    ON peticoes.entrevistas (chatwoot_account_id, chatwoot_conversation_id);

-- a chave é (mensagem, campo) — ver 001_schema.sql e 009_respostas_por_campo.sql
DROP INDEX IF EXISTS peticoes.entrevista_respostas_msg_uk;
CREATE UNIQUE INDEX IF NOT EXISTS entrevista_respostas_msg_campo_uk
    ON peticoes.entrevista_respostas (chatwoot_message_id, campo);
