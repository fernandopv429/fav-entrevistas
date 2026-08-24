-- =============================================================================
--  coagir_data: aceitar data em português por extenso.
--
--  Por que: o agente de IA copia o valor como o cliente falou — "14 de abril de
--  2025" — e a coerção só entendia ISO e DD/MM/AAAA, então a data virava NULL.
--  Data ausente BLOQUEIA a peça (sem período não há avos, FGTS nem férias), e o
--  caminho Python (api/coercao.py) já aceitava esse formato: as duas ingestões
--  estavam discordando sobre o mesmo dado.
--
--  Idempotente.
-- =============================================================================

SET search_path TO peticoes, public;

CREATE OR REPLACE FUNCTION peticoes.coagir_data(t text)
RETURNS date LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    s     text := lower(btrim(translate(COALESCE(t, ''), 'ãáâàéêíóôõúüç', 'aaaaeeiooouuc')));
    meses text[] := ARRAY['janeiro','fevereiro','marco','abril','maio','junho',
                          'julho','agosto','setembro','outubro','novembro','dezembro'];
    m     text[];
    idx   integer;
BEGIN
    IF s = '' THEN RETURN NULL; END IF;

    -- ISO: 2025-04-14
    IF s ~ '^\d{4}-\d{2}-\d{2}' THEN
        RETURN substring(s from 1 for 10)::date;
    END IF;

    -- BR: 14/04/2025 ou 14-04-2025
    IF s ~ '^\d{1,2}[/-]\d{1,2}[/-]\d{4}$' THEN
        RETURN to_date(translate(s, '-', '/'), 'DD/MM/YYYY');
    END IF;

    -- por extenso: "14 de abril de 2025"
    m := regexp_match(s, '^(\d{1,2})\s+de\s+([a-z]+)\s+de\s+(\d{4})$');
    IF m IS NOT NULL THEN
        idx := array_position(meses, m[2]);
        IF idx IS NOT NULL THEN
            RETURN make_date(m[3]::int, idx, m[1]::int);
        END IF;
    END IF;

    -- só mês e ano: "abril de 2019" -> dia 1. Vale para admissão antiga, em que
    -- o cliente não lembra o dia; melhor o mês certo que nenhum período.
    m := regexp_match(s, '^([a-z]+)\s+de\s+(\d{4})$');
    IF m IS NOT NULL THEN
        idx := array_position(meses, m[1]);
        IF idx IS NOT NULL THEN
            RETURN make_date(m[2]::int, idx, 1);
        END IF;
    END IF;

    RETURN NULL;   -- formato desconhecido: NULL é melhor que data errada
EXCEPTION WHEN others THEN RETURN NULL;
END $$;
