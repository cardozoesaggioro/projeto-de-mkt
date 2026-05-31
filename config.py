# -*- coding: utf-8 -*-
"""
config.py — Toda a configuração vem de variáveis de ambiente.

Segredos NUNCA no front e NUNCA no código. O servidor lê tudo daqui.
Também sabe dizer, honestamente, QUAIS credenciais estão faltando — isso
alimenta o /api/health para o onboarding adaptar os cartões de conexão.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | os.PathLike | None = None) -> None:
    """
    Carregador de .env minimalista (stdlib). Lê KEY=VALUE, ignora comentários,
    e NÃO sobrescreve variáveis já presentes no ambiente (o ambiente manda).
    Assim você põe o segredo no .env (gitignored) sem me passá-lo em texto.
    """
    env_path = Path(path) if path else Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    # utf-8-sig tolera o BOM que o PowerShell (Out-File/Set-Content -Encoding utf8)
    # adiciona — sem isso, a 1ª chave viria como "﻿LLM_API_KEY" e não casaria.
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Config:
    # Meta / Instagram Graph API (conta Business/Creator do cliente)
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = ""
    ig_scopes: str = "instagram_basic,pages_show_list,business_management"

    # LLM (proxy no servidor; usado p/ tom, pilares, frase das perguntas, arquétipos)
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-4-5"
    # Provedor: vazio = inferido pelo nome do modelo; ou force "anthropic"/"openai".
    llm_provider: str = ""

    # Servidor
    port: int = 8000

    @staticmethod
    def from_env() -> "Config":
        load_dotenv()  # carrega .env (se existir) antes de ler o ambiente
        return Config(
            meta_app_id=os.environ.get("META_APP_ID", ""),
            meta_app_secret=os.environ.get("META_APP_SECRET", ""),
            meta_redirect_uri=os.environ.get("META_REDIRECT_URI", ""),
            ig_scopes=os.environ.get("IG_SCOPES", "instagram_basic,pages_show_list,business_management"),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_model=os.environ.get("LLM_MODEL", "claude-sonnet-4-5"),
            llm_provider=os.environ.get("LLM_PROVIDER", "").strip().lower(),
            port=int(os.environ.get("PORT", "8000")),
        )

    @property
    def resolved_provider(self) -> str:
        """
        Decide o provedor: se LLM_PROVIDER vier setado, ele manda; senão inferimos
        pelo prefixo do modelo. Retorna "anthropic", "openai" ou "desconhecido".
        """
        if self.llm_provider in ("anthropic", "openai"):
            return self.llm_provider
        m = self.llm_model.lower()
        if m.startswith("claude"):
            return "anthropic"
        if m.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
            return "openai"
        return "desconhecido"

    # --- prontidão de cada integração -------------------------------------
    @property
    def has_meta_creds(self) -> bool:
        return bool(self.meta_app_id and self.meta_app_secret and self.meta_redirect_uri)

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_api_key)

    def missing_credentials(self) -> list[dict[str, str]]:
        """Lista honesta do que falta — vai pro /api/health e pro onboarding."""
        missing: list[dict[str, str]] = []
        if not self.meta_app_id:
            missing.append({"env": "META_APP_ID", "para": "OAuth Instagram (Graph API)"})
        if not self.meta_app_secret:
            missing.append({"env": "META_APP_SECRET", "para": "trocar code->token"})
        if not self.meta_redirect_uri:
            missing.append({"env": "META_REDIRECT_URI", "para": "callback do OAuth"})
        if not self.llm_api_key:
            missing.append({"env": "LLM_API_KEY", "para": "tom/pilares/frase das perguntas"})
        return missing

    def integrations_status(self) -> dict[str, str]:
        """Status por integração para os cartões de conexão do onboarding."""
        return {
            "site": "pronto (conector público, mockado até Playwright)",
            "instagram": "pronto" if self.has_meta_creds else "aguardando credenciais Meta + App Review",
            "upload": "pronto",
            "llm": (f"pronto ({self.resolved_provider}:{self.llm_model})" if self.has_llm
                    else "aguardando LLM_API_KEY (usando heurística)"),
            "linkedin": "fora do v1 (Partner approval)",
            "tiktok": "fora do v1",
        }
