# -*- coding: utf-8 -*-
"""
manual.py — Gera o MANUAL DE MARCA completo (entregável final).

Monta um documento HTML profissional a partir do brand book confirmado:
identidade visual (cores/tipografia/logo), verbal (tom/tagline/vocabulário),
estratégia (pilares/público/arquétipo/posicionamento), e um PARECER analítico
(coerência, forças, lacunas, recomendações) escrito pela IA.

A prosa (textos corridos) vem do LLM; os VALORES vêm do brand book confirmado.
O documento é estilizado nas próprias cores/fontes da marca e é
imprimível/salvável em PDF pelo navegador.
"""
from __future__ import annotations

import html as _html
from typing import Any

import archetypes


def _esc(v: Any) -> str:
    return _html.escape(str(v)) if v is not None else ""


def _is_hex(v: Any) -> bool:
    return isinstance(v, str) and len(v) == 7 and v.startswith("#")


def render_manual(name: str, payload: dict[str, Any], prose: dict[str, Any]) -> str:
    """Monta o HTML do manual. `payload` = brandbook_payload; `prose` = textos do LLM."""
    nodes = payload.get("nodes", [])
    by_id = {n["id"]: n for n in nodes}

    def val(nid: str, default: Any = None) -> Any:
        n = by_id.get(nid)
        return n["value"] if n and n.get("value") not in (None, "", []) else default

    def conf(nid: str) -> int:
        n = by_id.get(nid)
        return int(round((n.get("confidence", 0) if n else 0) * 100))

    primary = val("primary_color", "#1F2937") or "#1F2937"
    secondary = val("secondary_color", "#D4AF37") or "#D4AF37"
    typo = val("typography", "Inter") or "Inter"
    body_font = val("body_font", "Inter") or "Inter"
    palette = val("color_palette", []) or []
    logo = val("logo")
    tagline = val("tagline", name)
    positioning = val("positioning", "")
    audience = val("audience", "")
    archetype_id = val("archetype", "")
    arq = archetypes.get(str(archetype_id)) if archetype_id else None
    tone = val("tone_of_voice", "")
    pillars = val("pillars", []) or []
    vocab = val("vocabulary", []) or []
    cta = val("cta_padrao", "")
    hashtags = val("hashtags", []) or []
    price = val("price_tier", "")
    competitors = val("competitors", []) or []
    style_refs = payload.get("style_references", []) or []
    sample = (payload.get("sample_slide") or {}).get("html", "")

    # ---- helpers de bloco ----
    def swatch(hexv: str, label: str = "") -> str:
        if not _is_hex(hexv):
            return ""
        return (f'<div class="sw"><div class="chip" style="background:{_esc(hexv)}"></div>'
                f'<div><b>{_esc(hexv)}</b>{("<br><span class=mut>"+_esc(label)+"</span>") if label else ""}</div></div>')

    def chips(items: list, cls: str = "tag") -> str:
        return "".join(f'<span class="{cls}">{_esc(i)}</span>' for i in items) or '<span class="mut">—</span>'

    def confbadge(nid: str) -> str:
        c = conf(nid)
        cls = "ok" if c >= 70 else "warn" if c >= 40 else "low"
        return f'<span class="cb {cls}">{c}%</span>'

    def li(items: list) -> str:
        return "".join(f"<li>{_esc(i)}</li>" for i in items)

    # ---- parecer / prosa ----
    sobre = _esc(prose.get("sobre", positioning or ""))
    voz = prose.get("voz_diretrizes", {}) or {}
    estrategia = _esc(prose.get("estrategia", ""))
    parecer = _esc(prose.get("parecer", ""))
    recs = prose.get("recomendacoes", []) or []

    palette_html = "".join(swatch(c) for c in palette if _is_hex(c)) or '<span class="mut">—</span>'
    refs_html = "".join(
        f'<div class="ref"><b>{_esc(r.get("resumo","Referência"))}</b>'
        f'<div class="mut">estrutura: {_esc(r.get("estrutura",""))} · cor: {_esc(r.get("uso_de_cor",""))} '
        f'· tipografia: {_esc(r.get("tipografia",""))} · formato: {_esc(r.get("formato",""))}</div></div>'
        for r in style_refs) or '<span class="mut">Nenhuma referência cadastrada.</span>'

    # tabela de proveniência/confiança (anexo)
    rows = ""
    for n in nodes:
        v = n.get("value")
        v = ", ".join(map(str, v)) if isinstance(v, list) else (v if v is not None else "—")
        sw = f'<span class="chip sm" style="background:{_esc(n["value"])}"></span>' if _is_hex(n.get("value")) else ""
        srcs = ", ".join(sorted({p.get("source", "") for p in n.get("provenance", [])})) or "—"
        rows += (f'<tr><td>{_esc(n["label"])}</td><td>{sw}{_esc(v)}</td>'
                 f'<td>{_esc(n.get("status",""))}</td><td>{confbadge(n["id"])}</td>'
                 f'<td class="mut">{_esc(srcs)}</td></tr>')

    logo_block = (f'<img src="{_esc(logo)}" alt="logo" style="max-height:90px;max-width:280px">'
                  if logo and str(logo).startswith("http") else '<span class="mut">logo não capturado</span>')

    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<title>Manual de Marca — {_esc(name)}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter&family=Poppins&family=Montserrat&family=Roboto&family=Open+Sans&family=Lato&family=Playfair+Display&family=Merriweather&display=swap" rel="stylesheet">
<style>
  :root{{--p:{_esc(primary)};--s:{_esc(secondary)}}}
  *{{box-sizing:border-box}}
  body{{margin:0;font-family:'{_esc(body_font)}',Inter,Arial,sans-serif;color:#1a1a1a;background:#fff;line-height:1.6}}
  .page{{max-width:880px;margin:0 auto;padding:48px 56px}}
  h1,h2,h3{{font-family:'{_esc(typo)}','{_esc(body_font)}',serif;color:var(--p);line-height:1.2}}
  h1{{font-size:42px;margin:0}} h2{{font-size:26px;margin:38px 0 12px;border-bottom:3px solid var(--s);padding-bottom:6px}}
  h3{{font-size:18px;margin:18px 0 6px}}
  .cover{{background:var(--p);color:#fff;padding:90px 56px;border-radius:0}}
  .cover h1{{color:#fff}} .cover .kick{{color:var(--s);letter-spacing:.2em;text-transform:uppercase;font-weight:700}}
  .cover .tag{{font-size:20px;opacity:.92;margin-top:14px}}
  .mut{{color:#6b7280}} .grid{{display:grid;grid-template-columns:1fr 1fr;gap:18px}}
  .card{{border:1px solid #e5e7eb;border-radius:12px;padding:16px}}
  .sw{{display:flex;align-items:center;gap:10px;margin:8px 0}}
  .chip{{width:42px;height:42px;border-radius:8px;border:1px solid #0002}} .chip.sm{{width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:6px}}
  .tag{{display:inline-block;background:#f3f4f6;border-radius:99px;padding:4px 12px;margin:3px;font-size:14px}}
  .cb{{font-size:12px;padding:2px 8px;border-radius:99px;color:#fff}} .cb.ok{{background:#2ea043}} .cb.warn{{background:#d29922}} .cb.low{{background:#da3633}}
  table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}}
  td,th{{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top}}
  .fontsample{{font-size:30px;margin:6px 0}} .do{{color:#15803d}} .dont{{color:#b91c1c}}
  .ref{{border-left:3px solid var(--s);padding:6px 12px;margin:8px 0}}
  .slidebox{{width:300px;height:300px;border-radius:12px;overflow:hidden;border:1px solid #ddd}}
  @media print{{.page,.cover{{padding:24px}} h2{{page-break-after:avoid}} .card,.ref,table{{page-break-inside:avoid}}}}
</style></head><body>

<div class="cover">
  <div class="kick">Manual de Marca · Ponto Zero</div>
  <h1>{_esc(name)}</h1>
  <div class="tag">{_esc(tagline)}</div>
</div>

<div class="page">
  <h2>1. Sobre a marca</h2>
  <p>{sobre or '<span class=mut>—</span>'}</p>
  <div class="grid">
    <div class="card"><h3>Posicionamento {confbadge('positioning')}</h3><p>{_esc(positioning) or '—'}</p></div>
    <div class="card"><h3>Público-alvo {confbadge('audience')}</h3><p>{_esc(audience) or '—'}</p></div>
  </div>

  <h2>2. Personalidade & Arquétipo</h2>
  <div class="card">
    <h3>{_esc(arq.nome) if arq else 'Arquétipo'} {confbadge('archetype')}</h3>
    <p>{_esc(arq.promessa) if arq else '—'}{(' — <i>“'+_esc(arq.tom_exemplo)+'”</i>') if arq else ''}</p>
  </div>

  <h2>3. Tom de voz & Linguagem</h2>
  <div class="card">
    <h3>Tom: {_esc(tone) or '—'} {confbadge('tone_of_voice')}</h3>
    <p>{_esc(voz.get('resumo',''))}</p>
    <div class="grid">
      <div><h3 class="do">✓ Fazer</h3><ul>{li(voz.get('fazer',[]))}</ul></div>
      <div><h3 class="dont">✗ Evitar</h3><ul>{li(voz.get('evitar',[]))}</ul></div>
    </div>
    <h3>Vocabulário da marca</h3>{chips(vocab)}
  </div>

  <h2>4. Estratégia de conteúdo</h2>
  <p>{estrategia or '<span class=mut>—</span>'}</p>
  <h3>Pilares {confbadge('pillars')}</h3>{chips(pillars)}
  <div class="grid" style="margin-top:14px">
    <div class="card"><h3>CTA padrão {confbadge('cta_padrao')}</h3><p>{_esc(cta) or '—'}</p></div>
    <div class="card"><h3>Hashtags</h3>{chips(hashtags)}</div>
  </div>

  <h2>5. Identidade visual</h2>
  <div class="grid">
    <div class="card"><h3>Cor primária {confbadge('primary_color')}</h3>{swatch(primary)}</div>
    <div class="card"><h3>Cor secundária {confbadge('secondary_color')}</h3>{swatch(secondary)}</div>
  </div>
  <h3>Paleta completa</h3><div style="display:flex;flex-wrap:wrap;gap:14px">{palette_html}</div>
  <div class="grid" style="margin-top:14px">
    <div class="card"><h3>Tipografia — títulos {confbadge('typography')}</h3>
      <div class="fontsample" style="font-family:'{_esc(typo)}',serif">{_esc(typo)}</div>
      <div style="font-family:'{_esc(typo)}',serif">AaBbCc 0123 — A marca decide.</div></div>
    <div class="card"><h3>Tipografia — corpo {confbadge('body_font')}</h3>
      <div class="fontsample" style="font-family:'{_esc(body_font)}',sans-serif">{_esc(body_font)}</div>
      <div style="font-family:'{_esc(body_font)}',sans-serif">AaBbCc 0123 — texto de leitura.</div></div>
  </div>
  <h3>Logo</h3><div class="card">{logo_block}</div>

  <h2>6. Mercado & valor</h2>
  <div class="grid">
    <div class="card"><h3>Faixa de preço {confbadge('price_tier')}</h3><p>{_esc(price) or '—'}</p></div>
    <div class="card"><h3>Concorrentes</h3>{chips(competitors)}</div>
  </div>

  <h2>7. Referências de estilo (carrossel)</h2>
  {refs_html}

  <h2>8. Amostra aplicada</h2>
  <div class="slidebox"><iframe srcdoc="{_esc(sample)}" style="border:0;width:1080px;height:1080px;transform:scale(0.2778);transform-origin:top left"></iframe></div>

  <h2>9. Parecer & recomendações</h2>
  <p>{parecer or '<span class=mut>—</span>'}</p>
  <ul>{li(recs)}</ul>

  <h2>Anexo · Proveniência e confiança</h2>
  <p class="mut">Cada atributo, sua origem (fontes) e o nível de confiança calculado pelos sinais.</p>
  <table><tr><th>Atributo</th><th>Valor</th><th>Status</th><th>Confiança</th><th>Fontes</th></tr>{rows}</table>
  <p class="mut" style="margin-top:24px">Gerado pelo Ponto Zero · {_esc(payload.get('generated_at',''))}</p>
</div>
</body></html>"""
