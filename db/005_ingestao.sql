-- =============================================================================
--  Suporte à ingestão via API: campos que o agente de IA precisa manter
--  e log das requisições recebidas.
-- =============================================================================

SET search_path TO peticoes, public;

-- Campos enviados pelo agente que não batem com nenhum campo do roteiro.
-- Ficam guardados (nada se perde) mas FORA do payload da API de petições.
ALTER TABLE peticoes.entrevistas
    ADD COLUMN IF NOT EXISTS nao_mapeados jsonb NOT NULL DEFAULT '{}'::jsonb;

-- Última pergunta enviada ao cliente e ainda sem resposta.
ALTER TABLE peticoes.entrevistas
    ADD COLUMN IF NOT EXISTS pergunta_pendente text;

-- Log das requisições recebidas (auditoria da ingestão, espelha api_chamadas
-- que registra as chamadas de saída).
CREATE TABLE IF NOT EXISTS peticoes.ingestao_log (
    id             bigserial PRIMARY KEY,
    entrevista_id  bigint REFERENCES peticoes.entrevistas(id) ON DELETE SET NULL,
    metodo         text NOT NULL,
    rota           text NOT NULL,
    http_status    integer,
    corpo          jsonb,
    resposta       jsonb,
    origem_ip      text,
    duracao_ms     integer,
    criado_em      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingestao_log_entrevista_ix
    ON peticoes.ingestao_log (entrevista_id, criado_em DESC);
CREATE INDEX IF NOT EXISTS ingestao_log_criado_ix
    ON peticoes.ingestao_log (criado_em DESC);
