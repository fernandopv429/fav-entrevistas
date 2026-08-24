-- =============================================================================
--  Funções e views: montam o payload da API e expõem o estado das entrevistas
-- =============================================================================

SET search_path TO peticoes, public;

-- ----------------------------------------------------------------------------
-- montar_entrevista(id) -> jsonb no formato do objeto `entrevista`
-- Nulos são removidos: campo ausente é omitido, e a API responde em
-- campos_ausentes o efeito de cada omissão.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION peticoes.montar_entrevista(p_entrevista_id bigint)
RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
    e   peticoes.entrevistas;
    j   jsonb;
    r   record;
BEGIN
    SELECT * INTO e FROM peticoes.entrevistas WHERE id = p_entrevista_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'entrevista % não encontrada', p_entrevista_id;
    END IF;

    -- reclamante
    j := jsonb_build_object(
        'RECL_NOME',           e.recl_nome,
        'RECL_CPF',            e.recl_cpf,
        'RECL_RG',             e.recl_rg,
        'RECL_PIS',            e.recl_pis,
        'RECL_CTPS',           e.recl_ctps,
        'RECL_SERIE',          e.recl_serie,
        'RECL_NASC',           to_char(e.recl_nasc, 'YYYY-MM-DD'),
        'RECL_FILIACAO',       e.recl_filiacao,
        'RECL_ENDERECO',       e.recl_endereco,
        'RECL_CEP',            e.recl_cep,
        'RECL_NACIONALIDADE',  e.recl_nacionalidade,
        'RECL_ESTADOCIVIL',    e.recl_estadocivil,
        'email',               e.email
    );

    -- contrato
    j := j || jsonb_build_object(
        'DATA_ADMISSAO',  to_char(e.data_admissao, 'YYYY-MM-DD'),
        'DATA_RESCISAO',  to_char(e.data_rescisao, 'YYYY-MM-DD'),
        'FUNCAO',         e.funcao,
        'SALARIO',        peticoes.fmt_brl(e.salario),
        'tipo_dispensa',  e.tipo_dispensa::text
    );

    -- jornada
    j := j || jsonb_build_object(
        'escala',               e.escala,
        'JORNADA_HORARIO',      e.jornada_horario,
        'tem_adic_noturno',     e.tem_adic_noturno,
        'finais_semana',        e.finais_semana,
        'intervalo_suprimido',  e.intervalo_suprimido,
        'INTERVALO_GOZADO',     e.intervalo_gozado,
        'media_horas_extras',   e.media_horas_extras,
        'periodo_antecedente',  e.periodo_antecedente,
        'periodo_sucedente',    e.periodo_sucedente
    );

    -- folgas e benefícios
    j := j || jsonb_build_object(
        'folgas_trabalhadas', e.folgas_trabalhadas,
        'FT_QTD_MEDIA',       e.ft_qtd_media,
        'VAL_FT',             e.val_ft,
        'vale_refeicao',      e.vale_refeicao,
        'vale_alimentacao',   e.vale_alimentacao,
        'vale_transporte',    e.vale_transporte
    );

    -- teses e documentos
    j := j || jsonb_build_object(
        'acumulo_funcao',     e.acumulo_funcao,
        'funcoes_acumuladas', e.funcoes_acumuladas,
        'tem_periculosidade', e.tem_periculosidade,
        'tem_insalubridade',  e.tem_insalubridade,
        'tem_doenca',         e.tem_doenca,
        'gratificacao',       e.gratificacao,
        'assiduidade',        e.assiduidade,
        'desconto_indevido',  e.desconto_indevido,
        'desconto_qual',      e.desconto_qual,
        'holerites',          e.holerites,
        'espelho_ponto',      e.espelho_ponto,
        'fatos_narrados',     e.fatos_narrados
    );

    -- reclamadas 1..3 -> RECLn_*
    FOR r IN
        SELECT ordem, nome, cnpj, logradouro, endcompl
        FROM peticoes.entrevista_reclamadas
        WHERE entrevista_id = p_entrevista_id
        ORDER BY ordem
    LOOP
        j := j || jsonb_build_object(
            format('RECL%s_NOME', r.ordem),       r.nome,
            format('RECL%s_CNPJ', r.ordem),       r.cnpj,
            format('RECL%s_LOGRADOURO', r.ordem), r.logradouro,
            format('RECL%s_ENDCOMPL', r.ordem),   r.endcompl
        );
    END LOOP;

    -- campos livres do escritório que a API deva receber
    IF e.extras IS NOT NULL AND e.extras <> '{}'::jsonb THEN
        j := j || e.extras;
    END IF;

    RETURN jsonb_strip_nulls(j);
END $$;

-- ----------------------------------------------------------------------------
-- montar_payload(id) -> corpo completo do POST /peca/da-entrevista
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION peticoes.montar_payload(p_entrevista_id bigint)
RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
    e      peticoes.entrevistas;
    corpo  jsonb;
    blocos jsonb;
BEGIN
    SELECT * INTO e FROM peticoes.entrevistas WHERE id = p_entrevista_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'entrevista % não encontrada', p_entrevista_id;
    END IF;

    corpo := jsonb_build_object(
        'entrevista',         peticoes.montar_entrevista(p_entrevista_id),
        'codigo',             COALESCE(e.codigo, e.id::text),
        'salario',            peticoes.fmt_brl(e.salario),
        'municipio',          e.municipio,
        'redigir_ia',         e.redigir_ia,
        'gerar_pdf',          e.gerar_pdf,
        'persistir',          e.persistir,
        'consultar_cct',      e.consultar_cct,
        'incluir_pdf_base64', e.incluir_pdf_base64
    );

    SELECT jsonb_object_agg(tag, texto) INTO blocos
    FROM peticoes.entrevista_blocos
    WHERE entrevista_id = p_entrevista_id;

    IF blocos IS NOT NULL THEN
        corpo := corpo || jsonb_build_object('blocos', blocos);
    END IF;

    RETURN jsonb_strip_nulls(corpo);
END $$;

-- ----------------------------------------------------------------------------
-- registrar_resposta(peticao_id, resposta_json) — grava o retorno da API,
-- explodindo verbas e campos_ausentes nas tabelas filhas.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION peticoes.registrar_resposta(
    p_peticao_id bigint,
    p_resposta   jsonb
) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_status text := p_resposta->>'status';
    v_rito   text := lower(translate(COALESCE(p_resposta->>'rito',''),
                                     'áâãàéêíóôõúüç', 'aaaaeeiooouuc'));
BEGIN
    UPDATE peticoes.peticoes SET
        codigo      = COALESCE(p_resposta->>'codigo', codigo),
        status      = CASE
                        WHEN v_status = 'redigido' THEN 'redigido'::peticoes.status_peticao
                        WHEN v_status = 'erro'     THEN 'erro'::peticoes.status_peticao
                        ELSE status
                      END,
        valor_causa = NULLIF(p_resposta->>'valor_causa','')::numeric,
        rito        = CASE
                        WHEN v_rito LIKE 'sumar%'  THEN 'sumarissimo'::peticoes.rito
                        WHEN v_rito LIKE 'ordin%'  THEN 'ordinario'::peticoes.rito
                        ELSE NULL
                      END,
        resposta    = p_resposta
    WHERE id = p_peticao_id;

    DELETE FROM peticoes.peticao_verbas WHERE peticao_id = p_peticao_id;
    INSERT INTO peticoes.peticao_verbas (peticao_id, ordem, rubrica, total, fundamento)
    SELECT p_peticao_id, i.ord, v->>'rubrica',
           NULLIF(v->>'total','')::numeric, v->>'fundamento'
    FROM jsonb_array_elements(COALESCE(p_resposta->'verbas','[]'::jsonb))
         WITH ORDINALITY AS i(v, ord);

    DELETE FROM peticoes.peticao_campos_ausentes WHERE peticao_id = p_peticao_id;
    INSERT INTO peticoes.peticao_campos_ausentes (peticao_id, campo, efeito)
    SELECT p_peticao_id, c->>'campo', c->>'efeito'
    FROM jsonb_array_elements(COALESCE(p_resposta->'campos_ausentes','[]'::jsonb)) AS c
    ON CONFLICT (peticao_id, campo) DO UPDATE SET efeito = EXCLUDED.efeito;
END $$;

-- ----------------------------------------------------------------------------
-- Views operacionais
-- ----------------------------------------------------------------------------

-- Fila: entrevistas liberadas e ainda sem peça aprovada
CREATE OR REPLACE VIEW peticoes.vw_fila_geracao AS
SELECT e.id,
       e.codigo,
       e.recl_nome,
       e.status,
       e.concluida_em,
       (SELECT count(*) FROM peticoes.peticoes p WHERE p.entrevista_id = e.id) AS tentativas,
       peticoes.montar_payload(e.id) AS payload
FROM peticoes.entrevistas e
WHERE e.status = 'concluida'
  AND NOT EXISTS (
        SELECT 1 FROM peticoes.peticoes p
        WHERE p.entrevista_id = e.id AND p.status = 'redigido'
  )
ORDER BY e.concluida_em NULLS LAST, e.id;

-- Progresso do preenchimento de cada entrevista
CREATE OR REPLACE VIEW peticoes.vw_entrevistas_progresso AS
SELECT e.id,
       e.codigo,
       e.recl_nome,
       e.status,
       e.chatwoot_conversation_id,
       (SELECT count(*) FROM peticoes.entrevista_respostas r WHERE r.entrevista_id = e.id) AS respostas,
       (SELECT count(*) FROM peticoes.entrevista_reclamadas x WHERE x.entrevista_id = e.id) AS reclamadas,
       (SELECT count(*) FROM peticoes.entrevista_documentos d WHERE d.entrevista_id = e.id) AS documentos,
       (SELECT count(*) FROM jsonb_object_keys(peticoes.montar_entrevista(e.id)))
           AS campos_preenchidos,
       (SELECT count(*) FROM peticoes.perguntas q WHERE q.ativo) AS campos_no_roteiro,
       e.criado_em,
       e.atualizado_em
FROM peticoes.entrevistas e;

-- Última peça de cada entrevista, com totais
CREATE OR REPLACE VIEW peticoes.vw_peticoes_ultimas AS
SELECT DISTINCT ON (p.entrevista_id)
       p.id            AS peticao_id,
       p.entrevista_id,
       e.recl_nome,
       p.codigo,
       p.status,
       p.rito,
       p.valor_causa,
       p.pdf_gerado,
       p.http_status,
       p.erro,
       (SELECT count(*) FROM peticoes.peticao_verbas v WHERE v.peticao_id = p.id) AS qtd_verbas,
       (SELECT count(*) FROM peticoes.peticao_campos_ausentes c WHERE c.peticao_id = p.id) AS qtd_ausentes,
       p.criado_em
FROM peticoes.peticoes p
JOIN peticoes.entrevistas e ON e.id = p.entrevista_id
ORDER BY p.entrevista_id, p.tentativa DESC;

-- O que ainda falta perguntar (junta o catálogo com o que a API reclamou)
CREATE OR REPLACE VIEW peticoes.vw_campos_a_reperguntar AS
SELECT e.id AS entrevista_id,
       e.chatwoot_conversation_id,
       c.campo,
       c.efeito,
       q.texto AS pergunta,
       q.tipo,
       q.opcoes
FROM peticoes.peticao_campos_ausentes c
JOIN peticoes.peticoes p   ON p.id = c.peticao_id
JOIN peticoes.entrevistas e ON e.id = p.entrevista_id
LEFT JOIN peticoes.perguntas q ON q.campo = c.campo
WHERE NOT c.reperguntado;
