# Como a entrevista se amarra à conversa do Chatwoot

Documento de decisão. O resumo: **o caso pertence ao contato, não à conversa.**

## O que a documentação do Chatwoot mostra

Quatro achados, todos verificados no código-fonte:

**1. O que o Chatwoot chama de `id` da conversa é o `display_id`.**
No serializer, o campo `id` é populado a partir de `conversation.display_id`
([_conversation.json.jbuilder](https://raw.githubusercontent.com/chatwoot/chatwoot/develop/app/views/api/v1/models/_conversation.json.jbuilder)).
As rotas da API também usam o `display_id` no path
([routes.rb](https://raw.githubusercontent.com/chatwoot/chatwoot/develop/config/routes.rb)).

**2. O `display_id` é sequencial POR CONTA.** Vem de um trigger:

```ruby
NEW.display_id := nextval('conv_dpid_seq_' || NEW.account_id);
```

Ou seja, a conversa 42 da conta 1 e a conversa 42 da conta 2 são conversas
diferentes com o mesmo número. **`conversation_id` sozinho não identifica nada.**

**3. Existe `uuid` na conversa**, com índice único global — âncora mais forte que
o `display_id`, quando o payload traz.

**4. As etiquetas não vêm no webhook.** O payload de `message_created` traz
`id`, `display_id` e `additional_attributes` da conversa, mas não os labels. E
`custom_attributes` de conversa historicamente também não são enviados
([issue #3558](https://github.com/chatwoot/chatwoot/issues/3558)) — os de contato
são. Então não dá para guardar um `entrevista_id` na conversa e esperar que ele
volte.

## O problema de fundo: conversa ≠ caso

No Chatwoot, conversa é uma **sessão de atendimento**. Ela é resolvida e, quando o
cliente volta a falar, abre **outra** conversa, com novo `display_id`.

Uma entrevista trabalhista tem 75 campos. Ninguém responde 75 perguntas numa
sentada. Então amarrar a entrevista a uma conversa produz esta falha:

> Cliente responde 40 campos hoje → conversa é resolvida → cliente volta amanhã
> para terminar → **entrevista nova e vazia; as 40 respostas ficam órfãs.**

O inverso — amarrar só no contato — também falha: duas reclamações do mesmo
cliente (dois empregadores diferentes) cairiam no mesmo caso.

## A solução: âncora em dois níveis

- O **caso pertence ao contato** (`account_id` + `contact_id`, com telefone como
  reforço). É o que persiste entre conversas.
- As **conversas que alimentaram o caso** ficam em `entrevista_conversas`, várias
  por entrevista, com `UNIQUE (account_id, conversation_id)`: uma conversa serve a
  um caso só, mas um caso atravessa quantas conversas precisar.

```
contato (account_id + contact_id)          <- o que persiste
   └── entrevista (o caso)
         ├── entrevista_conversas          <- N conversas do Chatwoot
         │     conv 1001 (resolvida)
         │     conv 1002 (resolvida)
         │     conv 1004 (atual)
         └── 75 campos, preenchidos aos poucos
```

## Resolução em cascata

Da âncora mais forte para a mais fraca:

| Ordem | Âncora | `resolvida_por` |
|---|---|---|
| 1 | `entrevista_id` explícito | `entrevista_id` |
| 2 | `codigo` do caso | `codigo` |
| 3 | `uuid` da conversa (único global) | `uuid_da_conversa` |
| 4 | (conta, conversa) na tabela de vínculos | `conversa` |
| 5 | caso **em aberto** do contato | `contato_caso_aberto` |
| 6 | caso **fechado** + etiqueta de retomada | `contato_caso_retomado_por_etiqueta` |
| 7 | caso fechado, sem etiqueta → abre novo | `caso_novo_apos_fechado` |

O passo 5 é o que salva as 40 respostas. O `vinculo.resolvida_por` vem em toda
resposta, então dá para auditar por que os dados entraram naquele caso.

## Caso concluído: a etiqueta decide

Entrevista concluída fica congelada — mensagem nova do mesmo contato abre caso
novo, para não alterar peça que o advogado já revisou.

Para retomar de onde parou, a conversa precisa da etiqueta **`retomar-entrevista`**
(configurável em `ETIQUETA_RETOMAR` no `.env`). Com ela, o caso volta para
`em_andamento`, sobrescreve o que estiver errado e preenche o que faltava. Fica
registrado: `reaberta_em`, `reaberta_etiqueta`, `reaberturas`.

Como as etiquetas não vêm no webhook, há dois caminhos:

1. **O agente manda em `etiquetas`** — caminho normal, não depende de token.
2. **O serviço consulta o Chatwoot** — se `CHATWOOT_BASE_URL` e
   `CHATWOOT_API_TOKEN` estiverem no `.env`, via
   `GET /api/v1/accounts/{conta}/conversations/{display_id}/labels`.

E se não der para determinar? **Abre caso novo.** `[]` (consultei, não há
etiqueta) e omitir (não sei) são tratados de forma diferente de propósito: na
dúvida, o serviço não sobrescreve trabalho já concluído.

## Multi-conta: `account_id` é obrigatório

Como o `display_id` é sequencial por conta, mandar `chatwoot_conversation_id` sem
`chatwoot_account_id` é recusado com **422**. Numa instalação com mais de uma
conta, aceitar isso significaria gravar a resposta de um cliente no caso de outro
— o tipo de erro que não aparece em teste e aparece em produção.

## Estabilidade: o que garante o quê

| Risco | Como está tratado |
|---|---|
| Conversa resolvida, cliente volta | caso reencontrado pelo contato (passo 5) |
| `display_id` repetido entre contas | `account_id` obrigatório, unique composto |
| Duas mensagens simultâneas | `UNIQUE (account_id, conversation_id)` no vínculo |
| Mesma mensagem processada 2x | `UNIQUE (chatwoot_message_id)` nas respostas |
| Conversa "roubada" por outro caso | unique impede; a conversa fica no caso original |
| Peça revisada alterada por acidente | congelamento + etiqueta explícita |
| Etiquetas ilegíveis | caminho conservador (caso novo), com aviso na resposta |

## Testes

```bash
python3 api/teste_vinculo.py
```

22 verificações, incluindo o cenário completo de conversa resolvida → nova
conversa → conclusão → retomada por etiqueta.
