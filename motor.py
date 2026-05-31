# -*- coding: utf-8 -*-
"""
motor.py — O motor de decisão. Quase todo determinístico.

Regras fiéis à especificação:
  - score do nó = (1 - confidence) * impact
  - Pergunta o de MAIOR score se score >= tau; senão PARA (suficiência).
    (não há barra de progresso; o fim é por suficiência)
  - tau ADAPTATIVO: começa 0.25; +0.04 ao confirmar um palpite (hit);
    -0.05 ao corrigir (correct); limites [0.12, 0.60].
  - Confirmar uma ÂNCORA propaga coerência: reduz a dispersion dos `propagates`.
  - A PRIMEIRA pergunta é a de POSTURA (consistente / espalhada / mudando) e ela
    REPONDERA tudo (ajusta os sinais antes de qualquer outra pergunta).

`impact` é INJETADO aqui pelo CONSUMPTION_MAP — o mapa do que o gerador de
carrossel consome. O extrator nunca conhece impacto.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from schema import Node, Signals, Status
from scorer import score_all, score_node


def _clone_signals(s: Signals) -> Signals:
    return Signals(ceiling=s.ceiling, dispersion=s.dispersion,
                   agreement=s.agreement, coverage=s.coverage)


# ---------------------------------------------------------------------------
# Mapa de consumo do GERADOR (a fonte do impacto). Quanto o carrossel depende
# de cada atributo. 1.0 = indispensável; valores menores = cosmético.
# ---------------------------------------------------------------------------
CONSUMPTION_MAP: dict[str, float] = {
    "primary_color": 1.00,
    "tone_of_voice": 0.95,
    "pillars": 0.85,
    "typography": 0.80,
    "archetype": 0.70,
    "positioning": 0.65,
    "audience": 0.60,
    "tagline": 0.60,
    "secondary_color": 0.55,
    "vocabulary": 0.50,
    "logo": 0.50,
}

TAU_START = 0.25
TAU_HIT = +0.04
TAU_CORRECT = -0.05
TAU_MIN, TAU_MAX = 0.12, 0.60


def inject_impact(nodes: dict[str, Node]) -> None:
    """Injeta o impacto do mapa de consumo. NÃO vem da extração."""
    for nid, node in nodes.items():
        node.impact = CONSUMPTION_MAP.get(nid, 0.3)


@dataclass
class Metrics:
    hits: int = 0
    corrects: int = 0
    questions: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.corrects
        return round(self.hits / total, 4) if total else 0.0

    @property
    def false_confidence_rate(self) -> float:
        """De cada palpite confiante apresentado, quantos o humano corrigiu."""
        total = self.hits + self.corrects
        return round(self.corrects / total, 4) if total else 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "hits": self.hits,
            "corrects": self.corrects,
            "perguntas_ate_suficiencia": self.questions,
            "hit_rate": self.hit_rate,
            "taxa_falsa_confianca": self.false_confidence_rate,
        }


class Motor:
    """Mantém o estado da entrevista adaptativa sobre um conjunto de nós."""

    def __init__(self, nodes: dict[str, Node], tau: float = TAU_START) -> None:
        self.nodes = nodes
        self.tau = tau
        self.posture: str | None = None
        self.metrics = Metrics()
        inject_impact(self.nodes)
        score_all(self.nodes)
        # guarda os sinais-BASE (antes de qualquer postura) p/ reaplicação idempotente
        self._base: dict[str, Signals] = {nid: _clone_signals(n.signals)
                                          for nid, n in self.nodes.items()}

    def replace_nodes(self, nodes: dict[str, Node]) -> None:
        """
        Troca os nós (ex.: re-extração) PRESERVANDO o estado do motor: tau,
        métricas e postura. Recaptura a base e reaplica a postura (idempotente),
        para que a confirmação humana e o progresso da entrevista não se percam.
        """
        self.nodes = nodes
        inject_impact(self.nodes)
        score_all(self.nodes)
        self._base = {nid: _clone_signals(n.signals) for nid, n in self.nodes.items()}
        if self.posture:
            self.apply_posture(self.posture)

    # ------------------------------------------------------------------ score
    @staticmethod
    def node_score(node: Node) -> float:
        """score = (1 - confidence) * impact. Nó já decidido pelo humano = 0."""
        if node.is_sticky:
            return 0.0
        return (1.0 - node.confidence) * node.impact

    # ---------------------------------------------------------------- postura
    def apply_posture(self, posture: str) -> None:
        """
        A primeira resposta. Repondera TODOS os sinais antes da entrevista.
          - consistente: a marca é coesa -> dispersões caem, confiança sobe
                         (resultado: menos perguntas).
          - espalhada:   a marca varia muito -> dispersões sobem, confiança cai
                         (resultado: mais perguntas, sobretudo no Visual).
          - mudando:     está em transição -> o inferido/declarado antigo perde
                         teto; força revisão da estratégia.
        """
        self.posture = posture
        for nid, node in self.nodes.items():
            # nós já decididos pelo humano são imutáveis (não repondera)
            if node.is_sticky:
                continue
            # SEMPRE parte da BASE -> aplicar a postura 2x não compõe (#7)
            base = self._base.get(nid)
            if base is not None:
                node.signals = _clone_signals(base)
            s = node.signals
            if posture == "consistente":
                s.dispersion = max(0.0, s.dispersion * 0.55)
                s.ceiling = min(1.0, s.ceiling * 1.05)
            elif posture == "espalhada":
                s.dispersion = min(1.0, s.dispersion * 1.5 + 0.1)
            elif posture == "mudando":
                s.ceiling = s.ceiling * 0.85
                s.dispersion = min(1.0, s.dispersion + 0.15)
        score_all(self.nodes)

    # --------------------------------------------------------------- pergunta
    def next_question(self) -> Node | None:
        """
        Devolve o nó de maior score se >= tau; senão None (suficiência atingida).
        Se a postura ainda não foi dada, o chamador deve perguntá-la primeiro.
        """
        candidates = [n for n in self.nodes.values() if not n.is_sticky]
        if not candidates:
            return None
        best = max(candidates, key=self.node_score)
        if self.node_score(best) >= self.tau:
            return best
        return None  # suficiência: o melhor já não vale o custo de perguntar

    def is_sufficient(self) -> bool:
        return self.posture is not None and self.next_question() is None

    # --------------------------------------------------------------- resposta
    def answer(self, node_id: str, action: str, value=None) -> None:
        """
        Registra a resposta humana e adapta tau.
          action == 'confirm' (hit)   -> tau += 0.04
          action == 'correct'         -> tau -= 0.05
        Confirmar uma âncora propaga coerência aos `propagates`.
        """
        node = self.nodes[node_id]
        self.metrics.questions += 1

        if action == "confirm":
            node.confirm(value)
            self.metrics.hits += 1
            self.tau = min(TAU_MAX, self.tau + TAU_HIT)
        elif action == "correct":
            node.correct(value)
            self.metrics.corrects += 1
            self.tau = max(TAU_MIN, self.tau + TAU_CORRECT)
        else:
            raise ValueError(f"ação inválida: {action}")

        # Valor decidido pelo humano = sinal máximo (teto cheio, sem dispersão).
        node.signals.ceiling = 1.0
        node.signals.dispersion = 0.0
        node.signals.agreement = 1.0
        node.signals.coverage = 1.0
        score_node(node)

        if node.anchor:
            self._propagate_coherence(node)

    def _propagate_coherence(self, anchor: Node) -> None:
        """Confirmar âncora => os nós que ela ancora ficam mais coesos."""
        for target_id in anchor.propagates:
            target = self.nodes.get(target_id)
            if target is None or target.is_sticky:
                continue
            target.signals.dispersion = max(0.0, target.signals.dispersion * 0.6)
            target.signals.agreement = min(1.0, target.signals.agreement + 0.1)
            score_node(target)

    # ---------------------------------------------------------------- métricas
    def distinction_captured(self) -> int:
        """Slots de alto impacto (>=0.7) com sinal PRÓPRIO (cobertura real)."""
        return sum(
            1 for n in self.nodes.values()
            if n.impact >= 0.7 and n.signals.coverage > 0.0
            and n.status != Status.AUSENTE
        )

    def snapshot(self) -> dict:
        """Estado vivo do motor — alimenta o painel ao vivo no front."""
        ranked = sorted(self.nodes.values(), key=self.node_score, reverse=True)
        return {
            "tau": round(self.tau, 4),
            "postura": self.posture,
            "suficiente": self.is_sufficient(),
            "distincao_capturada": self.distinction_captured(),
            "metrics": self.metrics.to_dict(),
            "fila": [
                {"id": n.id, "label": n.label, "score": round(self.node_score(n), 4),
                 "confidence": round(n.confidence, 4), "impact": round(n.impact, 4),
                 "status": n.status.value}
                for n in ranked
            ],
        }
