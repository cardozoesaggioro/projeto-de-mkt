# -*- coding: utf-8 -*-
"""
carousel.py — O fim do funil: gera o carrossel a partir do brand book.

Este é o CONSUMIDOR do brand book — o mesmo "mapa de consumo" que injeta o
`impact` no motor. Ele pega os valores confirmados (cor, tom, tipografia,
pilares, tagline, posicionamento) e monta slides 1080x1080 prontos pro Instagram.

Divisão:
  - a COPY (texto dos slides) vem do LLM quando há chave (consciente do tom),
    senão de moldes determinísticos. O LLM dá o texto; nunca a confiança.
  - o LAYOUT é determinístico: usa as cores/tipografia exatas da marca.
  - o RENDER para PNG reaproveita o Playwright (export pronto pra publicar).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Slide:
    role: str          # cover | pillar | cta
    headline: str
    body: str
    html: str = ""
    kicker: str = ""


def _v(values: dict[str, Any], key: str, default: Any = None) -> Any:
    val = values.get(key)
    return val if val not in (None, "", []) else default


# ---------------------------------------------------------------------------
# COPY — texto dos slides
# ---------------------------------------------------------------------------
def _copy_via_llm(values: dict[str, Any], topic: str, n: int,
                  llm_call: Callable[..., str | None]) -> list[dict[str, str]] | None:
    """Pede ao LLM a copy dos slides, no tom da marca. Retorna None se falhar."""
    ctx = {
        "tema": topic,
        "tom": _v(values, "tone_of_voice", "profissional"),
        "pilares": _v(values, "pillars", []),
        "posicionamento": _v(values, "positioning", ""),
        "publico": _v(values, "audience", ""),
        "tagline": _v(values, "tagline", ""),
    }
    prompt = (
        f"Você é redator de social media. Crie a copy de um carrossel de {n} slides "
        f"para Instagram sobre o tema \"{topic}\", no tom \"{ctx['tom']}\", para o "
        f"público \"{ctx['publico']}\". Contexto da marca: {json.dumps(ctx, ensure_ascii=False)}.\n"
        f"Responda APENAS um JSON: uma lista de {n} objetos "
        f'{{"headline": "...", "body": "..."}}. O 1º slide é capa (gancho forte), '
        f"os do meio desenvolvem os pilares, o último é CTA. headline curta (<=60 "
        f"chars), body <=140 chars."
    )
    raw = llm_call(prompt, system="Responda só JSON válido, em pt-BR.", max_tokens=700)
    if not raw:
        return None
    try:
        # tolera cercas de código ```json ... ```
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
        if isinstance(data, list) and data:
            return [{"headline": str(s.get("headline", "")), "body": str(s.get("body", ""))}
                    for s in data][:n]
    except Exception:
        return None
    return None


def _copy_heuristic(values: dict[str, Any], topic: str, n: int) -> list[dict[str, str]]:
    """Copy determinística a partir de pilares/tagline — sem LLM."""
    pillars = list(_v(values, "pillars", []) or [])
    tagline = _v(values, "tagline", "")
    positioning = _v(values, "positioning", "")
    slides = [{"headline": topic or (tagline or "Sua marca em foco"),
               "body": positioning or "Um carrossel construído com a identidade da sua marca."}]
    for p in pillars:
        slides.append({"headline": str(p).capitalize(),
                       "body": f"Como {str(p).lower()} aparece na prática para {_v(values,'audience','seu público')}."})
    slides.append({"headline": "Vamos conversar?",
                   "body": tagline or "Fale com a gente e leve isso para a sua marca."})
    # ajusta ao número pedido
    if len(slides) > n:
        slides = [slides[0]] + slides[1:n-1] + [slides[-1]]
    while len(slides) < n:
        slides.insert(-1, {"headline": "Mais um ponto", "body": "Conteúdo alinhado à sua identidade."})
    return slides[:n]


# ---------------------------------------------------------------------------
# LAYOUT — HTML do slide (1080x1080), cores/tipografia da marca
# ---------------------------------------------------------------------------
def _slide_html(values: dict[str, Any], s: Slide, idx: int, total: int) -> str:
    primary = _v(values, "primary_color", "#16367A")
    secondary = _v(values, "secondary_color", "#D4AF37")
    typo = _v(values, "typography", "Inter")
    is_cover = s.role == "cover"
    is_cta = s.role == "cta"

    bg = primary if (is_cover or is_cta) else "#ffffff"
    fg = "#ffffff" if (is_cover or is_cta) else "#111111"
    accent = secondary

    kicker = s.kicker or (f"{idx}/{total}" if not is_cover else "Ponto Zero")
    cta_btn = (f'<div style="margin-top:28px;display:inline-block;background:{accent};'
               f'color:#111;font-weight:700;padding:16px 28px;border-radius:12px;'
               f'font-size:28px">{s.headline}</div>') if is_cta else ""

    headline_html = "" if is_cta else (
        f'<div style="font-size:{72 if is_cover else 60}px;font-weight:800;'
        f'line-height:1.05;color:{fg}">{s.headline}</div>')

    return f"""
<div style="width:1080px;height:1080px;background:{bg};color:{fg};
            font-family:'{typo}',Inter,Arial,sans-serif;box-sizing:border-box;
            padding:110px 96px;display:flex;flex-direction:column;
            justify-content:space-between;position:relative">
  <div style="font-size:26px;letter-spacing:.22em;text-transform:uppercase;
              color:{accent};font-weight:700">{kicker}</div>
  <div>
    {headline_html}
    <div style="margin-top:24px;font-size:34px;line-height:1.35;
                color:{fg};opacity:.92">{s.body}</div>
    {cta_btn}
  </div>
  <div style="height:10px;width:{int((idx)/total*100)}%;background:{accent};
              border-radius:99px"></div>
</div>"""


# ---------------------------------------------------------------------------
# API pública do módulo
# ---------------------------------------------------------------------------
def build_carousel(values: dict[str, Any], topic: str, n_slides: int = 5,
                   llm_call: Callable[..., str | None] | None = None) -> dict[str, Any]:
    """
    Monta o carrossel. `values` = dict {id_atributo: valor} do brand book.
    `llm_call` (opcional) = função do proxy de LLM do servidor.
    """
    n_slides = max(3, min(10, n_slides))
    copy = (_copy_via_llm(values, topic, n_slides, llm_call) if llm_call else None)
    source = "llm" if copy else "heuristica"
    if not copy:
        copy = _copy_heuristic(values, topic, n_slides)

    slides: list[Slide] = []
    for i, c in enumerate(copy):
        role = "cover" if i == 0 else ("cta" if i == len(copy) - 1 else "pillar")
        slides.append(Slide(role=role, headline=c["headline"], body=c["body"]))
    for i, s in enumerate(slides, start=1):
        s.html = _slide_html(values, s, i, len(slides))

    return {
        "topic": topic,
        "copy_source": source,
        "n_slides": len(slides),
        "slides": [{"role": s.role, "headline": s.headline, "body": s.body, "html": s.html}
                   for s in slides],
    }


def render_html_to_png(html: str, size: int = 1080) -> bytes:
    """Renderiza o HTML de um slide para PNG (1080x1080) via Playwright."""
    from playwright.sync_api import sync_playwright

    page_html = f"<!doctype html><html><body style='margin:0'>{html}</body></html>"
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": size, "height": size})
        page.set_content(page_html, wait_until="networkidle")
        png = page.screenshot(clip={"x": 0, "y": 0, "width": size, "height": size})
        browser.close()
    return png
