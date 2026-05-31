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
    if not _robots_allows(url):
        return RawBundle("site", "blocked",
                         detail=f"robots.txt não permite rastrear {url}.")

    try:
        return _site_real(url, sync_playwright)
    except Exception as exc:  # timeout / DNS / render -> honesto
        return RawBundle("site", "partial",
                         detail=f"Render falhou ({type(exc).__name__}: {exc}); sem dados do site.")


def _site_real(url: str, sync_playwright) -> RawBundle:
    """Render headless + extração determinística. Chamado por connect_site."""
    raw: dict[str, Any] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(user_agent="PontoZeroBot")
        page.goto(url, wait_until="networkidle", timeout=20000)

        # --- CSS COMPUTADO (deterministico): cor de destaque + tipografia ---
        # cor: pega a cor computada de um CTA/botão proeminente; senão, do h1.
        accent = page.evaluate(
            """() => {
                const el = document.querySelector('button, .btn, a.button, [class*=cta]')
                          || document.querySelector('h1') || document.body;
                const s = getComputedStyle(el);
                // prioriza background com cor; senão a cor do texto
                const bg = s.backgroundColor;
                return (bg && bg !== 'rgba(0, 0, 0, 0)') ? bg : s.color;
            }"""
        )
        font = page.evaluate(
            "() => getComputedStyle(document.querySelector('h1, h2') || document.body).fontFamily"
        )
        rgb = _parse_rgb(accent)
        if rgb:
            raw["color_css"] = {"value": rgb, "scope": Scope.DETERMINISTICO}
        if font:
            raw["typography"] = {"value": font.split(",")[0].strip(' "\''),
                                 "scope": Scope.DETERMINISTICO}

        # --- COPY do DOM ---
        def text(sel: str) -> str:
            el = page.query_selector(sel)
            return (el.inner_text().strip() if el else "")[:200]

        h1 = text("h1")
        desc = page.evaluate(
            "() => (document.querySelector('meta[name=description]')||{}).content || ''"
        )
        if h1:
            raw["tagline"] = {"value": h1, "scope": Scope.DECLARADO}
        if desc:
            raw["positioning"] = {"value": desc[:200], "scope": Scope.DECLARADO}

        # --- LOGO / og:image ---
        og = page.evaluate(
            "() => (document.querySelector('meta[property=\"og:image\"]')||{}).content || ''"
        )
        if og:
            raw["logo"] = {"value": og, "scope": Scope.DECLARADO}

        # --- SCREENSHOT -> paleta via CV ---
        try:
            shot = page.screenshot(full_page=False)
            raw["image_palettes"] = [palette_from_image_bytes(shot)]
        except Exception:
            pass

        browser.close()

    status = "ok" if raw else "partial"
    return RawBundle("site", status, raw=raw,
                     detail=f"Playwright render OK ({len(raw)} sinais de {url}).")


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
    Upload (logo/doc): paleta EXATA por CV (Pillow).
    Cada item de `files` pode trazer:
      - {"b64": "<base64 da imagem>"}  -> extrai paleta real
      - {"path": "/caminho/arquivo"}   -> extrai paleta real (uso local)
      - {"palette": [[(rgb,peso),...]]} -> paleta já pronta (mock/teste)
    Sem nenhum desses, devolve um mock coerente para o fluxo rodar.

    # TODO[REAL]: parse de PDF/DOCX (pypdf) para extrair copy de brand guides.
    """
    files = files or []
    palettes: list[list[tuple[RGB, float]]] = []
    real_count = 0
    errors: list[str] = []

    for f in files:
        try:
            if "palette" in f:
                palettes.append([tuple(c) if not isinstance(c, tuple) else c
                                 for c in f["palette"]])
            elif "b64" in f:
                palettes.append(palette_from_image_bytes(base64.b64decode(f["b64"])))
                real_count += 1
            elif "path" in f:
                with open(f["path"], "rb") as fh:
                    palettes.append(palette_from_image_bytes(fh.read()))
                real_count += 1
        except Exception as exc:  # arquivo inválido / Pillow ausente
            errors.append(f"{type(exc).__name__}: {exc}")

    if not palettes:
        # mock default: um logo institucional
        palettes = [[((22, 54, 122), 0.7), ((212, 175, 55), 0.3)]]
        detail = "MOCK — paleta de exemplo (nenhum arquivo real enviado)."
        logo_rgb = (212, 175, 55)
    else:
        detail = (f"CV real em {real_count} arquivo(s)." if real_count
                  else "paleta(s) fornecida(s).")
        if errors:
            detail += f" Falhas: {'; '.join(errors)}"
        # cor do logo = cor mais proeminente do 1º arquivo (escopo CV)
        logo_rgb = max(palettes[0], key=lambda c: c[1])[0]

    return RawBundle(
        source="upload",
        access_status="ok",
        detail=detail,
        raw={"image_palettes": palettes,
             "color_logo": {"value": tuple(logo_rgb), "scope": Scope.CV}},
    )


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


def build_nodes(bundles: list[RawBundle]) -> dict[str, Node]:
    """
    Monta os nós: valor (via reconcile) + SINAIS observáveis. NÃO calcula
    confiança (scorer faz) e NÃO injeta impacto (motor faz).
    """
    usable = [b for b in bundles if b.access_status in ("ok", "partial")]
    nodes: dict[str, Node] = {}

    for d in ATTR_DEFS:
        # 1) colher proveniência (cor tem caminho próprio via CV)
        cv = None
        if d.is_color and d.id == "primary_color":
            prov, cv = _color_provenance(usable)
        elif d.id == "secondary_color":
            prov = []
            for b in usable:
                logo = b.raw.get("color_logo")
                if logo:
                    prov.append(Provenance(b.source, logo["scope"], _hex(logo["value"]),
                                           b.access_status, b.detail))
        else:
            prov = _gather_provenance(d.id, usable)

        # 2) reconciliar conflito por grupo
        res = reconcile(d.group, prov)

        # 3) montar SINAIS observáveis
        sig = Signals()
        if prov:
            ceil = max(SCOPE_CEILING.get(p.scope, 0.6) for p in prov
                       if p.access_status in ("ok", "partial"))
            sig.ceiling = ceil
            sig.agreement = res["agreement"]
            sig.coverage = min(1.0, len(prov) / d.expected_sources)
            if cv is not None:
                # cor: dispersão e cobertura vêm do cluster perceptual
                sig.dispersion = cv.dispersion
                sig.coverage = max(sig.coverage, cv.coverage)
            else:
                # textual: dispersão = fração de valores distintos
                distinct = len({str(p.value).strip().lower() for p in prov})
                sig.dispersion = 0.0 if len(prov) <= 1 else (distinct - 1) / len(prov)
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
        nodes[d.id] = node

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
