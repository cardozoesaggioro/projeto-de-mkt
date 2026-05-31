# RUNBOOK — colocar o Ponto Zero no ar

> Este runbook NÃO contém segredos nem valores de DNS inventados. Onde precisar
> de um valor real (chave, ID, host de CNAME), está marcado **[VOCÊ FORNECE]** e
> deve vir do painel da Meta / do Render. Nunca crie contas em nome de ninguém.

---

## 0. Pré-requisitos (decisões/contas suas)

| Item | Quem cria | Necessário para |
|---|---|---|
| Conta Render | **você** | hospedar o backend |
| App na Meta (developers.facebook.com) | **você** | OAuth do Instagram |
| Conta IG **Business/Creator** do cliente | **você/cliente** | a Graph API só lê Business/Creator |
| Chave do LLM | **você** | tom, pilares, frase das perguntas, arquétipos |
| Acesso ao DNS de `cardozoesaggioro.com.br` | **você** | apontar o subdomínio |

---

## 1. Deploy no Render (via Git)

1. Suba este diretório para um repositório Git (GitHub/GitLab).
2. No Render: **New → Web Service** → conecte o repo.
3. Runtime: **Docker** (usa o `Dockerfile`) — ou Native (usa o `Procfile`).
4. O Render injeta `PORT` automaticamente; o `server.py` já faz bind em
   `0.0.0.0:$PORT`. Não defina `PORT` à mão.
5. **Environment** → adicione as variáveis (valores **[VOCÊ FORNECE]**):
   - `META_APP_ID`, `META_APP_SECRET`, `META_REDIRECT_URI`
   - `IG_SCOPES` (sugerido: `instagram_basic,pages_show_list,business_management`)
   - `LLM_API_KEY`, `LLM_MODEL`
6. Deploy. Confira em `https://<seu-serviço>.onrender.com/api/health` —
   `missing_credentials` deve listar só o que ainda falta.

> Enquanto faltar credencial, o serviço **sobe e funciona** com conectores
> mockados. O `/api/health` diz exatamente o que falta.

---

## 2. DNS — `app.mkt.cardozoesaggioro.com.br`

> **Não invente o destino do CNAME.** O Render te dá o host exato na aba
> **Settings → Custom Domains** depois de você adicionar o domínio.

1. No Render, no serviço: **Settings → Custom Domains → Add Custom Domain** →
   `app.mkt.cardozoesaggioro.com.br`.
2. O Render mostrará um **valor de CNAME** do tipo `<algo>.onrender.com`
   → isso é **[RENDER FORNECE]**.
3. No DNS de `cardozoesaggioro.com.br`, crie:

   | Tipo | Nome | Valor |
   |---|---|---|
   | CNAME | `app.mkt` | `[RENDER FORNECE: <algo>.onrender.com]` |

4. Aguarde propagação; o Render emite o TLS (Let's Encrypt) sozinho.
5. Confirme: `https://app.mkt.cardozoesaggioro.com.br/api/health`.

---

## 3. Meta / Instagram (OAuth Graph API)

> A **Basic Display API foi desligada (dez/2024)** — usamos **Graph API**.
> Conta **pessoal não tem API**: o onboarding oferece converter para Creator
> ou upload manual.

1. **developers.facebook.com** → crie um App (tipo *Business*).
2. Produtos: adicione **Facebook Login** e **Instagram Graph API**.
3. Em **Facebook Login → Settings → Valid OAuth Redirect URIs**, cole **exatamente**:
   `https://app.mkt.cardozoesaggioro.com.br/auth/instagram/callback`
   (= `META_REDIRECT_URI`).
4. Copie **App ID** e **App Secret** → vão para as env vars no Render
   (**[VOCÊ FORNECE]**).
5. **App Review**: solicite as permissões de `IG_SCOPES` (leva semanas).
   A conta IG do cliente precisa ser **Business/Creator** e estar ligada a uma
   Página do Facebook.
6. Quando aprovado, implemente os dois `# TODO[REAL]` em `connectors.py` e
   `server.py` (`_ig_callback`): troca `code`→`access_token` e leitura de mídia.
   Hoje o `/auth/instagram/start` já monta a URL de consentimento quando as
   credenciais existem; sem elas, responde 503 explicando o que falta.

---

## 4. LLM

1. Gere a chave no provedor e ponha em `LLM_API_KEY`; ajuste `LLM_MODEL`.
2. Implemente o `# TODO[REAL]` em `server.py` (`llm_phrase_question`) para
   chamar o provedor via HTTPS. Lembre: **a confiança nunca vem do modelo** —
   o LLM dá o *valor*; o scorer calcula a confiança à parte.

---

## 5. Checklist de aceite (o que provar antes de liberar)

- [ ] `GET /api/health` responde e lista credenciais faltando.
- [ ] Fluxo mock fim-a-fim: onboarding → postura → entrevista → recap → amostra.
- [ ] Resolvedor de cor: cor coesa = confiança alta; lama = confiança baixa.
- [ ] Motor termina por suficiência (não pergunta tudo).
- [ ] `app.mkt...` resolve e serve o app por HTTPS.
- [ ] OAuth do IG: `start` redireciona quando há credenciais (após App Review).

---

## O que eu (engenheiro) preciso de VOCÊ

1. **Render**: criar a conta e conectar o repositório (ou me autorizar).
2. **Meta**: criar o App, me passar `META_APP_ID`/`META_APP_SECRET` e cadastrar
   o redirect URI; iniciar o **App Review**.
3. **LLM**: me passar `LLM_API_KEY` e confirmar o `LLM_MODEL`.
4. **DNS**: criar o CNAME `app.mkt` com o destino que o **Render** fornecer.
5. Confirmar se a conta de Instagram do cliente é **Business/Creator**.
