# -*- coding: utf-8 -*-
"""
reconcile.py — Resolve conflito de evidências POR GRUPO.

Regras fiéis à especificação:
  - Grupo VISUAL: a realidade renderizada manda. O medido (deterministico/CV)
    VENCE a fonte oficial declarada. Ex.: o brand guide diz #0000FF, mas o site
    renderiza #1A3AFF -> confiamos no que o olho vê.
  - Grupos VERBAL e ESTRATEGIA: o DECLARADO vence a inferência recente.
    Ex.: a bio oficial define o tom melhor do que uma frase inferida por LLM.

`agreement` = fração de fontes que concordam com o valor VENCEDOR. Esse número
vai direto para o scorer (não é inventado).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from schema import Group, Provenance, Scope


# Prioridade de escopo por grupo (maior = vence). Esta tabela É a regra.
_PRIORITY: dict[Group, dict[Scope, int]] = {
    # Visual: o medido vence o declarado.
    Group.VISUAL: {
        Scope.DETERMINISTICO: 4,  # CSS computado, pixel exato
        Scope.CV: 3,              # cluster de cor
        Scope.DECLARADO: 2,       # brand guide / og:image
        Scope.INFERENCIA: 1,
    },
    # Verbal: o declarado vence a inferência recente.
    Group.VERBAL: {
        Scope.DECLARADO: 4,
        Scope.DETERMINISTICO: 3,
        Scope.CV: 2,
        Scope.INFERENCIA: 1,
    },
    # Estratégia: idem verbal — o declarado manda.
    Group.ESTRATEGIA: {
        Scope.DECLARADO: 4,
        Scope.DETERMINISTICO: 3,
        Scope.CV: 2,
        Scope.INFERENCIA: 1,
    },
    Group.META: {
        Scope.DECLARADO: 4, Scope.DETERMINISTICO: 3, Scope.CV: 2, Scope.INFERENCIA: 1,
    },
}


def _norm(v: Any) -> Any:
    """
    Normaliza valor para comparar concordância e ser hasheável (vai p/ Counter).
    Strings: strip+lower. Listas/tuplas: tupla normalizada (ordem ignorada).
    """
    if isinstance(v, str):
        return v.strip().lower()
    if isinstance(v, (list, tuple)):
        return tuple(sorted(_norm(x) for x in v))
    return v


def reconcile(group: Group, provenance: list[Provenance]) -> dict[str, Any]:
    """
    Recebe as evidências (proveniência) de um atributo e devolve:
      {value, scope, agreement, winner_source}
    Só considera fontes que conseguiram ler (access_status == 'ok'/'partial').
    """
    usable = [p for p in provenance if p.access_status in ("ok", "partial")]
    if not usable:
        return {"value": None, "scope": Scope.INFERENCIA, "agreement": 0.0,
                "winner_source": None}

    prio = _PRIORITY.get(group, _PRIORITY[Group.ESTRATEGIA])

    # Vencedor = maior prioridade de escopo; desempate por valor mais frequente.
    freq = Counter(_norm(p.value) for p in usable)
    winner = max(
        usable,
        key=lambda p: (prio.get(p.scope, 0), freq[_norm(p.value)]),
    )

    # agreement = concordância com o vencedor, COM SUAVIZAÇÃO (Laplace).
    # Sem suavização, 1 fonte daria 1.0 ("concorda consigo mesma") — corroboração
    # falsa. Com prior 0.5 e pseudo-contagem 1: (matches+0.5)/(n+1).
    #   n=1, match  -> 0.75   |  n=2 concordam -> 0.83  |  n=3 -> 0.875
    #   n=2 discordam (1)     -> 0.50
    agree = sum(1 for p in usable if _norm(p.value) == _norm(winner.value))
    agreement = (agree + 0.5) / (len(usable) + 1)

    return {
        "value": winner.value,
        "scope": winner.scope,
        "agreement": round(agreement, 4),
        "winner_source": winner.source,
        "raw_agreement": round(agree / len(usable), 4),  # sem suavização (debug)
        "n_sources": len(usable),
    }
