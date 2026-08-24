-- =============================================================================
--  Vínculo entrevista <-> conversas do Chatwoot, em dois níveis.
--
--  Por quê: no Chatwoot uma conversa é uma sessão de atendimento — ela é
--  resolvida e, quando o cliente volta a falar, abre OUTRA conversa, com novo
--  display_id. Uma entrevista de 55 campos não se preenche numa sentada.
--  Amarrar a entrevista a uma única conversa perde tudo quando o cliente volta.
--
--  Então: o caso pertence ao CONTATO; as CONVERSAS que o alimentaram ficam
--  aqui, várias por entrevista. Uma conversa serve a um caso só (unique).
--
--  Atenção ao display_id: ele vem de uma sequência POR CONTA
--  (conv_dpid_seq_{account_id}), logo só é único junto com o account_id.
-- =============================================================================

SET search_path TO peticoes, public;

CREATE TABLE IF NOT EXISTS peticoes.entrevista_conversas (
    id                          bigserial PRIMARY KEY,
    entrevista_id               bigint NOT NULL
                                  REFERENCES peticoes.entrevistas(id) ON DELETE CASCADE,
    chatwoot_account_id         bigint NOT NULL,
    -- é o display_id: o que o Chatwoot chama de "id" da conversa na API e no
    -- payload do webhook. Sequencial por conta, não é único global.
    chatwoot_conversation_id    bigint NOT NULL,
    -- uuid da conversa: único global quando o payload traz. Âncora mais forte.
    chatwoot_conversation_uuid  uuid,
    chatwoot_inbox_id           bigint,
    -- etiquetas vistas nesta conversa (as do webhook não vêm; o agente informa
    -- ou o serviço consulta a API do Chatwoot)
    etiquetas                   text[],
    primeira_em                 timestamptz NOT NULL DEFAULT now(),
    ultima_em                   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT entrevista_conversas_uk
        UNIQUE (chatwoot_account_id, chatwoot_conversation_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS entrevista_conversas_uuid_uk
    ON peticoes.entrevista_conversas (chatwoot_conversation_uuid);
CREATE INDEX IF NOT EXISTS entrevista_conversas_entrevista_ix
    ON peticoes.entrevista_conversas (entrevista_id, ultima_em DESC);

-- Traz o vínculo que já estava nas colunas de entrevistas.
INSERT INTO peticoes.entrevista_conversas
    (entrevista_id, chatwoot_account_id, chatwoot_conversation_id, chatwoot_inbox_id)
SELECT id, chatwoot_account_id, chatwoot_conversation_id, chatwoot_inbox_id
FROM peticoes.entrevistas
WHERE chatwoot_account_id IS NOT NULL
  AND chatwoot_conversation_id IS NOT NULL
ON CONFLICT (chatwoot_account_id, chatwoot_conversation_id) DO NOTHING;

-- O índice único em entrevistas travava o caso numa conversa só. As colunas
-- ficam como "conversa de origem" (útil para leitura rápida), sem exclusividade.
DROP INDEX IF EXISTS peticoes.entrevistas_conversation_uk;
CREATE INDEX IF NOT EXISTS entrevistas_conversation_ix
    ON peticoes.entrevistas (chatwoot_account_id, chatwoot_conversation_id);

COMMENT ON COLUMN peticoes.entrevistas.chatwoot_conversation_id IS
    'conversa de origem (display_id). O vínculo completo está em entrevista_conversas';

-- Rastro de retomada: quando um caso concluído volta a ser preenchido por
-- causa da etiqueta, fica registrado quem reabriu e por qual etiqueta.
ALTER TABLE peticoes.entrevistas
    ADD COLUMN IF NOT EXISTS reaberta_em        timestamptz,
    ADD COLUMN IF NOT EXISTS reaberta_etiqueta  text,
    ADD COLUMN IF NOT EXISTS reaberturas        integer NOT NULL DEFAULT 0;

-- ----------------------------------------------------------------------------
-- Visão: todas as conversas de cada caso, para conferência rápida
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW peticoes.vw_entrevista_conversas AS
SELECT e.id AS entrevista_id,
       e.codigo,
       e.recl_nome,
       e.status,
       count(c.id)                       AS qtd_conversas,
       array_agg(c.chatwoot_conversation_id ORDER BY c.primeira_em) AS conversas,
       min(c.primeira_em)                AS primeiro_contato,
       max(c.ultima_em)                  AS ultimo_contato,
       e.reaberturas
FROM peticoes.entrevistas e
LEFT JOIN peticoes.entrevista_conversas c ON c.entrevista_id = e.id
GROUP BY e.id, e.codigo, e.recl_nome, e.status, e.reaberturas;
