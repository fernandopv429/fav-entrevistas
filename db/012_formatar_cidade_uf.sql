-- =============================================================================
--  Cidade/UF sai com a barra que a API espera.
--
--  Por que: o cliente escreve "Itapecerica da Serra SP" e o Claude copia fiel,
--  como manda a regra. Mas a API de Petições precisa do separador para
--  reconhecer a UF — e sem isso o gate barra com COMPETENCIA_INDEFINIDA, que é
--  bloqueante (art. 651 da CLT: a competência sai do local da prestação).
--
--  Medido: 'Itapecerica da Serra SP' -> gate reprovou
--          'Itapecerica da Serra/SP' -> competência TRT-2
--
--  Mesma decisão do CPF e do CNPJ: o banco guarda o que o cliente falou, e a
--  formatação acontece na saída, onde a API exige.
--
--  Idempotente.
-- =============================================================================

SET search_path TO peticoes, public;

-- 'Itapecerica da Serra SP'          -> 'Itapecerica da Serra/SP'
-- 'Sao Paulo SP, CEP 01310-100'      -> 'Sao Paulo/SP, CEP 01310-100'
-- 'Itapecerica da Serra/SP'          -> intacto (já tem barra)
-- 'Rod Regis Bittencourt km 296,5'   -> intacto (não termina em UF)
CREATE OR REPLACE FUNCTION peticoes.fmt_cidade_uf(t text)
RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    s   text := btrim(COALESCE(t, ''));
    ufs text := 'AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|RJ|RN|RS|RO|RR|SC|SP|SE|TO';
BEGIN
    IF s = '' THEN RETURN NULL; END IF;
    -- já separado por barra ou hífen: não mexe
    IF s ~ ('[/-]\s*(' || ufs || ')(\M|$)') THEN RETURN s; END IF;

    -- UF no fim da string, precedida de espaço
    IF s ~ ('\s(' || ufs || ')$') THEN
        RETURN regexp_replace(s, '\s(' || ufs || ')$', '/\1');
    END IF;
    -- UF antes de vírgula: "Sao Paulo SP, CEP ..."
    IF s ~ ('\s(' || ufs || ')\s*,') THEN
        RETURN regexp_replace(s, '\s(' || ufs || ')\s*,', '/\1,');
    END IF;
    RETURN s;
END $$;
