# -*- coding: utf-8 -*-
"""
archetypes.py — Arquétipos de marca para o caso "dado ausente".

Princípio de UX: reconhecimento, não evocação. Quando uma fonte não revela um
atributo de estratégia (ex.: não dá pra inferir o arquétipo do site), o sistema
NÃO mostra um campo em branco. Ele oferece OPÇÕES CLICÁVEIS de arquétipo, cada
uma com um exemplo de tom, para o humano reconhecer a sua.

Os 12 arquétipos clássicos (Jung/Mark&Pearson), enxutos para escolha rápida.
O LLM pode reordenar/filtrar conforme o setor, mas o VALOR (a escolha) é humano
e a confiança é calculada à parte (escolha do humano => ceiling alto).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Archetype:
    id: str
    nome: str
    promessa: str       # o que a marca entrega
    tom_exemplo: str    # uma frase no tom dele (ajuda o reconhecimento)


ARCHETYPES: list[Archetype] = [
    Archetype("inocente", "O Inocente", "Simplicidade e otimismo",
              "A vida pode ser leve. A gente cuida do resto."),
    Archetype("sabio", "O Sábio", "Verdade e conhecimento",
              "Os dados não mentem. Veja o que eles revelam."),
    Archetype("explorador", "O Explorador", "Liberdade e descoberta",
              "O próximo destino não está no mapa. Vamos."),
    Archetype("heroi", "O Herói", "Coragem e superação",
              "Não existe atalho. Existe treino. Bora."),
    Archetype("fora_da_lei", "O Fora-da-lei", "Ruptura e revolução",
              "As regras foram feitas por quem nunca tentou."),
    Archetype("mago", "O Mago", "Transformação e visão",
              "O impossível é só uma questão de tempo."),
    Archetype("cara_comum", "O Cara Comum", "Pertencimento e autenticidade",
              "Sem firula. Feito pra quem é de verdade."),
    Archetype("amante", "O Amante", "Intimidade e prazer",
              "Feito pra ser sentido, não só usado."),
    Archetype("bobo_da_corte", "O Bobo da Corte", "Alegria e espontaneidade",
              "Se não for divertido, a gente nem faz."),
    Archetype("prestativo", "O Prestativo", "Cuidado e serviço",
              "Você não está sozinho nisso. Conte com a gente."),
    Archetype("criador", "O Criador", "Criatividade e expressão",
              "Imagine. Depois a gente constrói junto."),
    Archetype("governante", "O Governante", "Controle e excelência",
              "Liderança não se improvisa. Se conquista."),
]

_BY_ID = {a.id: a for a in ARCHETYPES}


def get(archetype_id: str) -> Archetype | None:
    return _BY_ID.get(archetype_id)


def as_options(limit: int = 12) -> list[dict[str, str]]:
    """Opções clicáveis para a pergunta de arquétipo (dado ausente)."""
    return [
        {"id": a.id, "label": a.nome, "promessa": a.promessa, "tom": a.tom_exemplo}
        for a in ARCHETYPES[:limit]
    ]
