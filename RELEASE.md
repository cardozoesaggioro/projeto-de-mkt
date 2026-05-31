# RELEASE — subir o Ponto Zero no ar (rápido)

> Estratégia: lançar JÁ com o que funciona sem credencial de terceiros
> (motor + análise + upload + LLM). Instagram entra depois (App Review leva
> semanas). Nada de segredo no Git: o `.env` está no `.gitignore`.

## Pré-checagem (já validado)
- Repositório Git limpo, `.env` ignorado, sem segredos versionados.
- `Dockerfile` (instala Chromium), `Procfile`, `render.yaml` prontos.

---

## Passo 1 — Criar o repositório no GitHub
1. Acesse https://github.com/new (sua conta).
2. Nome: `ponto-zero` (ou o que preferir). **Private**. NÃO marque "add README".
3. Crie. Copie a URL, ex.: `https://github.com/SEU_USUARIO/ponto-zero.git`.

## Passo 2 — Enviar o código (no PowerShell)
```powershell
cd C:\Users\rodri\ponto_zero
git branch -M main                 # renomeia master -> main (padrão do Render)
git remote add origin https://github.com/SEU_USUARIO/ponto-zero.git
git push -u origin main
```
> Vai pedir login do GitHub na 1ª vez. O `.env` NÃO sobe (está ignorado).

## Passo 3 — Deploy no Render (Blueprint)
1. Acesse https://dashboard.render.com → **New → Blueprint**.
2. Conecte sua conta GitHub e selecione o repo `ponto-zero`.
3. O Render lê o `render.yaml` e propõe o serviço **ponto-zero** (Docker).
4. Clique em **Apply**.

## Passo 4 — Variáveis de ambiente (Environment)
No serviço criado, em **Environment**, preencha as marcadas `sync:false`:
- `LLM_API_KEY` = sua chave OpenAI (a mesma do `.env` local) — **essencial**.
- `LLM_MODEL` = `gpt-4o-mini` (ou ajuste).
- `LLM_PROVIDER` = pode deixar vazio (detecta pelo modelo).
- Meta (`META_APP_ID/SECRET/REDIRECT_URI`): só quando o App Review sair.
- `ENABLE_SITE_BROWSER`:
  - **Free (512MB):** deixe `0` — o Chromium pode estourar memória; o site
    cai no mock e o resto funciona pleno.
  - **Starter+ (mais RAM):** troque para `1` para render real do site.

## Passo 5 — Verificar
- Aguarde o build (instala Chromium; pode levar alguns minutos).
- Abra `https://<seu-serviço>.onrender.com/api/health` → deve mostrar
  `status: ok` e `llm: pronto (openai:gpt-4o-mini)`.
- Abra a raiz `/` → o app carrega.

## Passo 6 — Domínio (depois, opcional)
- Render → serviço → **Settings → Custom Domains** → add
  `app.mkt.cardozoesaggioro.com.br`.
- O Render te dá um host CNAME (`<algo>.onrender.com`). No DNS de
  `cardozoesaggioro.com.br`, crie: CNAME `app.mkt` → esse host.
- TLS é emitido automaticamente. (Ver RUNBOOK.md para detalhes.)

---

## Resumo do que cada plano entrega no 1º dia
| Recurso | Free | Starter+ |
|---|---|---|
| Motor + análise + recap | ✅ | ✅ |
| Upload (CV + PDF/DOCX) | ✅ | ✅ |
| LLM (tom/pilares/arquétipo) | ✅ | ✅ |
| Site real (Playwright) | mock (RAM) | ✅ |
| Instagram | após App Review | após App Review |
