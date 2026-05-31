# Ponto Zero

Monta o **brand book** de uma empresa (para alimentar um gerador de carrossel)
extraindo das fontes dela — site, Instagram, uploads, documentos — e fazendo ao
humano o **mínimo de perguntas de alto valor**.

> Arquitetura: front HTML (sem segredos) → backend fino (guarda credenciais, faz
> OAuth/fetch/LLM) → fontes + LLM. O motor é quase todo **determinístico**; o LLM
> entra só onde precisa de julgamento (tom, pilares, frase das perguntas, arquétipos).

## Como rodar (local, sem nenhuma credencial)

```bash
python server.py
# abre http://localhost:8000
```

O fluxo roda fim-a-fim com **conectores mockados**. Clique em *“Usar exemplos e
começar”* para ver onboarding → postura → entrevista → recap → slide de amostra.

## A alma do sistema (regras de design)

1. **Schema único** — todo atributo é um nó com a mesma forma (`schema.py`).
   `impact` é **injetado** pelo mapa de consumo do gerador, não nasce na
   extração. O nó é **monotônico**: valor confirmado pelo humano é pegajoso.
2. **Scorer central e único** (`scorer.py`) —
   `confidence = ceiling*(1-0.6*dispersion)*(0.6+0.4*agreement)*coverage`.
   Nenhum extrator emite confiança; nem o LLM. Ela é montada só de sinais
   observáveis do corpus.
3. **Motor** (`motor.py`) — `score = (1-confidence)*impact`; pergunta o de maior
   score se `>= tau`, senão para por **suficiência**. `tau` adaptativo
   (0.25; +0.04 hit; −0.05 correção; limites [0.12, 0.60]). Postura é a 1ª
   pergunta e repondera tudo. Confirmar âncora propaga coerência.
4. **Conectores finos** (`connectors.py`) — buscam o cru → `RawBundle` com
   `access_status` honesto. Só OAuth/SSO, nunca senha.
5. **Resolvedor de cor** (`color_cv.py`) — clusteriza em CIELAB (ΔE≤18), acha a
   cor que **recorre** entre imagens; a dispersão do cluster vira o sinal de
   confiança. Lama = dispersão alta/cobertura baixa = confiança baixa.
6. **Reconciliação por grupo** (`reconcile.py`) — Visual: o medido vence o
   declarado; Verbal/Estratégia: o declarado vence a inferência recente.
   `agreement` = fração de fontes que concordam com o vencedor.
7. **UX de reconhecimento** (`app.html`) — toda pergunta é **opções clicáveis**;
   dado ausente → opções de arquétipo; fim por suficiência; validação final é
   **reagir a um slide de amostra**, não auditar lista.
8. **Métricas** — distinção capturada, taxa de falsa confiança, hit rate,
   perguntas até suficiência, tau.

## Arquivos

| arquivo | papel |
|---|---|
| `schema.py` | o nó atômico (forma única, monotônico) |
| `scorer.py` | a fórmula central da confiança |
| `motor.py` | decisão: score, tau adaptativo, postura, propagação |
| `reconcile.py` | resolução de conflito por grupo |
| `color_cv.py` | resolvedor de cor (CIELAB, anti-lama) |
| `archetypes.py` | arquétipos para dado ausente |
| `connectors.py` | conectores finos (mock) + extratores + montagem |
| `config.py` | env vars + credenciais faltando |
| `store.py` | persistência sqlite |
| `server.py` | http.server + endpoints |
| `app.html` | front de página única |

## Endpoints

- `GET /` — serve o app
- `GET /api/health` — status + credenciais faltando
- `POST /api/extract/{site,instagram,upload}`
- `GET /auth/instagram/{start,callback}` — OAuth (atrás de TODO até credenciais)
- `GET|POST /api/brandbook` — lê / persiste (sqlite)
- auxiliares: `POST /api/motor/posture`, `GET /api/motor/next`,
  `POST /api/motor/answer`, `GET /api/sample`

## Ligar dados reais

Veja **`RUNBOOK.md`** (passos de credencial e DNS) e os `# TODO[REAL]` no
código (cada um cita a env var que precisa). Configure via `.env.example`.

## Deploy

`Dockerfile` (python:3.12-slim, usa `$PORT`) + `Procfile` (`web: python server.py`).
Alvo: Render. Domínio `app.mkt.cardozoesaggioro.com.br` por CNAME — ver RUNBOOK.
