-- =============================================================================
--  CPF e CNPJ saem formatados no payload.
--
--  Por que: a API de Petições só consulta o CNAE quando o CNPJ vem pontuado.
--  Medido contra ela, com a mesma função ambígua ('Porteiro'):
--    '04.542.518/0002-99'  ->  categoria: vigilancia (por CNAE 8011101)   APROVADO
--    '04542518000299'      ->  CNPJ não consultado                        BARRADO
--  E ela desiste em silêncio: nenhum erro, só a categoria indefinida no fim.
--
--  O cliente digita no WhatsApp como quiser. O banco guarda o que ele falou —
--  é a regra da casa — e a formatação acontece na saída, onde a API exige.
--
--  Idempotente.
-- =============================================================================

SET search_path TO peticoes, public;

-- 14 dígitos -> 00.000.000/0000-00 · 11 dígitos -> 000.000.000-00
-- Quantidade diferente disso volta como veio: melhor mandar o que o cliente
-- falou do que inventar máscara sobre dado incompleto.
CREATE OR REPLACE FUNCTION peticoes.fmt_documento(t text)
RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE d text := regexp_replace(COALESCE(t, ''), '[^0-9]', '', 'g');
BEGIN
    IF t IS NULL OR btrim(t) = '' THEN RETURN NULL; END IF;
    IF length(d) = 14 THEN
        RETURN substr(d,1,2)||'.'||substr(d,3,3)||'.'||substr(d,6,3)||'/'||
               substr(d,9,4)||'-'||substr(d,13,2);
    ELSIF length(d) = 11 THEN
        RETURN substr(d,1,3)||'.'||substr(d,4,3)||'.'||substr(d,7,3)||'-'||substr(d,10,2);
    END IF;
    RETURN t;
END $$;
