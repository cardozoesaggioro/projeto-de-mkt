# -*- coding: utf-8 -*-
"""
connectors.py — Conectores FINOS + extratores.

Divisão de responsabilidades (fiel à especificação):
  - CONECTOR: busca o cru e devolve um RawBundle com `access_status` HONESTO
    (ok / partial / blocked / unauthorized). Zero lógica de marca. Só OAuth/SSO,
    nunca senha. Segredos só no servidor.
  - EXTRATOR: transforma RawBundle -> proveniência (valor + ESCOPO). Não calcula
    confiança e não conhece impacto.
  - build_nodes(): monta os nós, roda reconcile + color_cv e preenche os SINAIS.
    A confiança é montada depois pelo scorer; o impacto, injetado pelo motor.

Tudo começa MOCKADO para o fluxo rodar fim-a-fim sem nenhuma credencial.
Cada ponto que vira chamada real está marcado com `# TODO[REAL]` e a env var.
"""
from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

import archetypes
from color_cv import palette_from_image_bytes, resolve_brand_color
from reconcile import reconcile
from schema import Group, Node, Provenance, Scope, Signals, Status

RGB = tuple[int, int, int]

# Teto de confiança que cada tipo de fonte permite (entra no scorer).
SCOPE_CEILING: dict[Scope, float] = {
    Scope.DETERMINISTICO: 0.95,
    Scope.DECLARADO: 0.90,
    Scope.CV: 0.85,
    Scope.INFERENCIA: 0.60,
}

# Peso de COBERTURA por escopo: o quanto cada fonte "preenche" a evidência.
# Substitui o antigo `len(prov)/expected_sources` (arbitrário e que punia uma
# leitura determinística única). Coverage = min(1, soma dos pesos). Assim:
#   1 leitura determinística      -> 0.75 (autoritativa, não cai pela metade)
#   1 inferência                  -> 0.40 (fraca sozinha)
#   determinística + cv           -> 1.00 (corroboração)
SCOPE_COVERAGE_WEIGHT: dict[Scope, float] = {
    Scope.DETERMINISTICO: 0.75,
    Scope.DECLARADO: 0.70,
    Scope.CV: 0.65,
    Scope.INFERENCIA: 0.40,
}


# ===========================================================================
# RawBundle
# ===========================================================================
@dataclass
class RawBundle:
    source: str
    access_status: str           # ok | partial | blocked | unauthorized
    raw: dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "access_status": self.access_status,
                "detail": self.detail, "raw_keys": sorted(self.raw.keys())}


# ===========================================================================
# CONECTORES (mockados — devolvem hipóteses de exemplo coerentes)
# ===========================================================================
def _site_mock() -> RawBundle:
    """Hipótese de exemplo coerente — usada quando o Playwright não está disponível."""
    return RawBundle(
        source="site",
        access_status="ok",
        detail="MOCK — Playwright não acionado (sem render real).",
        raw={
            "color_css": {"value": (22, 54, 122), "scope": Scope.DETERMINISTICO},
            "image_palettes": [
                [((22, 54, 122), 0.55), ((212, 175, 55), 0.20), ((245, 245, 245), 0.25)],
                [((26, 60, 130), 0.50), ((210, 178, 60), 0.25), ((255, 255, 255), 0.25)],
            ],
            "typography": {"value": "Playfair Display / Inter", "scope": Scope.DETERMINISTICO},
            "logo": {"value": "https://exemplo/logo.svg", "scope": Scope.DECLARADO},
            "tone_of_voice": {"value": "autoridade acessível", "scope": Scope.DECLARADO},
            "tagline": {"value": "Decisões com base, não com achismo.", "scope": Scope.DECLARADO},
            "pillars": {"value": ["credibilidade", "clareza", "resultado"], "scope": Scope.DECLARADO},
            "positioning": {"value": "consultoria que traduz complexidade", "scope": Scope.DECLARADO},
            "audience": {"value": "decisores PME B2B", "scope": Scope.INFERENCIA},
            "vocabulary": {"value": ["base", "evidência", "clareza"], "scope": Scope.DECLARADO},
        },
    )


def _robots_allows(url: str, user_agent: str = "PontoZeroBot") -> bool:
    """Respeita robots.txt. Em dúvida (sem robots/erro), permite (padrão da web)."""
    import urllib.robotparser
    try:
        parts = urllib.parse.urlsplit(url)
        robots = f"{parts.scheme}://{parts.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots)
        rp.read()
        return rp.can_fetch(user_agent, url)
    except Exception:
        return True


def _parse_rgb(css: str) -> RGB | None:
    """Converte 'rgb(22, 54, 122)' / 'rgba(...)' do CSS computado em tupla."""
    if not css:
        return None
    nums = [int(float(n)) for n in __import__("re").findall(r"[\d.]+", css)[:3]]
    return (nums[0], nums[1], nums[2]) if len(nums) >= 3 else None


def connect_site(url: str) -> RawBundle:
    """
    Site: sobe um browser headless (Playwright), renderiza e lê o CSS COMPUTADO
    (cor/fonte exatas = deterministico), a copy do DOM, o logo/og:image, e tira
    um screenshot para a paleta via CV. Sem auth, respeitando robots.txt.

    Se o Playwright não estiver instalado, cai no mock (o fluxo nunca quebra).
    """
    try:
        from playwright.sync_api import sync_playwright  # import preguiçoso
    except ImportError:
        return _site_mock()

    if not url:
        return _site_mock()
    # hosts-placeholder do onboarding -> mock coerente (demo sem credencial)
    host = urllib.parse.urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host in {"exemplo.com.br", "exemplo.com", "example.com", "example.org"}:
        return _site_mock()
    if not _robots_allows(url):
        return RawBundle("site", "blocked",
                         detail=f"robots.txt não permite rastrear {url}.")

    try:
        return _site_real(url, sync_playwright)
    except Exception as exc:  # timeout / DNS / render -> honesto
        return RawBundle("site", "partial",
                         detail=f"Render falhou ({type(exc).__name__}: {exc}); sem dados do site.")


def _clean_title(title: str) -> str:
    """
    Limpa o <title>: 'Página | Marca' ou 'Marca - proposta de valor'.
    Escolhe o segmento mais DESCRITIVO (mais palavras), descartando o nome curto.
    """
    import re
    parts = [p.strip() for p in re.split(r"\s*[|\-–—:·]\s*", title) if p.strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    # o segmento mais descritivo tende a ser a frase (não o nome da marca):
    # desempata por nº de palavras e, em empate, pelo comprimento.
    return max(parts, key=lambda p: (len(p.split()), len(p)))


def _site_copy(meta: dict[str, str]) -> tuple[str, str]:
    """
    Decide tagline e posicionamento a partir dos candidatos do DOM.
    Tagline: h1 -> og:title -> hero -> <title> limpo -> h2 (primeiro não-vazio,
    com cara de frase, não navegação). Posicionamento: meta/og description.
    """
    def usable(s: str) -> bool:
        s = (s or "").strip()
        # evita lixo de navegação/cookie e textos longos demais p/ tagline
        return 6 <= len(s) <= 90 and not any(
            w in s.lower() for w in ("cookie", "menu", "aceitar", "política de priv"))

    candidates = [meta.get("h1", ""), meta.get("og_title", ""), meta.get("hero", ""),
                  _clean_title(meta.get("title", "")), meta.get("h2", "")]
    tagline = next((c.strip() for c in candidates if usable(c)), "")

    positioning = (meta.get("meta_desc") or meta.get("og_desc") or "").strip()
    # se não houver tagline mas houver título, usa o título inteiro como último recurso
    if not tagline and meta.get("title"):
        tagline = meta["title"].strip()[:90]
    return tagline, positioning


def _site_real(url: str, sync_playwright) -> RawBundle:
    """Render headless + extração determinística. Chamado por connect_site."""
    raw: dict[str, Any] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(user_agent="PontoZeroBot")
        page.goto(url, wait_until="networkidle", timeout=20000)

        # --- CSS COMPUTADO (deterministico): cor de destaque + tipografia ---
        # cor: pega a cor computada de um CTA/botão proeminente; senão, do h1.
        # PRIMÁRIA = cor de SUPERFÍCIE dominante (maior área colorida no topo);
        # ACENTO = cor do CTA/botão. Antes confundíamos os dois (pegava o CTA
        # como primária). Agora separamos: primária -> color_css, acento -> color_accent.
        colors = page.evaluate(
            """() => {
                const rgb = s => { const m=(s||'').match(/\\d+/g);
                    return (m && m.length>=3) ? [ +m[0], +m[1], +m[2] ] : null; };
                // "neutro" = branco/preto/cinza claro (fundo/texto), não conta como marca
                const neutral = c => !c || Math.min(c[0],c[1],c[2])>238 ||
                    Math.max(c[0],c[1],c[2])<18 ||
                    (Math.max(...c)-Math.min(...c) < 14 && Math.min(...c) > 170);
                // ACENTO: CTA/botão proeminente
                const cta = document.querySelector(
                    'button, .btn, a.button, [class*=cta], [class*=btn]');
                let accent = cta ? rgb(getComputedStyle(cta).backgroundColor) : null;
                if (neutral(accent)) accent = null;
                // PRIMÁRIA: maior área de superfície COLORIDA acima da dobra
                let best=null, bestArea=0;
                for (const el of document.querySelectorAll('header, section, div, nav, main, footer')) {
                    const r = el.getBoundingClientRect();
                    if (r.top > 760 || r.width*r.height < 40000) continue;
                    const bg = rgb(getComputedStyle(el).backgroundColor);
                    if (neutral(bg)) continue;
                    const area = r.width * Math.min(r.height, 800);
                    if (area > bestArea) { bestArea = area; best = bg; }
                }
                return { primary: best, accent };
            }"""
        )
        font = page.evaluate(
            "() => getComputedStyle(document.querySelector('h1, h2') || document.body).fontFamily"
        )
        if colors.get("primary"):
            raw["color_css"] = {"value": tuple(colors["primary"]), "scope": Scope.DETERMINISTICO}
        if colors.get("accent"):
            raw["color_accent"] = {"value": tuple(colors["accent"]), "scope": Scope.DETERMINISTICO}
        if font:
            raw["typography"] = {"value": font.split(",")[0].strip(' "\''),
                                 "scope": Scope.DETERMINISTICO}

        # --- COPY do DOM (robusta) ---
        # Colhe vários candidatos de uma vez; a escolha/limpeza é feita em Python.
        meta = page.evaluate(
            """() => {
                const q = s => document.querySelector(s);
                const t = el => el ? (el.innerText || el.textContent || '').trim() : '';
                const attr = (s,a) => { const e=q(s); return e ? (e.getAttribute(a)||'') : ''; };
                // hero: maior texto curto e proeminente perto do topo
                let hero = '';
                const cands = Array.from(document.querySelectorAll(
                    'h1, h2, header h1, header h2, [class*=hero] *, [class*=banner] *'));
                for (const el of cands) {
                    const fs = parseFloat(getComputedStyle(el).fontSize) || 0;
                    const txt = (el.innerText || '').trim().replace(/\\s+/g,' ');
                    if (fs >= 24 && txt.length >= 8 && txt.length <= 90) { hero = txt; break; }
                }
                return {
                    h1: t(q('h1')).replace(/\\s+/g,' '),
                    h2: t(q('h2')).replace(/\\s+/g,' '),
                    title: (document.title || '').trim(),
                    og_title: attr('meta[property=\"og:title\"]','content'),
                    og_desc:  attr('meta[property=\"og:description\"]','content'),
                    meta_desc: attr('meta[name=description]','content'),
                    hero: hero,
                };
            }"""
        )

        tagline, positioning = _site_copy(meta)
        if tagline:
            raw["tagline"] = {"value": tagline[:120], "scope": Scope.DECLARADO}
        if positioning:
            raw["positioning"] = {"value": positioning[:200], "scope": Scope.DECLARADO}

        # --- LOGO (detecção real, não só og:image) ---
        logo = page.evaluate(
            """() => {
                const abs = u => { try { return new URL(u, location.href).href; }
                                   catch(e){ return u || ''; } };
                const meta = el => ((el.getAttribute('alt')||'') + ' ' +
                    (el.className&&el.className.baseVal!==undefined?el.className.baseVal:el.className||'') + ' ' +
                    (el.id||'') + ' ' + (el.currentSrc||el.getAttribute('src')||'')).toLowerCase();
                const inHeader = el => !!el.closest(
                    'header, nav, [class*=header], [class*=navbar], [class*=topo], [id*=header]');
                // <img> e <svg>: pontua por palavra 'logo', header e proximidade do topo
                let best=null, bestS=-1, how='';
                for (const el of document.querySelectorAll('img, svg')) {
                    let s=0; const m=meta(el);
                    if (m.includes('logo')) s+=10;
                    if (inHeader(el)) s+=5;
                    const r=el.getBoundingClientRect(); if (r.top>=0 && r.top<220) s+=2;
                    const src = el.currentSrc || el.getAttribute('src') || '';
                    if (el.tagName==='SVG' || el.tagName==='svg') { if(s>0 && s>bestS){bestS=s;best='[svg inline]';how='svg';} continue; }
                    if (src && s>bestS) { bestS=s; best=src; how = m.includes('logo')?'img[logo]':(inHeader(el)?'img@header':'img'); }
                }
                if (best && bestS>=5) return {url: best.startsWith('[')?best:abs(best), how};
                const icon = document.querySelector('link[rel~=\"icon\"], link[rel=\"apple-touch-icon\"]');
                if (icon && icon.getAttribute('href')) return {url: abs(icon.getAttribute('href')), how:'favicon'};
                if (best) return {url: abs(best), how};
                const og = document.querySelector('meta[property=\"og:image\"]');
                return og ? {url: abs(og.getAttribute('content')||''), how:'og:image'} : {url:'', how:'none'};
            }"""
        )
        logo_how = ""
        if logo and logo.get("url"):
            raw["logo"] = {"value": logo["url"], "scope": Scope.DECLARADO}
            logo_how = logo.get("how", "")  # transparência: como achamos o logo

        # --- SCREENSHOT -> paleta via CV ---
        try:
            shot = page.screenshot(full_page=False)
            raw["image_palettes"] = [palette_from_image_bytes(shot)]
        except Exception:
            pass

        browser.close()

    status = "ok" if raw else "partial"
    logo_note = f"; logo via {logo_how}" if logo_how else ""
    return RawBundle("site", status, raw=raw,
                     detail=f"Playwright render OK ({len(raw)} sinais de {url}{logo_note}).")


GRAPH_API = "https://graph.facebook.com/v21.0"


def _graph_get(path: str, params: dict[str, str], timeout: int = 15) -> dict[str, Any]:
    """GET na Graph API e devolve JSON. Levanta em erro (o chamador trata)."""
    url = f"{GRAPH_API}/{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def connect_instagram(handle: str, has_meta_creds: bool = False,
                      token: str | None = None,
                      ig_user_id: str | None = None) -> RawBundle:
    """
    Instagram: SOMENTE Graph API (graph.facebook.com) via OAuth 2.0 da conta
    Business/Creator DO PRÓPRIO cliente. A Basic Display API foi desligada
    (dez/2024) — NÃO usar. Conta pessoal não tem API.

    Três estados, todos honestos no `access_status`:
      - COM token (OAuth concluído): leitura REAL de mídia/captions via Graph API.
      - COM credenciais Meta, SEM token: 'unauthorized' (falta concluir o OAuth).
      - SEM credenciais: mock 'partial' para o fluxo rodar sem segredo.
    """
    # ---- caminho REAL (token presente) -----------------------------------
    if token:
        return _instagram_real(token, ig_user_id)

    # ---- credenciais existem, mas OAuth não foi concluído ----------------
    if has_meta_creds:
        return RawBundle(source="instagram", access_status="unauthorized",
                         detail="OAuth não concluído — acesse /auth/instagram/start.")

    # ---- sem credenciais: mock parcial p/ o fluxo ------------------------
    return RawBundle(
        source="instagram",
        access_status="partial",
        detail="MOCK — sem credenciais Meta; leitura real bloqueada (App Review pendente).",
        raw={
            "image_palettes": [
                [((24, 58, 128), 0.6), ((214, 176, 58), 0.4)],
            ],
            "tone_of_voice": {"value": "autoridade acessível", "scope": Scope.INFERENCIA},
            "vocabulary": {"value": ["base", "dado", "prática"], "scope": Scope.INFERENCIA},
        },
    )


def _instagram_real(token: str, ig_user_id: str | None) -> RawBundle:
    """
    Leitura real via Graph API. Já implementado e GUARDADO por token —
    só executa após o OAuth + App Review aprovado.

    1) Descobre o ig_user_id (se não veio): /me/accounts -> página ->
       instagram_business_account.
    2) /{ig-id}/media?fields=caption,media_type,media_url,permalink -> captions
       e URLs de mídia.

    Das captions extraímos sinais de Verbal (tom/vocabulário) como INFERENCIA.
    # TODO[REAL]: baixar as media_url e rodar color_cv -> exige Pillow
    #   (decodificar JPEG/PNG). Marcado em requirements.txt.
    """
    try:
        if not ig_user_id:
            accts = _graph_get("me/accounts",
                               {"fields": "instagram_business_account", "access_token": token})
            pages = accts.get("data", [])
            ig_user_id = next(
                (p["instagram_business_account"]["id"] for p in pages
                 if p.get("instagram_business_account")), None)
            if not ig_user_id:
                return RawBundle("instagram", "unauthorized",
                                 detail="Nenhuma conta IG Business ligada a uma Página do Facebook.")

        media = _graph_get(f"{ig_user_id}/media",
                           {"fields": "caption,media_type,media_url,permalink",
                            "limit": "25", "access_token": token})
        items = media.get("data", [])
        captions = [it.get("caption", "") for it in items if it.get("caption")]
        media_urls = [it.get("media_url") for it in items if it.get("media_url")]

        raw: dict[str, Any] = {"media_urls": media_urls}
        if captions:
            # sinais Verbais inferidos das legendas (valor; confiança é do scorer)
            raw["tone_of_voice"] = {"value": _tone_from_captions(captions),
                                    "scope": Scope.INFERENCIA}
            raw["vocabulary"] = {"value": _top_words(captions), "scope": Scope.INFERENCIA}
        # paleta de cor das imagens do IG (CV real) — alimenta a cor primária
        palettes = _palettes_from_urls(media_urls[:8])
        if palettes:
            raw["image_palettes"] = palettes
        status = "ok" if items else "partial"
        return RawBundle("instagram", status, raw=raw,
                         detail=f"Graph API: {len(items)} mídias, {len(captions)} captions.")
    except Exception as exc:  # token expirado / permissão / rede
        return RawBundle("instagram", "unauthorized",
                         detail=f"Falha na Graph API ({type(exc).__name__}: {exc}).")


def _palettes_from_urls(urls: list[str], timeout: int = 10) -> list[list[tuple[RGB, float]]]:
    """Baixa cada imagem e extrai a paleta (CV). Falha de uma imagem não derruba."""
    out: list[list[tuple[RGB, float]]] = []
    for url in urls:
        if not url:
            continue
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                out.append(palette_from_image_bytes(resp.read()))
        except Exception:
            continue  # imagem indisponível / formato não suportado -> ignora
    return out


def _tone_from_captions(captions: list[str]) -> str:
    """Heurística simples de tom a partir das legendas (valor, não confiança)."""
    txt = " ".join(captions).lower()
    if any(w in txt for w in ("🚀", "bora", "vem", "partiu")):
        return "energético e direto"
    if any(w in txt for w in ("dado", "evidência", "análise", "estudo")):
        return "analítico e fundamentado"
    return "profissional acessível"


def _top_words(captions: list[str], n: int = 5) -> list[str]:
    """Palavras mais recorrentes nas legendas (vocabulário observado)."""
    from collections import Counter
    stop = {"de", "a", "o", "que", "e", "do", "da", "em", "um", "para", "com",
            "não", "uma", "os", "no", "se", "na", "por", "mais", "as", "dos"}
    words = [w.strip(".,!?:;#@").lower()
             for c in captions for w in c.split() if len(w) > 3]
    freq = Counter(w for w in words if w not in stop)
    return [w for w, _ in freq.most_common(n)]


def connect_upload(files: list[dict[str, Any]] | None = None) -> RawBundle:
    """
    Upload (logo/doc): paleta EXATA por CV (Pillow) E parse de PDF/DOCX.
    Cada item de `files` pode trazer:
      - {"b64": "<base64>", "name": "logo.png"} -> imagem: extrai paleta real
      - {"b64": "<base64>", "name": "brand.pdf"} -> documento: extrai copy
      - {"path": "/caminho/arquivo", "name": ...} -> idem, lendo do disco
      - {"palette": [[(rgb,peso),...]]} -> paleta já pronta (mock/teste)
    Sem nenhum desses, devolve um mock coerente para o fluxo rodar.
    """
    files = files or []
    palettes: list[list[tuple[RGB, float]]] = []
    doc_signals: dict[str, Any] = {}
    real_imgs = 0
    real_docs = 0
    errors: list[str] = []

    for f in files:
        name = str(f.get("name", "")).lower()
        try:
            if "palette" in f:
                palettes.append([tuple(c) if not isinstance(c, tuple) else c
                                 for c in f["palette"]])
                continue
            # carrega os bytes (b64 ou path)
            if "b64" in f:
                data = base64.b64decode(f["b64"])
            elif "path" in f:
                with open(f["path"], "rb") as fh:
                    data = fh.read()
            else:
                continue

            if name.endswith((".pdf", ".docx")):
                text = _text_from_document(data, name)
                doc_signals.update(_doc_signals(text))  # brand guide = DECLARADO
                real_docs += 1
            else:
                palettes.append(palette_from_image_bytes(data))
                real_imgs += 1
        except Exception as exc:  # arquivo inválido / dependência ausente
            errors.append(f"{name or 'arquivo'}: {type(exc).__name__}: {exc}")

    raw: dict[str, Any] = {}
    if palettes:
        raw["image_palettes"] = palettes
        # cor do logo = cor mais proeminente entre as paletas válidas (defensivo:
        # ignora entradas malformadas vindas do cliente).
        valid = [c for pal in palettes for c in pal
                 if isinstance(c, (list, tuple)) and len(c) == 2]
        if valid:
            logo_rgb = max(valid, key=lambda c: c[1])[0]
            raw["color_logo"] = {"value": tuple(logo_rgb), "scope": Scope.CV}
    raw.update(doc_signals)

    if not raw:
        # mock default: um logo institucional
        raw = {"image_palettes": [[((22, 54, 122), 0.7), ((212, 175, 55), 0.3)]],
               "color_logo": {"value": (212, 175, 55), "scope": Scope.CV}}
        detail = "MOCK — nenhum arquivo real enviado."
    else:
        bits = []
        if real_imgs:
            bits.append(f"{real_imgs} imagem(ns) por CV")
        if real_docs:
            bits.append(f"{real_docs} documento(s) por parse")
        detail = "Upload real: " + (", ".join(bits) or "paleta fornecida") + "."
        if errors:
            detail += f" Falhas: {'; '.join(errors)}"

    return RawBundle(source="upload", access_status="ok", detail=detail, raw=raw)


def _text_from_document(data: bytes, name: str) -> str:
    """Extrai texto de PDF (pypdf) ou DOCX (python-docx). Import preguiçoso."""
    from io import BytesIO

    if name.endswith(".pdf"):
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if name.endswith(".docx"):
        import docx
        document = docx.Document(BytesIO(data))
        return "\n".join(p.text for p in document.paragraphs)
    return ""


def _doc_signals(text: str) -> dict[str, Any]:
    """
    Deriva sinais a partir da copy de um brand guide (fonte DECLARADA).
    Heurísticas simples e transparentes — o VALOR vem do documento; a CONFIANÇA
    é calculada à parte pelo scorer.
    """
    import re

    sig: dict[str, Any] = {}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    low = text.lower()

    # tagline: linha curta e marcante perto do topo (ex.: slogan)
    for ln in lines[:8]:
        if 8 <= len(ln) <= 70 and ln[0].isupper() and not ln.endswith(":"):
            sig["tagline"] = {"value": ln, "scope": Scope.DECLARADO}
            break

    # positioning: frase com verbos de posicionamento
    for ln in lines:
        if re.search(r"\b(somos|ajudamos|missão|transformamos|entregamos)\b", ln, re.I):
            sig["positioning"] = {"value": ln[:200], "scope": Scope.DECLARADO}
            break

    # pilares: itens listados sob um título "pilares/valores/princípios"
    pillars = _pillars_after_heading(lines, ("pilares", "valores", "princípios", "principios"))
    if pillars:
        sig["pillars"] = {"value": pillars[:5], "scope": Scope.DECLARADO}

    # vocabulário: palavras recorrentes do documento
    vocab = _top_words([text], n=6)
    if vocab:
        sig["vocabulary"] = {"value": vocab, "scope": Scope.DECLARADO}

    # tom: heurística pelo vocabulário do guia
    if any(w in low for w in ("evidência", "dado", "análise", "rigor")):
        sig["tone_of_voice"] = {"value": "analítico e fundamentado", "scope": Scope.DECLARADO}
    elif any(w in low for w in ("cuidado", "perto", "humano", "acolher")):
        sig["tone_of_voice"] = {"value": "próximo e acolhedor", "scope": Scope.DECLARADO}

    return sig


def _pillars_after_heading(lines: list[str], headings: tuple[str, ...]) -> list[str]:
    """Coleta itens de lista logo após um título de pilares/valores."""
    out: list[str] = []
    capture = False
    for ln in lines:
        low = ln.lower().rstrip(":")
        if low in headings or any(low.startswith(h) for h in headings):
            capture = True
            continue
        if capture:
            item = ln.lstrip("-•*0123456789. ").strip()
            # fim da lista: vazio, longo demais, ou cara de sentença (ponto/frase)
            is_sentence = item.endswith(".") or len(item.split()) > 4
            if not item or len(item) > 40 or is_sentence:
                if out:
                    break
                continue
            out.append(item)
            if len(out) >= 5:
                break
    return out


# Stubs explicitamente fora do v1 (exigem aprovação de Partner).
def connect_linkedin(*_a, **_k) -> RawBundle:
    # TODO[REAL]: LinkedIn exige aprovação de Partner — fora do v1.
    return RawBundle("linkedin", "unauthorized", "STUB — Partner approval (fora do v1).")


def connect_tiktok(*_a, **_k) -> RawBundle:
    # TODO[REAL]: TikTok — fora do v1.
    return RawBundle("tiktok", "unauthorized", "STUB — fora do v1.")


# ===========================================================================
# DEFINIÇÃO DOS ATRIBUTOS (o que o brand book tem)
# ===========================================================================
@dataclass
class AttrDef:
    id: str
    label: str
    group: Group
    anchor: bool = False
    propagates: list[str] = field(default_factory=list)
    expected_sources: int = 2   # p/ cobertura: quantos canais esperamos corroborar
    is_color: bool = False


ATTR_DEFS: list[AttrDef] = [
    AttrDef("primary_color", "Cor primária", Group.VISUAL, anchor=True,
            propagates=["secondary_color", "typography", "logo"],
            expected_sources=3, is_color=True),
    AttrDef("secondary_color", "Cor secundária", Group.VISUAL, expected_sources=2, is_color=True),
    AttrDef("typography", "Tipografia", Group.VISUAL, expected_sources=2),
    AttrDef("logo", "Logo", Group.VISUAL, expected_sources=2),
    AttrDef("tone_of_voice", "Tom de voz", Group.VERBAL, anchor=True,
            propagates=["tagline", "vocabulary"], expected_sources=3),
    AttrDef("tagline", "Tagline", Group.VERBAL, expected_sources=2),
    AttrDef("vocabulary", "Vocabulário", Group.VERBAL, expected_sources=2),
    AttrDef("pillars", "Pilares de conteúdo", Group.ESTRATEGIA, anchor=True,
            propagates=["positioning", "audience"], expected_sources=2),
    AttrDef("positioning", "Posicionamento", Group.ESTRATEGIA, expected_sources=2),
    AttrDef("audience", "Público-alvo", Group.ESTRATEGIA, expected_sources=2),
    AttrDef("archetype", "Arquétipo", Group.ESTRATEGIA, expected_sources=2),
]


# ===========================================================================
# EXTRATORES + montagem dos nós
# ===========================================================================
def _gather_provenance(attr_id: str, bundles: list[RawBundle]) -> list[Provenance]:
    """Colhe a proveniência de um atributo textual a partir dos bundles."""
    prov: list[Provenance] = []
    for b in bundles:
        obs = b.raw.get(attr_id)
        if obs is None:
            continue
        prov.append(Provenance(
            source=b.source, scope=obs["scope"], value=obs["value"],
            access_status=b.access_status, detail=b.detail,
        ))
    return prov


def _color_provenance(bundles: list[RawBundle]) -> tuple[list[Provenance], Any]:
    """Proveniência de cor: roda o resolvedor CV sobre as paletas de imagem."""
    all_palettes: list[list[tuple[RGB, float]]] = []
    prov: list[Provenance] = []

    for b in bundles:
        # cor declarada no CSS (deterministico) entra como proveniência forte
        css = b.raw.get("color_css")
        if css:
            prov.append(Provenance(b.source, css["scope"], _hex(css["value"]),
                                   b.access_status, b.detail))
        pals = b.raw.get("image_palettes")
        if pals:
            all_palettes.extend(pals)

    cv = resolve_brand_color(all_palettes) if all_palettes else None
    if cv:
        prov.append(Provenance("cv:cluster", Scope.CV, cv.hex, "ok",
                               f"cluster ΔE≤18; {cv.n_images}/{cv.n_total_images} imgs"))
    return prov, cv


def _hex(rgb: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _hex_to_rgb(h: str) -> RGB | None:
    h = str(h).lstrip("#")
    if len(h) != 6:
        return None
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return None


def _perceptual_agreement(prov: list[Provenance], winner_hex: str) -> float:
    """
    Agreement de COR por proximidade perceptual (ΔE), não por igualdade de string.
    Conta as fontes cujo valor está dentro de ΔE<=18 do vencedor. Suavizado
    (Laplace) igual ao reconcile, para não dar 1.0 com fonte única.
    Corrige o caso #16367A vs #18397E (mesma cor pro olho) virar discordância.
    """
    from color_cv import delta_e, rgb_to_lab, CLUSTER_THRESH

    win_rgb = _hex_to_rgb(winner_hex)
    if win_rgb is None:
        return (0 + 0.5) / (len(prov) + 1)
    win_lab = rgb_to_lab(win_rgb)
    matches = 0
    for p in prov:
        rgb = _hex_to_rgb(p.value) if isinstance(p.value, str) else None
        if rgb and delta_e(rgb_to_lab(rgb), win_lab) <= CLUSTER_THRESH:
            matches += 1
    return round((matches + 0.5) / (len(prov) + 1), 4)


def _corpus_from_nodes(nodes: dict[str, Node]) -> str:
    """Junta a copy textual já extraída (tagline/posicionamento/vocabulário)."""
    parts: list[str] = []
    for nid in ("tagline", "positioning", "vocabulary"):
        n = nodes.get(nid)
        if n and n.value:
            parts.append(" ".join(n.value) if isinstance(n.value, list) else str(n.value))
    return " · ".join(parts).strip()


def infer_strategic(corpus: str, llm_call) -> dict[str, Any]:
    """
    Infere a CAMADA ESTRATÉGICA (tom, pilares, arquétipo, público) a partir da
    copy, via LLM. O LLM dá o VALOR; a confiança é calculada à parte (INFERÊNCIA).
    Retorna {} se não houver LLM ou em qualquer falha (fluxo nunca quebra).
    """
    import json
    ids = [a.id for a in archetypes.ARCHETYPES]
    prompt = (
        f'Com base SOMENTE nesta copy de uma marca, infira o essencial.\n'
        f'Copy: """{corpus[:1500]}"""\n'
        f'Responda APENAS um JSON válido com as chaves: '
        f'{{"tone_of_voice": "<2-4 palavras>", "pillars": ["..","..",".."], '
        f'"archetype": "<um id de: {ids}>", "audience": "<quem é o público>"}}.'
    )
    raw = llm_call(prompt, system="Responda só JSON válido, em pt-BR.", max_tokens=400)
    if not raw:
        return {}
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception:
        return {}
    out: dict[str, Any] = {}
    if isinstance(data.get("tone_of_voice"), str):
        out["tone_of_voice"] = data["tone_of_voice"].strip()
    if isinstance(data.get("pillars"), list) and data["pillars"]:
        out["pillars"] = [str(p).strip() for p in data["pillars"]][:5]
    if data.get("archetype") in ids:
        out["archetype"] = data["archetype"]
    if isinstance(data.get("audience"), str):
        out["audience"] = data["audience"].strip()
    return out


def build_nodes(bundles: list[RawBundle],
                previous: dict[str, Node] | None = None,
                llm_call=None) -> dict[str, Node]:
    """
    Monta os nós: valor (via reconcile) + SINAIS observáveis. NÃO calcula
    confiança (scorer faz) e NÃO injeta impacto (motor faz).

    MONOTONICIDADE (regra #1): se `previous` traz um nó já tocado pelo humano
    (confirmado/corrigido), o valor/estado dele são PEGAJOSOS — a re-extração
    acumula proveniência nova mas não atropela a decisão humana.
    """
    previous = previous or {}
    usable = [b for b in bundles if b.access_status in ("ok", "partial")]
    nodes: dict[str, Node] = {}

    for d in ATTR_DEFS:
        # 1) colher proveniência (cor tem caminho próprio via CV)
        cv = None
        if d.is_color and d.id == "primary_color":
            prov, cv = _color_provenance(usable)
        elif d.id == "secondary_color":
            # secundária/acento = cor do CTA (determinística) + cor do logo (CV)
            prov = []
            for b in usable:
                accent = b.raw.get("color_accent")
                if accent:
                    prov.append(Provenance(b.source, accent["scope"], _hex(tuple(accent["value"])),
                                           b.access_status, b.detail))
                logo = b.raw.get("color_logo")
                if logo:
                    prov.append(Provenance(b.source, logo["scope"], _hex(tuple(logo["value"])),
                                           b.access_status, b.detail))
        else:
            prov = _gather_provenance(d.id, usable)

        # 2) reconciliar conflito por grupo
        res = reconcile(d.group, prov)

        # 3) montar SINAIS observáveis
        sig = Signals()
        if prov:
            sig.ceiling = max(SCOPE_CEILING.get(p.scope, 0.6) for p in prov)
            # COBERTURA unificada: soma de pesos por escopo (autoridade + corroboração)
            sig.coverage = min(1.0, sum(SCOPE_COVERAGE_WEIGHT.get(p.scope, 0.4)
                                        for p in prov))
            is_color = cv is not None or d.id == "secondary_color"
            if cv is not None:
                # COR primária: agreement perceptual (ΔE) + dispersão que inclui
                # a FALTA DE RECORRÊNCIA entre imagens (sinal anti-lama real).
                sig.agreement = _perceptual_agreement(prov, res["value"])
                recurrence_gap = 1.0 - cv.coverage
                sig.dispersion = round(max(cv.dispersion, recurrence_gap), 4)
            elif d.id == "secondary_color":
                sig.agreement = _perceptual_agreement(prov, res["value"])
                sig.dispersion = 0.0
            else:
                # TEXTUAL/categórico: divergência fica SÓ no agreement (já suavizado
                # no reconcile). Sem dispersão própria -> evita penalizar 2x (#4).
                sig.agreement = res["agreement"]
                sig.dispersion = 0.0
            status = Status.PALPITE
            value = res["value"]
            scope = res["scope"]
            alternatives = _alternatives(d, prov, cv)
        else:
            # AUSENTE: vira pergunta de arquétipo / opções
            status = Status.AUSENTE
            value = None
            scope = Scope.INFERENCIA
            alternatives = archetypes.as_options() if d.id == "archetype" else []

        node = Node(
            id=d.id, label=d.label, group=d.group, scope=scope, value=value,
            status=status, signals=sig, provenance=prov,
            alternatives=alternatives, anchor=d.anchor, propagates=d.propagates,
        )

        # MONOTONICIDADE: preserva a decisão humana de uma extração anterior.
        prev = previous.get(d.id)
        if prev is not None and prev.is_sticky:
            node.value = prev.value
            node.status = prev.status
            node.scope = prev.scope
            # valor humano = sinal máximo (teto cheio, sem dispersão)
            node.signals = Signals(ceiling=1.0, dispersion=0.0,
                                   agreement=1.0, coverage=1.0)

        nodes[d.id] = node

    # ---- enriquecimento ESTRATÉGICO via LLM (preenche ausentes) ----
    if llm_call is not None:
        corpus = _corpus_from_nodes(nodes)
        if len(corpus) >= 30:
            inferred = infer_strategic(corpus, llm_call)
            for attr_id, value in inferred.items():
                n = nodes.get(attr_id)
                # só preenche ausentes/vazios e nunca atropela decisão humana
                if n is None or n.is_sticky or n.value not in (None, "", []):
                    continue
                n.value = value
                n.scope = Scope.INFERENCIA
                n.status = Status.PALPITE
                n.provenance.append(Provenance("llm:inferência", Scope.INFERENCIA,
                                               value, "ok", "inferido da copy do site"))
                # confiança de INFERÊNCIA: baixa de propósito -> vira pergunta
                n.signals = Signals(ceiling=SCOPE_CEILING[Scope.INFERENCIA],
                                    dispersion=0.0, agreement=0.75, coverage=0.5)
                # arquétipo mantém as opções clicáveis como alternativas
                if attr_id == "archetype" and not n.alternatives:
                    n.alternatives = archetypes.as_options()

    return nodes


def _alternatives(d: AttrDef, prov: list[Provenance], cv) -> list[Any]:
    """Opções clicáveis = palpite + alternativas (reconhecimento, não evocação)."""
    if d.is_color and cv is not None:
        return cv.alternatives
    # valores distintos vistos em outras fontes viram alternativas
    seen, alts = set(), []
    for p in prov:
        key = str(p.value).strip().lower()
        if key not in seen:
            seen.add(key)
            alts.append(p.value)
    return alts[1:5]  # tira o 1º (já é o palpite) e limita


def run_all_connectors(
    site_url: str | None,
    instagram_handle: str | None,
    uploads: list[dict[str, Any]] | None,
    has_meta_creds: bool = False,
) -> list[RawBundle]:
    """Aciona os conectores pedidos e devolve os RawBundles."""
    bundles: list[RawBundle] = []
    if site_url:
        bundles.append(connect_site(site_url))
    if instagram_handle:
        bundles.append(connect_instagram(instagram_handle, has_meta_creds))
    if uploads is not None:
        bundles.append(connect_upload(uploads))
    return bundles
