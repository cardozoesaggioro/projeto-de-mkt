# -*- coding: utf-8 -*-
"""
catalog.py — A BASE da análise: quais campos existem e quais RESPOSTAS são
possíveis em cada um.

Princípio de UX (reconhecimento, não evocação): toda pergunta vira OPÇÕES
CLICÁVEIS. Aqui definimos, por campo:
  - o TIPO (cor / categórico / aberto / lista / fonte),
  - o CATÁLOGO de opções possíveis (vocabulário controlado), quando faz sentido.

A montagem das opções de uma pergunta = palpite do sistema + o que foi visto
nas fontes + o catálogo. O `value` é o que o brand book guarda; o `label` é o
que o humano lê.
"""
from __future__ import annotations

from typing import Any

import archetypes

# Tipo de cada campo do brand book.
#   cor       -> swatch + hex custom
#   categorico-> lista fechada de opções (vocabulário controlado)
#   aberto    -> texto livre (com palpite + alternativas)
#   lista     -> várias tags (texto livre separado por vírgula)
#   fonte     -> tipografia (palpite + fontes comuns + livre)
FIELD_TYPE: dict[str, str] = {
    "primary_color": "cor",
    "secondary_color": "cor",
    "color_palette": "lista",     # paleta completa (várias cores)
    "typography": "fonte",        # fonte de títulos/display
    "body_font": "fonte",         # fonte de corpo/texto
    "logo": "aberto",
    "tone_of_voice": "categorico",
    "tagline": "aberto",
    "vocabulary": "lista",
    "cta_padrao": "categorico",   # chamada para ação padrão
    "hashtags": "lista",
    "pillars": "lista",
    "positioning": "aberto",
    "audience": "categorico",
    "price_tier": "categorico",   # faixa de preço/posicionamento de valor
    "competitors": "lista",
    "archetype": "categorico",
}


def _opt(value: str, label: str, hint: str = "") -> dict[str, str]:
    return {"value": value, "label": label, "hint": hint}


# --- vocabulários controlados (as RESPOSTAS possíveis) ---------------------
TONE_OPTIONS = [
    _opt("Autoridade confiável", "Autoridade confiável", "segurança e expertise"),
    _opt("Próximo e acolhedor", "Próximo e acolhedor", "humano, fala perto"),
    _opt("Analítico e fundamentado", "Analítico e fundamentado", "dados e evidência"),
    _opt("Energético e direto", "Energético e direto", "ritmo rápido, ação"),
    _opt("Sofisticado e premium", "Sofisticado e premium", "requinte, exclusividade"),
    _opt("Divertido e leve", "Divertido e leve", "bom humor, descontraído"),
    _opt("Inspirador e visionário", "Inspirador e visionário", "futuro e propósito"),
    _opt("Técnico e preciso", "Técnico e preciso", "linguagem especializada"),
    _opt("Empático e humano", "Empático e humano", "acolhe a dor do público"),
    _opt("Ousado e provocador", "Ousado e provocador", "quebra padrões"),
]

AUDIENCE_OPTIONS = [
    _opt("Decisores B2B / empresas", "Decisores B2B / empresas", "compra corporativa"),
    _opt("Pequenas e médias empresas", "Pequenas e médias empresas", "PMEs"),
    _opt("Consumidor final (B2C)", "Consumidor final (B2C)", "pessoa física"),
    _opt("Profissionais especializados", "Profissionais especializados", "nicho técnico"),
    _opt("Público jovem (Gen Z)", "Público jovem (Gen Z)", "18-27 anos"),
    _opt("Alta renda / premium", "Alta renda / premium", "ticket alto"),
    _opt("Setor público", "Setor público", "governo/licitações"),
    _opt("Startups e tecnologia", "Startups e tecnologia", "early adopters"),
]

# Pilares de conteúdo sugeridos (lista — o humano pode escolher vários/ajustar).
PILLAR_SUGGESTIONS = [
    "Educação / conteúdo de valor", "Bastidores e cultura", "Provas sociais / cases",
    "Produto e serviço", "Tendências do setor", "Autoridade e dados",
    "Comunidade e engajamento", "Ofertas e promoções",
]

# Fontes comuns para sugerir na tipografia.
FONT_SUGGESTIONS = [
    "Inter", "Poppins", "Montserrat", "Roboto", "Open Sans", "Lato",
    "Playfair Display", "Merriweather",
]

# CTA padrão (chamada para ação).
CTA_OPTIONS = [
    _opt("Fale conosco", "Fale conosco", "contato/atendimento"),
    _opt("Saiba mais", "Saiba mais", "conteúdo/educação"),
    _opt("Solicite um orçamento", "Solicite um orçamento", "venda consultiva"),
    _opt("Agende uma conversa", "Agende uma conversa", "reunião/consulta"),
    _opt("Compre agora", "Compre agora", "e-commerce"),
    _opt("Cadastre-se", "Cadastre-se", "lead/newsletter"),
    _opt("Baixe o material", "Baixe o material", "isca digital"),
    _opt("Assine agora", "Assine agora", "recorrência"),
]

# Faixa de preço / posicionamento de valor.
PRICE_OPTIONS = [
    _opt("Econômico", "Econômico", "menor preço"),
    _opt("Acessível", "Acessível", "bom custo-benefício"),
    _opt("Intermediário", "Intermediário", "valor médio de mercado"),
    _opt("Premium", "Premium", "acima da média, diferenciado"),
    _opt("Luxo", "Luxo", "alto padrão/exclusivo"),
]

CATALOG: dict[str, list[dict[str, str]]] = {
    "tone_of_voice": TONE_OPTIONS,
    "audience": AUDIENCE_OPTIONS,
    "archetype": archetypes.as_options_norm() if hasattr(archetypes, "as_options_norm") else [],
    "pillars": [_opt(p, p, "pilar sugerido") for p in PILLAR_SUGGESTIONS],
    "typography": [_opt(f, f, "fonte comum") for f in FONT_SUGGESTIONS],
    "body_font": [_opt(f, f, "fonte comum") for f in FONT_SUGGESTIONS],
    "cta_padrao": CTA_OPTIONS,
    "price_tier": PRICE_OPTIONS,
}


def display_label(field_id: str, value: Any) -> str:
    """Rótulo humano de um valor (ex.: arquétipo 'sabio' -> 'O Sábio')."""
    if value in (None, "", []):
        return ""
    if field_id == "archetype":
        a = archetypes.get(str(value))
        return a.nome if a else str(value)
    if isinstance(value, list):
        return " · ".join(str(v) for v in value)
    return str(value)


def build_options(field_id: str, guess: Any,
                  extracted: list[Any] | None = None) -> dict[str, Any]:
    """
    Monta as opções clicáveis de uma pergunta:
      palpite do sistema -> alternativas vistas nas fontes -> catálogo.
    Retorna {tipo, opcoes:[{value,label,hint,kind}], palpite_label}.
    """
    tipo = FIELD_TYPE.get(field_id, "aberto")
    opcoes: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(value: Any, label: str, hint: str, kind: str) -> None:
        if not label:
            return
        key = label.strip().lower()
        if key in seen:
            return
        seen.add(key)
        opcoes.append({"value": value, "label": label, "hint": hint, "kind": kind})

    # 1) palpite do sistema
    palpite_label = display_label(field_id, guess)
    if palpite_label:
        add(guess, palpite_label, "palpite do sistema", "palpite")

    # 2) alternativas vistas nas fontes (só valores simples; ignora objetos)
    for a in (extracted or []):
        if isinstance(a, (dict, list)):
            continue
        add(a, display_label(field_id, a), "visto nas fontes", "fonte")

    # 3) catálogo (vocabulário controlado / sugestões)
    for o in CATALOG.get(field_id, []):
        add(o["value"], o["label"], o.get("hint", ""), "catalogo")

    return {"tipo": tipo, "opcoes": opcoes, "palpite_label": palpite_label}
