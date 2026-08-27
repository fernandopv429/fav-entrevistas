-- =============================================================================
--  Fecha o schema contra a documentação da API 0.8.2.
--
--  O que faltava, campo a campo:
--    ft_pagamento, VALOR_AUX_ALIMENTACAO, VAL_CONDUCAO, gratificacao_qual,
--    assiduidade_prometido, assiduidade_pago  -> não existiam
--    gratificacao                             -> estava text; a doc diz bool,
--                                                e o texto é gratificacao_qual
--    tipo_dispensa                            -> faltava nulidade_pedido_demissao
--    consultar_cnpj                           -> opção do POST que não existia
--    RECL2_LOGRADOURO / RECL3_LOGRADOURO      -> coluna existia, roteiro não
--
--  Idempotente: pode ser reaplicado.
-- =============================================================================

SET search_path TO peticoes, public;

-- ----------------------------------------------------------------------------
-- tipo_dispensa: a doc aceita nulidade_pedido_demissao COMO SINÔNIMO de
-- coacao_demissao (mesma modalidade: pedido de demissão viciado). Guardamos os
-- dois porque o agente pode devolver qualquer um dos rótulos.
-- ----------------------------------------------------------------------------
ALTER TYPE peticoes.tipo_dispensa ADD VALUE IF NOT EXISTS 'nulidade_pedido_demissao';

-- ----------------------------------------------------------------------------
-- Colunas novas
-- ----------------------------------------------------------------------------
ALTER TABLE peticoes.entrevistas
    -- folgas trabalhadas
    ADD COLUMN IF NOT EXISTS ft_pagamento          text,  -- ft_pagamento ('PIX', 'dinheiro')
    -- benefícios: valores. Texto porque a doc os trata como texto e porque
    -- 'R$ 25,00' e faixas chegam do chat como escritos.
    ADD COLUMN IF NOT EXISTS valor_aux_alimentacao text,  -- VALOR_AUX_ALIMENTACAO
    ADD COLUMN IF NOT EXISTS val_conducao          text,  -- VAL_CONDUCAO
    -- teses
    ADD COLUMN IF NOT EXISTS gratificacao_qual     text,  -- gratificacao_qual
    ADD COLUMN IF NOT EXISTS assiduidade_prometido text,  -- assiduidade_prometido
    ADD COLUMN IF NOT EXISTS assiduidade_pago      text,  -- assiduidade_pago
    -- opção do POST
    ADD COLUMN IF NOT EXISTS consultar_cnpj boolean NOT NULL DEFAULT true;

-- ----------------------------------------------------------------------------
-- gratificacao: text -> boolean, preservando o texto em gratificacao_qual.
-- A doc separa: gratificacao é bool (tem ou não), gratificacao_qual é o texto
-- que desambigua gratificação de função × prêmio de assiduidade — e é de onde
-- o motor extrai prometido/pago quando vêm na frase.
-- ----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'peticoes' AND table_name = 'entrevistas'
          AND column_name = 'gratificacao' AND data_type = 'text'
    ) THEN
        -- não perde o que já foi coletado: o texto migra para _qual
        UPDATE peticoes.entrevistas
           SET gratificacao_qual = COALESCE(gratificacao_qual, gratificacao)
         WHERE gratificacao IS NOT NULL
           AND lower(btrim(gratificacao)) NOT IN ('sim','não','nao','true','false','s','n');

        ALTER TABLE peticoes.entrevistas
            ALTER COLUMN gratificacao TYPE boolean
            USING CASE
                WHEN gratificacao IS NULL THEN NULL
                WHEN lower(btrim(gratificacao)) IN ('não','nao','false','n','0','') THEN false
                ELSE true
            END;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- montar_entrevista: agora com os 63 campos da documentação.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION peticoes.montar_entrevista(p_entrevista_id bigint)
RETURNS jsonb
LANGUAGE plpgsql STABLE AS $$
DECLARE
    e peticoes.entrevistas;
    j jsonb;
    r record;
BEGIN
    SELECT * INTO e FROM peticoes.entrevistas WHERE id = p_entrevista_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'entrevista % não encontrada', p_entrevista_id;
    END IF;

    -- reclamante
    j := jsonb_build_object(
        'RECL_NOME',          e.recl_nome,
        'RECL_CPF',           peticoes.fmt_documento(e.recl_cpf),
        'RECL_RG',            e.recl_rg,
        'RECL_PIS',           e.recl_pis,
        'RECL_CTPS',          e.recl_ctps,
        'RECL_SERIE',         e.recl_serie,
        'RECL_NASC',          to_char(e.recl_nasc, 'YYYY-MM-DD'),
        'RECL_FILIACAO',      e.recl_filiacao,
        'RECL_ENDERECO',      e.recl_endereco,
        'RECL_CEP',           e.recl_cep,
        'RECL_NACIONALIDADE', e.recl_nacionalidade,
        'RECL_ESTADOCIVIL',   e.recl_estadocivil,
        'email',              e.email
    );

    -- contrato
    j := j || jsonb_build_object(
        'DATA_ADMISSAO', to_char(e.data_admissao, 'YYYY-MM-DD'),
        'DATA_RESCISAO', to_char(e.data_rescisao, 'YYYY-MM-DD'),
        'FUNCAO',        e.funcao,
        'SALARIO',       peticoes.fmt_brl(e.salario),
        'tipo_dispensa', e.tipo_dispensa::text
    );

    -- jornada. periodo_antecedente/sucedente são minutos no banco (dá para
    -- validar e somar), mas a doc os descreve como texto — '30 minutos' — então
    -- a unidade viaja junto, em vez de um 30 solto que o motor teria de adivinhar.
    j := j || jsonb_build_object(
        'escala',              e.escala,
        'JORNADA_HORARIO',     e.jornada_horario,
        'tem_adic_noturno',    e.tem_adic_noturno,
        'finais_semana',       e.finais_semana,
        'intervalo_suprimido', e.intervalo_suprimido,
        'INTERVALO_GOZADO',    e.intervalo_gozado,
        'media_horas_extras',  e.media_horas_extras,
        'periodo_antecedente', e.periodo_antecedente || ' minutos',
        'periodo_sucedente',   e.periodo_sucedente   || ' minutos'
    );

    -- folgas trabalhadas e benefícios
    j := j || jsonb_build_object(
        'folgas_trabalhadas',    e.folgas_trabalhadas,
        'FT_QTD_MEDIA',          e.ft_qtd_media,
        'VAL_FT',                e.val_ft,
        'ft_pagamento',          e.ft_pagamento,
        'vale_refeicao',         e.vale_refeicao,
        'vale_alimentacao',      e.vale_alimentacao,
        'vale_transporte',       e.vale_transporte,
        'VALOR_AUX_ALIMENTACAO', e.valor_aux_alimentacao,
        'VAL_CONDUCAO',          e.val_conducao
    );

    -- teses e documentos
    j := j || jsonb_build_object(
        'acumulo_funcao',        e.acumulo_funcao,
        'funcoes_acumuladas',    e.funcoes_acumuladas,
        'gratificacao',          e.gratificacao,
        'gratificacao_qual',     e.gratificacao_qual,
        'assiduidade',           e.assiduidade,
        'assiduidade_prometido', e.assiduidade_prometido,
        'assiduidade_pago',      e.assiduidade_pago,
        'tem_periculosidade',    e.tem_periculosidade,
        'tem_insalubridade',     e.tem_insalubridade,
        'tem_doenca',            e.tem_doenca,
        'desconto_indevido',     e.desconto_indevido,
        'desconto_qual',         e.desconto_qual,
        'holerites',             e.holerites,
        'espelho_ponto',         e.espelho_ponto,
        'fatos_narrados',        e.fatos_narrados
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
            format('RECL%s_CNPJ', r.ordem),       peticoes.fmt_documento(r.cnpj),
            format('RECL%s_LOGRADOURO', r.ordem), r.logradouro,
            format('RECL%s_ENDCOMPL', r.ordem),   peticoes.fmt_cidade_uf(r.endcompl)
        );
    END LOOP;

    -- campos que o motor hoje ignora (armamento_colete, epi, produtos...).
    -- A doc garante que enviar não causa erro, e alguns são candidatos a entrar.
    IF e.extras IS NOT NULL AND e.extras <> '{}'::jsonb THEN
        j := j || e.extras;
    END IF;

    RETURN jsonb_strip_nulls(j);
END $$;

-- ----------------------------------------------------------------------------
-- montar_payload: acrescenta consultar_cnpj (decide SEEVISSP × SINDEEPRES ×
-- SIEMACO pelo CNAE; desligado, a categoria cai para a função, menos confiável).
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
        'municipio',          peticoes.fmt_cidade_uf(e.municipio),
        'redigir_ia',         e.redigir_ia,
        'gerar_pdf',          e.gerar_pdf,
        'persistir',          e.persistir,
        'consultar_cct',      e.consultar_cct,
        'consultar_cnpj',     e.consultar_cnpj,
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
-- Roteiro: os campos que faltavam.
--
-- Convenção nova: coluna = 'extras.<chave>' grava no jsonb `extras`, que
-- montar_entrevista funde no payload. É por onde entram os campos que o motor
-- hoje ignora — coletar agora custa uma pergunta e evita reentrevistar o
-- cliente quando armamento_colete/produtos/epi passarem a contar.
-- ----------------------------------------------------------------------------
INSERT INTO peticoes.perguntas
    (campo, coluna, secao, ordem, texto, tipo, opcoes, obrigatorio, efeito_ausencia)
VALUES
-- ------------------------------------------------------------- reclamadas ---
('RECL2_LOGRADOURO',NULL,'reclamadas',255,
 'Qual o endereço do local onde você prestava serviço (rua e número)?','texto',NULL,false,
 'endereço da tomadora incompleto'),
('RECL3_LOGRADOURO',NULL,'reclamadas',285,
 'Endereço da terceira empresa (rua e número)?','texto',NULL,false,
 'endereço da terceira reclamada incompleto'),

-- ---------------------------------------------------------------- contrato ---
-- reescrita só para incluir nulidade_pedido_demissao nas opções
('tipo_dispensa','tipo_dispensa','contrato',340,
 'Como terminou o contrato?','escolha',
 ARRAY['sem_justa_causa','rescisao_indireta','coacao_demissao',
       'nulidade_pedido_demissao','reversao_justa_causa','acordo'],false,
 'assume dispensa sem justa causa'),

-- ----------------------------------------------------- folgas e benefícios ---
('ft_pagamento','ft_pagamento','folgas',525,
 'Como você recebia por essas folgas? (PIX, dinheiro, depósito)','texto',NULL,false,
 'capítulo das folgas fica com narrativa genérica'),
('VALOR_AUX_ALIMENTACAO','valor_aux_alimentacao','folgas',560,
 'Qual o valor diário do vale/auxílio-alimentação?','texto',NULL,false,
 'o motor lê o valor da cláusula do tíquete-refeição na CCT'),
('VAL_CONDUCAO','val_conducao','folgas',570,
 'Quanto gastava de condução por dia?','texto',NULL,false,
 'o pedido de vale-transporte nas folgas não sai: a CCT obriga o benefício mas não declara valor'),

-- ------------------------------------------------------------------- teses ---
-- reescrita: a doc separa o bool (gratificacao) do texto (gratificacao_qual)
('gratificacao','gratificacao','teses',650,
 'Recebia gratificação de função ou algum prêmio?','booleano',NULL,false,
 'gratificação não integra a base de cálculo'),
('gratificacao_qual','gratificacao_qual','teses',655,
 'Qual gratificação ou prêmio era, e de quanto?','texto',NULL,false,
 'o motor pode classificar a verba errada: gratificação de função e prêmio de assiduidade têm cálculos diferentes'),
('assiduidade_prometido','assiduidade_prometido','teses',662,
 'Quanto foi prometido de prêmio de assiduidade?','texto',NULL,false,
 'a verba não é calculada'),
('assiduidade_pago','assiduidade_pago','teses',664,
 'E quanto era efetivamente pago?','texto',NULL,false,
 'a verba não é calculada: o pedido é a diferença (art. 457, § 1º)'),

-- ------------------------------ coletados hoje, ainda ignorados pelo motor ---
('ferias','extras.ferias','extras',900,
 'Você tirou suas férias durante o contrato?','booleano',NULL,false,
 'hoje não afeta a peça'),
('ferias_quantidade','extras.ferias_quantidade','extras',910,
 'Quantos períodos de férias ficaram sem tirar?','texto',NULL,false,
 'hoje não afeta a peça; distingue férias vencidas de proporcionais'),
('armamento_colete','extras.armamento_colete','extras',920,
 'Usava arma de fogo ou colete balístico no trabalho?','booleano',NULL,false,
 'hoje não afeta a peça; reforçaria a periculosidade do vigilante'),
('produtos','extras.produtos','extras',930,
 'Manuseava produtos químicos? Quais?','texto',NULL,false,
 'hoje não afeta a peça; sustentaria a insalubridade'),
('epi','extras.epi','extras',940,
 'A empresa fornecia EPI (luva, máscara, protetor auricular)?','booleano',NULL,false,
 'hoje não afeta a peça; sustentaria a insalubridade'),
('testemunha','extras.testemunha','extras',950,
 'Tem colegas que possam testemunhar?','texto',NULL,false,
 'hoje não afeta a peça'),
('ft_comprovante','extras.ft_comprovante','extras',960,
 'Tem comprovante dos pagamentos por folga (print do PIX, extrato)?','booleano',NULL,false,
 'hoje não afeta a peça'),
('horas_extras','extras.horas_extras','extras',970,
 'Fazia horas extras?','booleano',NULL,false,
 'hoje não afeta a peça: quem decide a rubrica é media_horas_extras'),
('rescisao_contratual','extras.rescisao_contratual','extras',980,
 'Recebeu o termo de rescisão (TRCT)?','booleano',NULL,false,
 'hoje não afeta a peça'),
('telefone','extras.telefone','extras',990,
 'Qual o seu telefone de contato?','texto',NULL,false,
 'hoje não afeta a peça; o contato já vem do Chatwoot'),
-- estes dois não se perguntam ao cliente: vêm do formulário. Ficam no roteiro
-- só para serem aceitos sem cair em nao_mapeados.
('modelo_peticao','extras.modelo_peticao','extras',1000,
 '(não perguntar — vem do formulário)','texto',NULL,false,'hoje não afeta a peça'),
('titulo','extras.titulo','extras',1010,
 '(não perguntar — vem do formulário)','texto',NULL,false,'hoje não afeta a peça')

ON CONFLICT (campo) DO UPDATE SET
    coluna          = EXCLUDED.coluna,
    secao           = EXCLUDED.secao,
    ordem           = EXCLUDED.ordem,
    texto           = EXCLUDED.texto,
    tipo            = EXCLUDED.tipo,
    opcoes          = EXCLUDED.opcoes,
    obrigatorio     = EXCLUDED.obrigatorio,
    efeito_ausencia = EXCLUDED.efeito_ausencia,
    ativo           = true;
