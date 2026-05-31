# -*- coding: utf-8 -*-
"""
schema.py — O nó atômico do brand book.

A ALMA do sistema: TODO atributo da marca é um nó com a mesma forma.
Princípios fiéis à especificação:
  - `impact` NÃO nasce na extração. Ele é INJETADO pelo mapa de consumo do
    gerador (ver motor/CONSUMPTION_MAP). Aqui ele começa em 0.0.
  - `confidence` NÃO é emitida por extrator nenhum. É montada só pelo scorer
    a partir dos `signals` observáveis. Aqui começa em 0.0.
  - O objeto é MONOTÔNICO: um valor confirmado pelo humano é "pegajoso"
    (sticky) e não pode ser atropelado por uma re-extração.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Enums de domínio
# ---------------------------------------------------------------------------
class Group(str, Enum):
    """Grupo de reconciliação. As regras de quem-vence dependem dele."""
    VISUAL = "Visual"        # cor, tipografia, logo (a realidade renderizada manda)
    VERBAL = "Verbal"        # tom, tagline, vocabulário (o declarado manda)
    ESTRATEGIA = "Estrategia"  # pilares, posicionamento, arquétipo, público
    META = "Meta"            # postura — repondera tudo, perguntada primeiro


class Scope(str, Enum):
    """De onde a evidência veio — usado pela reconciliação."""
    DECLARADO = "declarado"           # a marca declarou (brand guide, bio)
    DETERMINISTICO = "deterministico"  # medido sem ambiguidade (CSS computado)
    CV = "cv"                         # visão computacional (cluster de cor)
    INFERENCIA = "inferencia"         # inferido por LLM/heurística (recente, fraco)


class Status(str, Enum):
    PALPITE = "palpite"        # hipótese do sistema, ainda não validada
    CONFIRMADO = "confirmado"  # humano confirmou — PEGAJOSO, monotônico
    CORRIGIDO = "corrigido"    # humano corrigiu — também pegajoso
    AUSENTE = "ausente"        # sem dado -> vira pergunta de arquétipo


# ---------------------------------------------------------------------------
# Sinais observáveis (a única matéria-prima da confiança)
# ---------------------------------------------------------------------------
@dataclass
class Signals:
    """
    Os quatro sinais que o scorer combina. TODOS observáveis do corpus,
    nenhum é auto-relato de modelo.
      - ceiling:    teto de confiança que a melhor fonte permite (0..1)
      - dispersion: o quanto as evidências divergem (0=coesas, 1=lama)
      - agreement:  fração de fontes que concordam com o valor vencedor (0..1)
      - coverage:   fração das fontes esperadas que de fato responderam (0..1)
    """
    ceiling: float = 0.0
    dispersion: float = 0.0
    agreement: float = 0.0
    coverage: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass
class Provenance:
    """Um registro de evidência: de onde veio, o que dizia, e se deu pra ler."""
    source: str                     # ex.: "site", "instagram", "upload:logo.png"
    scope: Scope                    # declarado / deterministico / cv / inferencia
    value: Any                      # o que essa fonte afirmou
    access_status: str = "ok"       # ok | partial | blocked | unauthorized
    detail: str = ""                # nota honesta (ex.: "robots.txt bloqueou /sobre")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["scope"] = self.scope.value
        return d


# ---------------------------------------------------------------------------
# O nó
# ---------------------------------------------------------------------------
@dataclass
class Node:
    """Um atributo da marca. Mesma forma para cor, tom, pilar, arquétipo..."""
    id: str
    label: str
    group: Group
    scope: Scope = Scope.INFERENCIA
    value: Any = None
    status: Status = Status.PALPITE
    signals: Signals = field(default_factory=Signals)
    provenance: list[Provenance] = field(default_factory=list)
    alternatives: list[Any] = field(default_factory=list)
    anchor: bool = False                 # confirmar uma âncora propaga coerência
    propagates: list[str] = field(default_factory=list)  # ids afetados
    impact: float = 0.0                  # INJETADO pelo mapa de consumo, não aqui
    confidence: float = 0.0              # MONTADA só pelo scorer

    # --- monotonicidade -----------------------------------------------------
    @property
    def is_sticky(self) -> bool:
        """Valor tocado pelo humano não é atropelado por re-extração."""
        return self.status in (Status.CONFIRMADO, Status.CORRIGIDO)

    def confirm(self, value: Any | None = None) -> None:
        """Humano confirmou o palpite (ou escolheu uma alternativa)."""
        if value is not None:
            self.value = value
        self.status = Status.CONFIRMADO

    def correct(self, value: Any) -> None:
        """Humano corrigiu para um valor diferente do palpite."""
        self.value = value
        self.status = Status.CORRIGIDO

    def merge_extraction(self, new_value: Any, new_prov: list[Provenance]) -> None:
        """
        Re-extração: agrega proveniência nova MAS respeita a monotonicidade.
        Se já é sticky, o valor humano permanece; só acumulamos evidência.
        """
        self.provenance.extend(new_prov)
        if not self.is_sticky:
            self.value = new_value

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "group": self.group.value,
            "scope": self.scope.value,
            "value": self.value,
            "status": self.status.value,
            "signals": self.signals.to_dict(),
            "provenance": [p.to_dict() for p in self.provenance],
            "alternatives": self.alternatives,
            "anchor": self.anchor,
            "propagates": self.propagates,
            "impact": round(self.impact, 4),
            "confidence": round(self.confidence, 4),
        }
