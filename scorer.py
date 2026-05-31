# -*- coding: utf-8 -*-
"""
scorer.py — O cérebro ÚNICO e CENTRAL da confiança.

Regra inviolável: NENHUM extrator emite confiança. Mesmo um valor vindo do LLM
tem a confiança calculada AQUI, a partir de sinais observáveis do corpus
(volume, consistência, concordância, cobertura) — nunca do auto-relato do modelo.

Fórmula (fiel à especificação):
    confidence = ceiling * (1 - 0.6*dispersion) * (0.6 + 0.4*agreement) * coverage
    limitada a [0, 1]
"""
from __future__ import annotations

from schema import Node, Signals


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def compute_confidence(signals: Signals) -> float:
    """A fórmula central. Recebe sinais, devolve confiança em [0,1]."""
    c = signals.ceiling
    d = _clamp(signals.dispersion)
    a = _clamp(signals.agreement)
    cov = _clamp(signals.coverage)

    confidence = c * (1.0 - 0.6 * d) * (0.6 + 0.4 * a) * cov
    return _clamp(confidence)


def score_node(node: Node) -> float:
    """Atualiza node.confidence in-place e retorna o valor."""
    node.confidence = compute_confidence(node.signals)
    return node.confidence


def score_all(nodes: dict[str, Node]) -> None:
    """Repontua todos os nós a partir dos sinais atuais."""
    for node in nodes.values():
        score_node(node)
