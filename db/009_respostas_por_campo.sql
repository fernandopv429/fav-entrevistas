-- =============================================================================
--  Rastro de respostas: uma linha por CAMPO, não por mensagem.
--
--  O índice era UNIQUE (chatwoot_message_id). Como uma única mensagem do
--  cliente costuma render vários campos ("sou vigilante, ganhava 2.148 e
--  trabalhava 12x36" = FUNCAO + SALARIO + escala), todos entram com o mesmo
--  mensagem_id e o ON CONFLICT descartava silenciosamente todos menos o
--  primeiro. O rastro ficava incompleto justamente nas mensagens mais ricas.
--
--  A chave certa é (mensagem, campo): reprocessar a mesma mensagem continua
--  não duplicando, mas cada campo dela é registrado.
-- =============================================================================

SET search_path TO peticoes, public;

DROP INDEX IF EXISTS peticoes.entrevista_respostas_msg_uk;

CREATE UNIQUE INDEX IF NOT EXISTS entrevista_respostas_msg_campo_uk
    ON peticoes.entrevista_respostas (chatwoot_message_id, campo);
