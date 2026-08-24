-- =============================================================================
--  Banco de dados: entrevistas trabalhistas captadas via Chatwoot
--  Destino: alimentar POST https://peticoes.nexusdevhub.com/peca/da-entrevista
--
--  Idempotente: pode ser reaplicado com segurança.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS peticoes;
SET search_path TO peticoes, public;

-- ----------------------------------------------------------------------------
-- Tipos
-- ----------------------------------------------------------------------------
DO $$ BEGIN
    CREATE TYPE peticoes.tipo_dispensa AS ENUM (
        'sem_justa_causa',
        'rescisao_indireta',
        'coacao_demissao',
        'reversao_justa_causa',
        'acordo'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE peticoes.status_entrevista AS ENUM (
        'rascunho',           -- criada, nada coletado
        'em_andamento',       -- bot coletando respostas
        'aguardando_cliente', -- pergunta enviada, sem resposta
        'aguardando_revisao', -- coletada, aguardando advogado
        'concluida',          -- liberada para gerar a peça
        'cancelada'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE peticoes.status_peticao AS ENUM (
        'pendente',    -- enfileirada
        'enviando',    -- requisição em voo
        'redigido',    -- API devolveu status=redigido
        'erro',        -- API devolveu status=erro (barrada na validação)
        'falha_http'   -- 401/422/503/timeout — não houve peça
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE peticoes.rito AS ENUM ('sumarissimo', 'ordinario');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE peticoes.tipo_resposta AS ENUM (
        'texto', 'texto_longo', 'data', 'booleano', 'numero',
        'moeda', 'escolha', 'faixa', 'anexo'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ----------------------------------------------------------------------------
-- Utilitários
-- ----------------------------------------------------------------------------

-- updated_at automático
CREATE OR REPLACE FUNCTION peticoes.tg_touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.atualizado_em := now();
    RETURN NEW;
END $$;

-- 1234.5 -> 'R$ 1.234,50'  (a API espera o salário como texto no padrão BR)
CREATE OR REPLACE FUNCTION peticoes.fmt_brl(v numeric)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN v IS NULL THEN NULL ELSE
        'R$ ' || translate(to_char(v, 'FM999,999,999,990.00'), ',.', '.,')
    END;
$$;

-- ----------------------------------------------------------------------------
-- Chatwoot: espelho mínimo do contato
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.contatos (
    id                  bigserial PRIMARY KEY,
    chatwoot_account_id bigint,
    chatwoot_contact_id bigint,
    nome                text,
    telefone            text,
    email               text,
    identifier          text,
    atributos           jsonb NOT NULL DEFAULT '{}'::jsonb,
    criado_em           timestamptz NOT NULL DEFAULT now(),
    atualizado_em       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT contatos_chatwoot_uk UNIQUE (chatwoot_account_id, chatwoot_contact_id)
);

CREATE INDEX IF NOT EXISTS contatos_telefone_ix ON peticoes.contatos (telefone);
CREATE INDEX IF NOT EXISTS contatos_email_ix    ON peticoes.contatos (lower(email));

DROP TRIGGER IF EXISTS contatos_touch ON peticoes.contatos;
CREATE TRIGGER contatos_touch BEFORE UPDATE ON peticoes.contatos
    FOR EACH ROW EXECUTE FUNCTION peticoes.tg_touch_updated_at();

-- ----------------------------------------------------------------------------
-- Entrevistas — um caso. Colunas espelham os campos do objeto `entrevista`.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.entrevistas (
    id                      bigserial PRIMARY KEY,
    -- identificação do caso na API (reenviar o mesmo `codigo` atualiza a peça)
    codigo                  text UNIQUE,
    status                  peticoes.status_entrevista NOT NULL DEFAULT 'rascunho',

    -- vínculo Chatwoot
    contato_id              bigint REFERENCES peticoes.contatos(id) ON DELETE SET NULL,
    chatwoot_account_id     bigint,
    chatwoot_conversation_id bigint,
    chatwoot_inbox_id       bigint,

    ---------------------------------------------------------------- reclamante
    recl_nome               text NOT NULL,          -- RECL_NOME (obrigatório na API)
    recl_cpf                text,                   -- RECL_CPF
    recl_rg                 text,                   -- RECL_RG
    recl_pis                text,                   -- RECL_PIS
    recl_ctps               text,                   -- RECL_CTPS
    recl_serie              text,                   -- RECL_SERIE
    recl_nasc               date,                   -- RECL_NASC
    recl_filiacao           text,                   -- RECL_FILIACAO
    recl_endereco           text,                   -- RECL_ENDERECO
    recl_cep                text,                   -- RECL_CEP
    recl_nacionalidade      text,                   -- RECL_NACIONALIDADE
    recl_estadocivil        text,                   -- RECL_ESTADOCIVIL
    email                   text,                   -- email

    ------------------------------------------------------------------ contrato
    data_admissao           date,                   -- DATA_ADMISSAO
    data_rescisao           date,                   -- DATA_RESCISAO
    funcao                  text,                   -- FUNCAO
    salario                 numeric(12,2),          -- SALARIO (enviado formatado)
    tipo_dispensa           peticoes.tipo_dispensa, -- tipo_dispensa

    ------------------------------------------------------------------- jornada
    escala                  text,                   -- escala   ex.: '12x36'
    jornada_horario         text,                   -- JORNADA_HORARIO
    tem_adic_noturno        boolean,                -- tem_adic_noturno
    finais_semana           boolean,                -- finais_semana
    intervalo_suprimido     boolean,                -- intervalo_suprimido
    intervalo_gozado        text,                   -- INTERVALO_GOZADO ex.: '15 minutos'
    media_horas_extras      text,                   -- media_horas_extras ex.: 'Até 1 hora'
    periodo_antecedente     integer,                -- periodo_antecedente (minutos)
    periodo_sucedente       integer,                -- periodo_sucedente (minutos)

    -------------------------------------------------------- folgas e benefícios
    folgas_trabalhadas      boolean,                -- folgas_trabalhadas
    ft_qtd_media            text,                   -- FT_QTD_MEDIA aceita faixa '5 a 6'
    val_ft                  text,                   -- VAL_FT aceita faixa 'R$ 180 a R$ 200'
    vale_refeicao           boolean,                -- vale_refeicao
    vale_alimentacao        boolean,                -- vale_alimentacao
    vale_transporte         boolean,                -- vale_transporte

    ---------------------------------------------------------- teses e documentos
    acumulo_funcao          boolean,                -- acumulo_funcao
    funcoes_acumuladas      text,                   -- funcoes_acumuladas
    tem_periculosidade      boolean,                -- tem_periculosidade
    tem_insalubridade       boolean,                -- tem_insalubridade
    tem_doenca              boolean,                -- tem_doenca
    gratificacao            text,                   -- gratificacao
    assiduidade             boolean,                -- assiduidade
    desconto_indevido       boolean,                -- desconto_indevido
    desconto_qual           text,                   -- desconto_qual
    holerites               boolean,                -- holerites
    espelho_ponto           boolean,                -- espelho_ponto
    fatos_narrados          text,                   -- fatos_narrados

    ------------------------------------------------- opções do pedido (raiz JSON)
    municipio               text,      -- municipio (prestação; deriva do endereço)
    redigir_ia              boolean NOT NULL DEFAULT true,
    gerar_pdf               boolean NOT NULL DEFAULT true,
    persistir               boolean NOT NULL DEFAULT true,
    consultar_cct           boolean NOT NULL DEFAULT true,
    incluir_pdf_base64      boolean NOT NULL DEFAULT false,

    -- campos que a API não conhece, mas que o escritório quer guardar
    extras                  jsonb NOT NULL DEFAULT '{}'::jsonb,
    observacoes             text,

    criado_em               timestamptz NOT NULL DEFAULT now(),
    atualizado_em           timestamptz NOT NULL DEFAULT now(),
    concluida_em            timestamptz,

    CONSTRAINT entrevistas_periodo_ck
        CHECK (data_rescisao IS NULL OR data_admissao IS NULL OR data_rescisao >= data_admissao),
    CONSTRAINT entrevistas_salario_ck CHECK (salario IS NULL OR salario >= 0),
    CONSTRAINT entrevistas_antecedente_ck
        CHECK (periodo_antecedente IS NULL OR periodo_antecedente BETWEEN 0 AND 1440),
    CONSTRAINT entrevistas_sucedente_ck
        CHECK (periodo_sucedente IS NULL OR periodo_sucedente BETWEEN 0 AND 1440)
);

-- NULLs não conflitam em índice único, então nada de índice parcial: assim o
-- ON CONFLICT (chatwoot_account_id, chatwoot_conversation_id) consegue inferi-lo.
CREATE UNIQUE INDEX IF NOT EXISTS entrevistas_conversation_uk
    ON peticoes.entrevistas (chatwoot_account_id, chatwoot_conversation_id);

CREATE INDEX IF NOT EXISTS entrevistas_status_ix ON peticoes.entrevistas (status);
CREATE INDEX IF NOT EXISTS entrevistas_contato_ix ON peticoes.entrevistas (contato_id);
CREATE INDEX IF NOT EXISTS entrevistas_criado_ix ON peticoes.entrevistas (criado_em DESC);

DROP TRIGGER IF EXISTS entrevistas_touch ON peticoes.entrevistas;
CREATE TRIGGER entrevistas_touch BEFORE UPDATE ON peticoes.entrevistas
    FOR EACH ROW EXECUTE FUNCTION peticoes.tg_touch_updated_at();

-- ----------------------------------------------------------------------------
-- Reclamadas — até 3. ordem 1 = empregadora; 2 e 3 = tomadoras (subsidiária).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.entrevista_reclamadas (
    id            bigserial PRIMARY KEY,
    entrevista_id bigint NOT NULL REFERENCES peticoes.entrevistas(id) ON DELETE CASCADE,
    ordem         smallint NOT NULL CHECK (ordem BETWEEN 1 AND 3),
    nome          text,        -- RECLn_NOME
    cnpj          text,        -- RECLn_CNPJ
    logradouro    text,        -- RECLn_LOGRADOURO (só a 1ª na API)
    endcompl      text,        -- RECLn_ENDCOMPL  cidade/UF/CEP
    criado_em     timestamptz NOT NULL DEFAULT now(),
    atualizado_em timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT entrevista_reclamadas_uk UNIQUE (entrevista_id, ordem)
);

DROP TRIGGER IF EXISTS entrevista_reclamadas_touch ON peticoes.entrevista_reclamadas;
CREATE TRIGGER entrevista_reclamadas_touch BEFORE UPDATE ON peticoes.entrevista_reclamadas
    FOR EACH ROW EXECUTE FUNCTION peticoes.tg_touch_updated_at();

-- ----------------------------------------------------------------------------
-- Catálogo de perguntas do bot (roteiro da entrevista no Chatwoot)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.perguntas (
    id             bigserial PRIMARY KEY,
    campo          text NOT NULL UNIQUE,   -- nome do campo da API (ex.: 'RECL_CPF')
    coluna         text,                   -- coluna correspondente em entrevistas
    secao          text NOT NULL,          -- reclamante | reclamadas | contrato | ...
    ordem          integer NOT NULL,
    texto          text NOT NULL,          -- a pergunta enviada ao cliente
    tipo           peticoes.tipo_resposta NOT NULL DEFAULT 'texto',
    opcoes         text[],                 -- para tipo = 'escolha'
    obrigatorio    boolean NOT NULL DEFAULT false,
    ativo          boolean NOT NULL DEFAULT true,
    efeito_ausencia text,                  -- o que acontece se o cliente não responder
    criado_em      timestamptz NOT NULL DEFAULT now(),
    atualizado_em  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS perguntas_ordem_ix ON peticoes.perguntas (ordem) WHERE ativo;

DROP TRIGGER IF EXISTS perguntas_touch ON peticoes.perguntas;
CREATE TRIGGER perguntas_touch BEFORE UPDATE ON peticoes.perguntas
    FOR EACH ROW EXECUTE FUNCTION peticoes.tg_touch_updated_at();

-- ----------------------------------------------------------------------------
-- Respostas cruas do cliente (rastro do que foi dito no chat)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.entrevista_respostas (
    id                   bigserial PRIMARY KEY,
    entrevista_id        bigint NOT NULL REFERENCES peticoes.entrevistas(id) ON DELETE CASCADE,
    pergunta_id          bigint REFERENCES peticoes.perguntas(id) ON DELETE SET NULL,
    campo                text NOT NULL,
    valor_bruto          text,          -- exatamente como o cliente escreveu
    valor_normalizado    text,          -- após parse/normalização
    chatwoot_message_id  bigint,
    recebido_em          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS entrevista_respostas_entrevista_ix
    ON peticoes.entrevista_respostas (entrevista_id, recebido_em);
-- (mensagem, campo), não só mensagem: uma frase do cliente rende vários campos
-- ("sou vigilante, ganhava 2.148, escala 12x36" = 3), todos com o mesmo
-- mensagem_id. Com a chave só na mensagem, o ON CONFLICT descartava calado
-- todos menos o primeiro — ver db/009_respostas_por_campo.sql.
CREATE UNIQUE INDEX IF NOT EXISTS entrevista_respostas_msg_campo_uk
    ON peticoes.entrevista_respostas (chatwoot_message_id, campo);

-- ----------------------------------------------------------------------------
-- Documentos anexados na conversa (holerites, espelho de ponto, CTPS...)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.entrevista_documentos (
    id                  bigserial PRIMARY KEY,
    entrevista_id       bigint NOT NULL REFERENCES peticoes.entrevistas(id) ON DELETE CASCADE,
    tipo                text,          -- 'holerite' | 'espelho_ponto' | 'ctps' | ...
    nome_arquivo        text,
    mime                text,
    url                 text,          -- URL do anexo no Chatwoot / storage
    tamanho_bytes       bigint,
    chatwoot_message_id bigint,
    criado_em           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS entrevista_documentos_entrevista_ix
    ON peticoes.entrevista_documentos (entrevista_id);

-- ----------------------------------------------------------------------------
-- Petições — resultado de cada chamada ao endpoint /peca/da-entrevista
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.peticoes (
    id                bigserial PRIMARY KEY,
    entrevista_id     bigint NOT NULL REFERENCES peticoes.entrevistas(id) ON DELETE CASCADE,
    tentativa         integer NOT NULL DEFAULT 1,
    codigo            text,                      -- resposta.codigo
    status            peticoes.status_peticao NOT NULL DEFAULT 'pendente',
    valor_causa       numeric(14,2),             -- resposta.valor_causa
    rito              peticoes.rito,             -- resposta.rito
    http_status       integer,
    erro              text,                      -- motivo da barra / mensagem de falha
    duracao_ms        integer,
    payload_enviado   jsonb,                     -- corpo exato do POST (auditoria)
    resposta          jsonb,                     -- JSON completo devolvido
    pdf_arquivo       text,                      -- caminho/URL do PDF salvo
    pdf_bytes         bytea,                     -- PDF inline (opcional)
    pdf_gerado        boolean NOT NULL DEFAULT false,
    criado_em         timestamptz NOT NULL DEFAULT now(),
    atualizado_em     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT peticoes_tentativa_uk UNIQUE (entrevista_id, tentativa)
);

CREATE INDEX IF NOT EXISTS peticoes_codigo_ix ON peticoes.peticoes (codigo);
CREATE INDEX IF NOT EXISTS peticoes_status_ix ON peticoes.peticoes (status);
CREATE INDEX IF NOT EXISTS peticoes_entrevista_ix ON peticoes.peticoes (entrevista_id, criado_em DESC);

DROP TRIGGER IF EXISTS peticoes_touch ON peticoes.peticoes;
CREATE TRIGGER peticoes_touch BEFORE UPDATE ON peticoes.peticoes
    FOR EACH ROW EXECUTE FUNCTION peticoes.tg_touch_updated_at();

-- ----------------------------------------------------------------------------
-- Verbas calculadas (resposta.verbas[])
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.peticao_verbas (
    id          bigserial PRIMARY KEY,
    peticao_id  bigint NOT NULL REFERENCES peticoes.peticoes(id) ON DELETE CASCADE,
    ordem       integer NOT NULL DEFAULT 0,
    rubrica     text NOT NULL,
    total       numeric(14,2),
    fundamento  text
);

CREATE INDEX IF NOT EXISTS peticao_verbas_peticao_ix ON peticoes.peticao_verbas (peticao_id, ordem);

-- ----------------------------------------------------------------------------
-- Campos ausentes apontados pela API (resposta.campos_ausentes[])
-- Serve de fila de re-pergunta no Chatwoot.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.peticao_campos_ausentes (
    id          bigserial PRIMARY KEY,
    peticao_id  bigint NOT NULL REFERENCES peticoes.peticoes(id) ON DELETE CASCADE,
    campo       text NOT NULL,
    efeito      text,
    reperguntado boolean NOT NULL DEFAULT false,
    CONSTRAINT peticao_campos_ausentes_uk UNIQUE (peticao_id, campo)
);

-- ----------------------------------------------------------------------------
-- Blocos/capítulos revisados — reenviados no campo `blocos` {tag: texto}
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.entrevista_blocos (
    id            bigserial PRIMARY KEY,
    entrevista_id bigint NOT NULL REFERENCES peticoes.entrevistas(id) ON DELETE CASCADE,
    tag           text NOT NULL,
    texto         text NOT NULL,
    revisado_por  text,
    criado_em     timestamptz NOT NULL DEFAULT now(),
    atualizado_em timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT entrevista_blocos_uk UNIQUE (entrevista_id, tag)
);

DROP TRIGGER IF EXISTS entrevista_blocos_touch ON peticoes.entrevista_blocos;
CREATE TRIGGER entrevista_blocos_touch BEFORE UPDATE ON peticoes.entrevista_blocos
    FOR EACH ROW EXECUTE FUNCTION peticoes.tg_touch_updated_at();

-- ----------------------------------------------------------------------------
-- Auditoria HTTP bruta (inclui 401/422/503 e timeouts)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS peticoes.api_chamadas (
    id            bigserial PRIMARY KEY,
    entrevista_id bigint REFERENCES peticoes.entrevistas(id) ON DELETE SET NULL,
    peticao_id    bigint REFERENCES peticoes.peticoes(id) ON DELETE SET NULL,
    metodo        text NOT NULL DEFAULT 'POST',
    url           text NOT NULL,
    accept        text,
    http_status   integer,
    duracao_ms    integer,
    erro          text,
    requisicao    jsonb,
    resposta      jsonb,
    criado_em     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS api_chamadas_criado_ix ON peticoes.api_chamadas (criado_em DESC);
CREATE INDEX IF NOT EXISTS api_chamadas_entrevista_ix ON peticoes.api_chamadas (entrevista_id);
