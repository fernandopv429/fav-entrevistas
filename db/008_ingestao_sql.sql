-- =============================================================================
--  Ingestão direta por SQL: peticoes.ingerir(jsonb)
--
--  Por que existe: o nó Postgres do n8n fala SQL, não HTTP. Esta função faz, em
--  uma chamada, o que POST /entrevistas faz — acha ou cria o caso, coage os
--  valores, distribui pelas colunas/reclamadas/extras e devolve o estado.
--
--  O mapa campo->coluna continua saindo de peticoes.perguntas: campo novo é um
--  INSERT no roteiro e passa a funcionar aqui também, sem tocar nesta função.
--
--  Idempotente.
-- =============================================================================

SET search_path TO peticoes, public;

-- ----------------------------------------------------------------------------
-- Coerção. O cliente escreve como fala; quem normaliza é o banco.
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION peticoes.coagir_booleano(t text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE lower(btrim(translate(COALESCE(t,''), 'ãáâàéêíóôõúüç', 'aaaaeeiooouuc')))
        WHEN 'sim' THEN true  WHEN 's' THEN true  WHEN 'true' THEN true
        WHEN '1'   THEN true  WHEN 'verdadeiro' THEN true WHEN 'ok' THEN true
        WHEN 'tenho' THEN true WHEN 'tinha' THEN true WHEN 'recebia' THEN true
        WHEN 'nao' THEN false WHEN 'n' THEN false WHEN 'false' THEN false
        WHEN '0'   THEN false WHEN 'falso' THEN false
        WHEN 'nunca' THEN false WHEN 'nenhum' THEN false
        ELSE NULL
    END;
$$;

-- Aceita ISO, DD/MM/AAAA e DD-MM-AAAA. Formato desconhecido vira NULL: data
-- errada é pior que data ausente — a peça sai com avos e FGTS de um período
-- que não existiu, e ninguém percebe.
CREATE OR REPLACE FUNCTION peticoes.coagir_data(t text)
RETURNS date LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE s text := btrim(COALESCE(t, ''));
BEGIN
    IF s = '' THEN RETURN NULL; END IF;
    IF s ~ '^\d{4}-\d{2}-\d{2}' THEN RETURN substring(s from 1 for 10)::date; END IF;
    IF s ~ '^\d{1,2}[/-]\d{1,2}[/-]\d{4}$' THEN
        RETURN to_date(translate(s, '-', '/'), 'DD/MM/YYYY');
    END IF;
    RETURN NULL;
EXCEPTION WHEN others THEN RETURN NULL;
END $$;

-- 'R$ 2.148,22' -> 2148.22 · '2148.22' -> 2148.22 · '1.800' -> 1800 · '30 minutos' -> 30
--
-- O ponto é ambíguo entre as duas convenções, e errar aqui é caro: o cliente
-- escreveu '1.800' corrigindo o salário e virou R$ 1,80 — a petição inteira
-- escala a partir do salário. A desambiguação é pela contagem de dígitos:
--   vírgula presente  -> ela é o decimal, ponto é milhar   ('2.148,22' -> 2148.22)
--   só ponto, 3 dígitos depois do último -> milhar          ('1.800'    -> 1800)
--   só ponto, 1 ou 2 dígitos depois      -> decimal         ('2148.22'  -> 2148.22)
CREATE OR REPLACE FUNCTION peticoes.coagir_numero(t text)
RETURNS numeric LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    s     text := regexp_replace(COALESCE(t, ''), '[^0-9,.]', '', 'g');
    cauda text;
BEGIN
    IF s = '' THEN RETURN NULL; END IF;

    IF position(',' in s) > 0 THEN
        s := replace(replace(s, '.', ''), ',', '.');
    ELSIF position('.' in s) > 0 THEN
        cauda := split_part(s, '.', array_length(string_to_array(s, '.'), 1));
        -- 3 dígitos depois do último ponto e algo antes: é separador de milhar
        IF length(cauda) = 3 AND left(s, 1) <> '.' THEN
            s := replace(s, '.', '');
        END IF;
    END IF;

    RETURN s::numeric;
EXCEPTION WHEN others THEN RETURN NULL;
END $$;

-- ----------------------------------------------------------------------------
-- ingerir(payload) -> estado da entrevista
--
-- Payload aceito (mesmo do POST /entrevistas):
--   {
--     "chatwoot_account_id": 1, "chatwoot_conversation_id": 81,
--     "chatwoot_inbox_id": 7, "mensagem_id": 123, "municipio": "...",
--     "contato": {"chatwoot_contact_id":1,"nome":"...","telefone":"..."},
--     "campos":  {"RECL_NOME":"...", "SALARIO":"R$ 2.148,22", ...}
--   }
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION peticoes.ingerir(p jsonb)
RETURNS jsonb
LANGUAGE plpgsql AS $$
DECLARE
    v_conta      bigint := NULLIF(p->>'chatwoot_account_id','')::bigint;
    v_conversa   bigint := NULLIF(p->>'chatwoot_conversation_id','')::bigint;
    v_inbox      bigint := NULLIF(p->>'chatwoot_inbox_id','')::bigint;
    v_msg        bigint := NULLIF(p->>'mensagem_id','')::bigint;
    v_contato    jsonb  := COALESCE(p->'contato', '{}'::jsonb);
    v_campos     jsonb  := COALESCE(p->'campos', p->'entrevista', '{}'::jsonb);
    v_contato_id bigint;
    v_eid        bigint;
    v_criada     boolean := false;
    v_nome       text;
    v_colunas    jsonb := '{}'::jsonb;   -- coluna -> valor já coagido
    v_extras     jsonb := '{}'::jsonb;
    v_naomap     jsonb := '{}'::jsonb;
    v_reclamadas jsonb := '{}'::jsonb;   -- ordem -> {atributo: valor}
    v_aceitos    text[] := '{}';
    r            record;
    chave        text;
    valor        text;
    v_coluna     text;
    v_tipo       text;
    v_ordem      text;
    v_atrib      text;
    v_lista      text;
BEGIN
    -- multi-conta: o display_id da conversa é sequencial POR CONTA, então
    -- conversa sem conta pode apontar para o caso de outro cliente.
    IF v_conversa IS NOT NULL AND v_conta IS NULL THEN
        RAISE EXCEPTION 'informe chatwoot_account_id junto com chatwoot_conversation_id';
    END IF;

    ----------------------------------------------------------------- contato
    IF (v_contato->>'chatwoot_contact_id') IS NOT NULL AND v_conta IS NOT NULL THEN
        INSERT INTO peticoes.contatos
            (chatwoot_account_id, chatwoot_contact_id, nome, telefone, email)
        VALUES (v_conta, (v_contato->>'chatwoot_contact_id')::bigint,
                v_contato->>'nome', v_contato->>'telefone', v_contato->>'email')
        ON CONFLICT (chatwoot_account_id, chatwoot_contact_id) DO UPDATE SET
            nome     = COALESCE(EXCLUDED.nome,     peticoes.contatos.nome),
            telefone = COALESCE(EXCLUDED.telefone, peticoes.contatos.telefone),
            email    = COALESCE(EXCLUDED.email,    peticoes.contatos.email)
        RETURNING id INTO v_contato_id;
    END IF;

    -------------------------------------------------------------- entrevista
    -- 1) pela conversa
    IF v_conversa IS NOT NULL THEN
        SELECT id INTO v_eid FROM peticoes.entrevistas
         WHERE chatwoot_account_id = v_conta AND chatwoot_conversation_id = v_conversa;
    END IF;
    -- 2) pelo contato, se houver caso ainda aberto (a conversa muda, o caso não)
    IF v_eid IS NULL AND v_contato_id IS NOT NULL THEN
        SELECT id INTO v_eid FROM peticoes.entrevistas
         WHERE contato_id = v_contato_id
           AND status NOT IN ('concluida','cancelada')
         ORDER BY id DESC LIMIT 1;
    END IF;
    -- 3) cria — mas só se a mensagem trouxe conteúdo de entrevista.
    --
    -- Sem esta porta, qualquer conversa da caixa vira um caso: uma notificação
    -- técnica ("Connection successfully established", de um contato chamado
    -- EvolutionAPI) abria entrevista trabalhista com o nome do contato e mais
    -- nada. `v_campos` é o que a IA extraiu — vazio significa que não havia
    -- nada de entrevista na mensagem.
    --
    -- Lead de verdade não se perde: a primeira mensagem ("oi, fui demitido")
    -- não abre caso, mas também não tinha campo nenhum para gravar; a seguinte,
    -- que já traz nome ou função, abre.
    IF v_eid IS NULL AND v_campos = '{}'::jsonb THEN
        RETURN jsonb_build_object(
            'ok', false,
            'motivo', 'sem_conteudo',
            'detalhe', 'a mensagem não trouxe nenhum campo de entrevista; '
                       'caso não foi criado');
    END IF;

    IF v_eid IS NULL THEN
        -- O nome do contato do Chatwoot é o ÚLTIMO recurso, e só aqui: o caso
        -- precisa de um nome para existir e começar a acumular respostas. Quem
        -- ingere não deve mandar esse fallback junto dos campos — mandado a cada
        -- mensagem, ele sobrescreve o nome verdadeiro que a IA já extraiu.
        v_nome := btrim(COALESCE(v_campos->>'RECL_NOME', v_campos->>'recl_nome',
                                 v_contato->>'nome', ''));
        IF v_nome = '' THEN
            RETURN jsonb_build_object(
                'ok', false,
                'erro', 'para criar uma entrevista é preciso RECL_NOME; '
                        'depois disso os outros campos podem vir aos poucos');
        END IF;
        INSERT INTO peticoes.entrevistas
            (recl_nome, contato_id, chatwoot_account_id, chatwoot_conversation_id,
             chatwoot_inbox_id, status)
        VALUES (v_nome, v_contato_id, v_conta, v_conversa, v_inbox, 'em_andamento')
        RETURNING id INTO v_eid;
        v_criada := true;
    END IF;

    ------------------------------------------------------ distribuir os campos
    FOR chave, valor IN
        SELECT k, CASE jsonb_typeof(v) WHEN 'string' THEN v #>> '{}' ELSE v::text END
        FROM jsonb_each(v_campos) AS e(k, v)
    LOOP
        IF valor IS NULL OR btrim(valor) = '' OR valor = 'null' THEN CONTINUE; END IF;

        -- RECLn_ATRIBUTO -> tabela filha das reclamadas
        IF chave ~* '^RECL[123]_(NOME|CNPJ|LOGRADOURO|ENDCOMPL)$' THEN
            v_ordem := substring(chave from 5 for 1);
            v_atrib := lower(split_part(chave, '_', 2));
            v_reclamadas := jsonb_set(v_reclamadas, ARRAY[v_ordem],
                COALESCE(v_reclamadas->v_ordem, '{}'::jsonb)
                || jsonb_build_object(v_atrib, btrim(valor)), true);
            v_aceitos := v_aceitos || chave;
            CONTINUE;
        END IF;

        SELECT q.coluna INTO v_coluna FROM peticoes.perguntas q
         WHERE q.ativo AND q.campo = chave LIMIT 1;

        IF v_coluna IS NULL THEN
            v_naomap := v_naomap || jsonb_build_object(chave, valor);
            CONTINUE;
        END IF;

        -- coluna = 'extras.<chave>' vai para o jsonb, não vira coluna
        IF v_coluna LIKE 'extras.%' THEN
            v_extras := v_extras || jsonb_build_object(
                split_part(v_coluna, '.', 2),
                COALESCE(to_jsonb(peticoes.coagir_booleano(valor)), to_jsonb(btrim(valor))));
            v_aceitos := v_aceitos || chave;
            CONTINUE;
        END IF;

        -- o tipo da coluna decide a coerção: é ele que manda, não o roteiro
        SELECT c.data_type INTO v_tipo FROM information_schema.columns c
         WHERE c.table_schema='peticoes' AND c.table_name='entrevistas'
           AND c.column_name = v_coluna;

        v_colunas := v_colunas || jsonb_build_object(v_coluna,
            CASE
                WHEN v_tipo = 'boolean' THEN to_jsonb(peticoes.coagir_booleano(valor))
                WHEN v_tipo = 'date'    THEN to_jsonb(peticoes.coagir_data(valor))
                WHEN v_tipo IN ('numeric','integer','bigint','smallint')
                                        THEN to_jsonb(peticoes.coagir_numero(valor))
                WHEN v_tipo = 'USER-DEFINED' THEN to_jsonb(btrim(valor))  -- enums
                ELSE to_jsonb(btrim(valor))
            END);
        v_aceitos := v_aceitos || chave;
    END LOOP;

    -- valor que não coube na coerção vira NULL: não grava lixo, mas registra
    v_colunas := (SELECT COALESCE(jsonb_object_agg(k, v), '{}'::jsonb)
                  FROM jsonb_each(v_colunas) AS x(k, v) WHERE jsonb_typeof(v) <> 'null');

    -- Preenche conta/conversa/inbox em caso que nasceu sem elas. Só a criação
    -- gravava essas colunas, então entrevista aberta antes de o id da conversa
    -- chegar ficava para sempre sem vínculo — e é essa coluna que o índice único
    -- usa para achar o caso pela conversa nas mensagens seguintes.
    IF v_conta IS NOT NULL THEN
        UPDATE peticoes.entrevistas
           SET chatwoot_account_id      = COALESCE(chatwoot_account_id, v_conta),
               chatwoot_conversation_id = COALESCE(chatwoot_conversation_id, v_conversa),
               chatwoot_inbox_id        = COALESCE(chatwoot_inbox_id, v_inbox)
         WHERE id = v_eid
           AND (chatwoot_account_id IS NULL OR chatwoot_conversation_id IS NULL
                OR chatwoot_inbox_id IS NULL)
           -- não rouba a conversa de outro caso
           AND NOT EXISTS (
                 SELECT 1 FROM peticoes.entrevistas o
                  WHERE o.id <> v_eid
                    AND o.chatwoot_account_id = v_conta
                    AND o.chatwoot_conversation_id = v_conversa);
    END IF;

    IF p ? 'municipio' AND btrim(COALESCE(p->>'municipio','')) <> '' THEN
        v_colunas := v_colunas || jsonb_build_object('municipio', p->>'municipio');
    END IF;

    -- Quem ingere pode declarar o estado do caso. É o que permite a entrevista
    -- marcada como completa entrar direto na fila de geração da peça
    -- (vw_fila_geracao só olha status = 'concluida').
    IF p->>'status' IN ('rascunho','em_andamento','aguardando_cliente',
                        'aguardando_revisao','concluida','cancelada') THEN
        v_colunas := v_colunas || jsonb_build_object('status', p->>'status');
    END IF;
    IF v_contato_id IS NOT NULL THEN
        v_colunas := v_colunas || jsonb_build_object('contato_id', v_contato_id);
    END IF;

    ------------------------------------------------------------------ gravar
    -- jsonb_populate_record aplica a função de entrada de cada coluna, então o
    -- casting sai de graça e na hora certa.
    IF v_colunas <> '{}'::jsonb THEN
        SELECT string_agg(quote_ident(k), ', ') INTO v_lista
          FROM jsonb_object_keys(v_colunas) AS k;
        EXECUTE format(
            'UPDATE peticoes.entrevistas SET (%s) = '
            '(SELECT %s FROM jsonb_populate_record(NULL::peticoes.entrevistas, $1)) '
            'WHERE id = $2', v_lista, v_lista)
        USING v_colunas, v_eid;

        IF NOT (v_colunas ? 'status') THEN
            UPDATE peticoes.entrevistas
               SET status = CASE WHEN status IN ('rascunho','aguardando_cliente')
                                 THEN 'em_andamento'::peticoes.status_entrevista
                                 ELSE status END
             WHERE id = v_eid;
        END IF;
        IF (v_colunas->>'status') = 'concluida' THEN
            UPDATE peticoes.entrevistas SET concluida_em = COALESCE(concluida_em, now())
             WHERE id = v_eid;
        END IF;
    END IF;

    IF v_extras <> '{}'::jsonb THEN
        UPDATE peticoes.entrevistas SET extras = extras || v_extras WHERE id = v_eid;
    END IF;

    IF v_naomap <> '{}'::jsonb THEN
        UPDATE peticoes.entrevistas SET nao_mapeados = nao_mapeados || v_naomap
         WHERE id = v_eid;
    END IF;

    FOR r IN SELECT key::smallint AS ordem, value AS atrib
               FROM jsonb_each(v_reclamadas) ORDER BY 1
    LOOP
        INSERT INTO peticoes.entrevista_reclamadas
            (entrevista_id, ordem, nome, cnpj, logradouro, endcompl)
        VALUES (v_eid, r.ordem, r.atrib->>'nome', r.atrib->>'cnpj',
                r.atrib->>'logradouro', r.atrib->>'endcompl')
        ON CONFLICT (entrevista_id, ordem) DO UPDATE SET
            nome       = COALESCE(EXCLUDED.nome,       entrevista_reclamadas.nome),
            cnpj       = COALESCE(EXCLUDED.cnpj,       entrevista_reclamadas.cnpj),
            logradouro = COALESCE(EXCLUDED.logradouro, entrevista_reclamadas.logradouro),
            endcompl   = COALESCE(EXCLUDED.endcompl,   entrevista_reclamadas.endcompl);
    END LOOP;

    -- rastro do que o cliente disse, cru
    INSERT INTO peticoes.entrevista_respostas
        (entrevista_id, campo, valor_bruto, chatwoot_message_id)
    SELECT v_eid, k, left(CASE jsonb_typeof(v) WHEN 'string' THEN v #>> '{}' ELSE v::text END, 4000), v_msg
      FROM jsonb_each(v_campos) AS e(k, v)
    ON CONFLICT (chatwoot_message_id, campo) DO NOTHING;

    -- vínculo conversa <-> caso (uma entrevista pode passar por N conversas)
    IF v_conversa IS NOT NULL THEN
        INSERT INTO peticoes.entrevista_conversas
            (entrevista_id, chatwoot_account_id, chatwoot_conversation_id, chatwoot_inbox_id)
        VALUES (v_eid, v_conta, v_conversa, v_inbox)
        ON CONFLICT DO NOTHING;
    END IF;

    RETURN jsonb_build_object(
        'ok', true,
        'entrevista_id', v_eid,
        'criada', v_criada,
        'aceitos', to_jsonb(v_aceitos),
        'nao_mapeados', v_naomap,
        'campos_preenchidos', (SELECT count(*) FROM jsonb_object_keys(peticoes.montar_entrevista(v_eid))),
        'campos_no_roteiro', (SELECT count(*) FROM peticoes.perguntas WHERE ativo),
        'status', (SELECT status::text FROM peticoes.entrevistas WHERE id = v_eid)
    );
END $$;
