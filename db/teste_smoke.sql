-- Teste de fumaça: cria um caso de exemplo e devolve o payload da API.
-- Não é migração (fora do padrão NNN_*.sql, o apply.py ignora).
-- Rode com:  python3 db/rodar_sql.py db/teste_smoke.sql

SET search_path TO peticoes, public;

DO $$
DECLARE
    v_contato bigint;
    v_id      bigint;
BEGIN
    INSERT INTO peticoes.contatos
        (chatwoot_account_id, chatwoot_contact_id, nome, telefone, email)
    VALUES (1, 9001, 'João da Silva Teste', '+5511999990000', 'joao.teste@example.com')
    ON CONFLICT (chatwoot_account_id, chatwoot_contact_id)
        DO UPDATE SET nome = EXCLUDED.nome
    RETURNING id INTO v_contato;

    INSERT INTO peticoes.entrevistas (
        codigo, status, contato_id,
        chatwoot_account_id, chatwoot_conversation_id, chatwoot_inbox_id,
        recl_nome, recl_cpf, recl_rg, recl_nasc, recl_endereco, recl_cep,
        recl_nacionalidade, recl_estadocivil, email,
        data_admissao, data_rescisao, funcao, salario, tipo_dispensa,
        escala, jornada_horario, tem_adic_noturno, finais_semana,
        intervalo_suprimido, intervalo_gozado, media_horas_extras,
        periodo_antecedente, periodo_sucedente,
        folgas_trabalhadas, ft_qtd_media, val_ft,
        vale_refeicao, vale_transporte,
        acumulo_funcao, funcoes_acumuladas, tem_periculosidade, tem_insalubridade,
        holerites, espelho_ponto, fatos_narrados, municipio, concluida_em
    ) VALUES (
        'SMOKE-001', 'concluida', v_contato,
        1, 9001, 3,
        'João da Silva Teste', '123.456.789-00', '12.345.678-9', '1988-04-12',
        'Rua das Acácias, 120, Centro, Guarulhos/SP', '07010-000',
        'brasileiro', 'casado', 'joao.teste@example.com',
        '2019-03-01', '2024-11-30', 'Vigilante', 2148.22, 'rescisao_indireta',
        '12x36', 'das 19h às 07h', true, true,
        true, '15 minutos', 'Até 1 hora',
        15, 20,
        true, '5 a 6', 'R$ 180 a R$ 200',
        true, true,
        true, 'Também operava o portão e fazia rondas externas', true, false,
        true, false,
        'Trabalhei cinco anos na escala 12x36 em posto de vigilância. '
        'O intervalo nunca era respeitado e as folgas eram convocadas por WhatsApp.',
        'Guarulhos', now()
    )
    ON CONFLICT (codigo) DO UPDATE SET atualizado_em = now()
    RETURNING id INTO v_id;

    INSERT INTO peticoes.entrevista_reclamadas
        (entrevista_id, ordem, nome, cnpj, logradouro, endcompl)
    VALUES
     (v_id, 1, 'Segurança Alfa Vigilância Ltda', '11.222.333/0001-44',
      'Av. Industrial, 900', 'Guarulhos/SP, CEP 07040-000'),
     (v_id, 2, 'Shopping Center Beta S/A', '55.666.777/0001-88',
      NULL, 'Guarulhos/SP')
    ON CONFLICT (entrevista_id, ordem) DO UPDATE SET nome = EXCLUDED.nome;

    INSERT INTO peticoes.entrevista_respostas
        (entrevista_id, campo, valor_bruto, valor_normalizado, chatwoot_message_id)
    VALUES (v_id, 'SALARIO', 'era 2148,22 por mês', '2148.22', 77001)
    ON CONFLICT (chatwoot_message_id) DO NOTHING;
END $$;

-- 1) payload completo que iria para a API
SELECT jsonb_pretty(peticoes.montar_payload(id)) AS payload
FROM peticoes.entrevistas WHERE codigo = 'SMOKE-001';

-- 2) quantos campos do objeto entrevista foram preenchidos
SELECT (SELECT count(*) FROM jsonb_object_keys(peticoes.montar_entrevista(id)))
           AS campos_entrevista,
       (SELECT count(*) FROM peticoes.perguntas WHERE ativo) AS campos_no_roteiro
FROM peticoes.entrevistas WHERE codigo = 'SMOKE-001';

-- 3) fila de geração
SELECT id, codigo, recl_nome, tentativas FROM peticoes.vw_fila_geracao;
