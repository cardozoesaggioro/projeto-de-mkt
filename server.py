# -*- coding: utf-8 -*-
"""
server.py — Backend fino do Ponto Zero (stdlib http.server).

Responsabilidades do backend (e SÓ dele): guardar credenciais, fazer
OAuth/fetch/LLM, rodar o motor. O front nunca vê um segredo.

Endpoints (fiéis à especificação) + alguns auxiliares para o painel ao vivo:
  GET  /                          -> serve app.html
  GET  /api/health                -> status + credenciais faltando
  POST /api/extract/{site|instagram|upload}
  GET  /auth/instagram/{start|callback}
  GET  /api/brandbook?session=..  -> lê (recap)
  POST /api/brandbook             -> persiste em sqlite
  -- auxiliares do motor (painel ao vivo) --
  POST /api/motor/posture         -> aplica postura (1a pergunta) e repondera
  GET  /api/motor/next?session=.. -> próxima pergunta ou suficiência
  POST /api/motor/answer          -> confirma/corrige e adapta tau
  GET  /api/sample?session=..     -> slide de amostra (validação final)

Bind em 0.0.0.0:$PORT.
"""
from __future__ import annotations

import base64
import json
import threading
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import carousel
import catalog
import connectors
import store
from config import Config
from motor import Motor
from schema import Node, Status

CFG = Config.from_env()
APP_HTML = Path(__file__).with_name("app.html")

# Referências de estilo GLOBAIS (a base de carrossel para TODOS os usuários).
# Ficam num arquivo versionado -> sobrevivem a reinícios/deploys do Render.
DEFAULT_REFS_FILE = Path(__file__).with_name("default_style_references.json")


def load_default_references() -> list:
    try:
        if DEFAULT_REFS_FILE.exists():
            return json.loads(DEFAULT_REFS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def save_default_references(refs: list) -> None:
    DEFAULT_REFS_FILE.write_text(
        json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")


_DEFAULT_REFS = load_default_references()

# ---------------------------------------------------------------------------
# Sessões em memória (uma entrevista por sessão). Protegidas por lock porque
# usamos ThreadingHTTPServer.
# ---------------------------------------------------------------------------
_SESSIONS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

# Estados de OAuth pendentes (CSRF): state -> session. Em memória de propósito.
_OAUTH_STATES: dict[str, str] = {}

# Chave fixa da conta-LENTE (a da agência, autorizada 1x p/ Business Discovery).
# O token dela serve para analisar qualquer @ público Business/Creator.
LENS_KEY = "__lens__"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session() -> str:
    sid = uuid.uuid4().hex[:12]
    with _LOCK:
        _SESSIONS[sid] = {"bundles": [], "nodes": None, "motor": None,
                          "style_references": []}
    return sid


def _get_session(sid: str) -> dict[str, Any] | None:
    with _LOCK:
        return _SESSIONS.get(sid)


# ---------------------------------------------------------------------------
# LLM proxy (chave em env). Sem chave -> fallback heurístico determinístico.
# ---------------------------------------------------------------------------
def _heuristic_question(node: Node) -> str:
    """Molde determinístico — usado sem LLM (o fluxo roda igual, só mais seco)."""
    if node.status == Status.AUSENTE:
        return f"Não achei sinal claro de “{node.label}”. Qual destes combina mais?"
    return f"Detectei “{node.label}”: {node.value}. Confere?"


def _http_json(url: str, body: dict, headers: dict, timeout: int = 15) -> dict:
    """POST JSON e devolve JSON. Levanta em erro (o chamador trata)."""
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _call_anthropic(prompt: str, system: str, max_tokens: int) -> str | None:
    """API de Mensagens da Anthropic (modelos claude-*)."""
    data = _http_json(
        "https://api.anthropic.com/v1/messages",
        body={
            "model": CFG.llm_model, "max_tokens": max_tokens,
            "system": system or "Responda em pt-BR, conciso, sem aspas extras.",
            "messages": [{"role": "user", "content": prompt}],
        },
        headers={"x-api-key": CFG.llm_api_key, "anthropic-version": "2023-06-01"},
    )
    parts = data.get("content", [])
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return text.strip() or None


def _call_openai(prompt: str, system: str, max_tokens: int) -> str | None:
    """Chat Completions da OpenAI (modelos gpt-*/o*)."""
    data = _http_json(
        "https://api.openai.com/v1/chat/completions",
        body={
            "model": CFG.llm_model, "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system or "Responda em pt-BR, conciso, sem aspas extras."},
                {"role": "user", "content": prompt},
            ],
        },
        headers={"authorization": f"Bearer {CFG.llm_api_key}"},
    )
    choices = data.get("choices", [])
    if not choices:
        return None
    return (choices[0].get("message", {}).get("content") or "").strip() or None


def call_llm(prompt: str, system: str = "", max_tokens: int = 120) -> str | None:
    """
    Proxy de LLM no servidor (chave SÓ no servidor, vinda de env). Multi-provedor:
    roteia por CFG.resolved_provider (Anthropic / OpenAI). Em QUALQUER falha
    devolve None e o chamador cai no fallback heurístico — o fluxo NUNCA quebra
    por causa do LLM.

    Regra de design: o LLM dá o VALOR/da frase; a CONFIANÇA nunca vem do modelo —
    ela já foi calculada pelo scorer a partir de sinais observáveis.
    """
    if not CFG.has_llm:
        return None
    provider = CFG.resolved_provider
    try:
        if provider == "anthropic":
            return _call_anthropic(prompt, system, max_tokens)
        if provider == "openai":
            return _call_openai(prompt, system, max_tokens)
        # TODO[REAL]: outros provedores entram aqui (rotear por LLM_PROVIDER).
        print(f"[llm] provedor desconhecido p/ modelo '{CFG.llm_model}' -> fallback")
        return None
    except Exception as exc:  # rede/timeout/credencial inválida -> fallback
        print(f"[llm] fallback heurístico ({type(exc).__name__}: {exc})")
        return None


def call_vision(image_b64: str, prompt: str, system: str = "",
                max_tokens: int = 500) -> str | None:
    """
    Lê uma IMAGEM via LLM multimodal (OpenAI gpt-4o*/4o-mini). Usado para ler
    prints de perfil (bio/legendas) que o CV não captura. Falha -> None (o
    fluxo nunca quebra; a cor por CV continua valendo).
    """
    if not CFG.has_llm or CFG.resolved_provider != "openai":
        return None
    try:
        data = _http_json(
            "https://api.openai.com/v1/chat/completions",
            body={
                "model": CFG.llm_model, "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system or "Responda só JSON, pt-BR."},
                    {"role": "user", "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    ]},
                ],
            },
            headers={"authorization": f"Bearer {CFG.llm_api_key}"},
        )
        choices = data.get("choices", [])
        return (choices[0].get("message", {}).get("content") or "").strip() or None if choices else None
    except Exception as exc:
        print(f"[vision] fallback ({type(exc).__name__}: {exc})")
        return None


def extract_profile_signals(image_b64: str) -> dict[str, Any]:
    """
    Lê um PRINT de perfil de Instagram e devolve sinais textuais (valor do LLM;
    a confiança é INFERÊNCIA, calculada pelo scorer). Retorna {} em falha.
    """
    prompt = (
        "Esta é uma captura de tela de um perfil de marca no Instagram. Leia o "
        "texto visível (nome, bio, legendas, destaques) e identifique as cores da "
        "MARCA (do logo/identidade, IGNORANDO fotos de pessoas). Responda APENAS JSON: "
        '{"positioning":"<bio/proposta de valor>","tone_of_voice":"<2-4 palavras>",'
        '"vocabulary":["palavra","palavra"],"audience":"<público provável>",'
        '"colors":["#RRGGBB cor primária da marca","#RRGGBB secundária"]}. '
        "Se algo não aparecer, use string vazia ou lista vazia."
    )
    raw = call_vision(image_b64, prompt)
    if not raw:
        return {}
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception:
        return {}
    out: dict[str, Any] = {}
    if isinstance(data.get("positioning"), str) and data["positioning"].strip():
        out["positioning"] = data["positioning"].strip()
    if isinstance(data.get("tone_of_voice"), str) and data["tone_of_voice"].strip():
        out["tone_of_voice"] = data["tone_of_voice"].strip()
    if isinstance(data.get("vocabulary"), list) and data["vocabulary"]:
        out["vocabulary"] = [str(v).strip() for v in data["vocabulary"] if str(v).strip()][:6]
    if isinstance(data.get("audience"), str) and data["audience"].strip():
        out["audience"] = data["audience"].strip()
    if isinstance(data.get("colors"), list):
        import re as _re
        cols = [c for c in data["colors"]
                if isinstance(c, str) and _re.fullmatch(r"#[0-9A-Fa-f]{6}", c.strip())]
        if cols:
            out["colors"] = [c.strip().upper() for c in cols][:3]
    return out


def extract_style_reference(image_b64: str) -> dict[str, Any]:
    """
    Lê uma imagem de post/slide usada como REFERÊNCIA de estilo de carrossel e
    descreve o estilo (para guiar a geração futura). Retorna {} em falha.
    """
    prompt = (
        "Esta é uma imagem de um post/slide de Instagram usada como REFERÊNCIA de "
        "ESTILO para criar carrosséis. Descreva o estilo de forma objetiva. "
        "Responda APENAS um JSON: "
        '{"estrutura":"<capa/miolo/CTA, como organiza>","uso_de_cor":"<paleta e como usa>",'
        '"tipografia":"<estilo de fonte/peso>","densidade_texto":"baixa|média|alta",'
        '"formato":"<lista, citação, dado, storytelling, etc.>",'
        '"resumo":"<1 frase que resume o estilo>"}.'
    )
    raw = call_vision(image_b64, prompt, max_tokens=400)
    if not raw:
        return {}
    try:
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        data = json.loads(raw)
    except Exception:
        return {}
    keys = ("estrutura", "uso_de_cor", "tipografia", "densidade_texto", "formato", "resumo")
    out = {k: str(data[k]).strip() for k in keys if isinstance(data.get(k), str) and data[k].strip()}
    return out


def llm_phrase_question(node: Node) -> str:
    """Frase humana da pergunta — via LLM se houver chave, senão heurística."""
    if not CFG.has_llm:
        return _heuristic_question(node)
    if node.status == Status.AUSENTE:
        prompt = (f"Reescreva como UMA pergunta curta e amigável (pt-BR) pedindo ao "
                  f"usuário para escolher o “{node.label}” da marca entre opções, "
                  f"já que não há sinal claro nas fontes. Só a pergunta.")
    else:
        prompt = (f"Reescreva como UMA pergunta curta de confirmação (pt-BR): o "
                  f"atributo “{node.label}” parece ser “{node.value}”. Só a pergunta.")
    return call_llm(prompt) or _heuristic_question(node)


# ---------------------------------------------------------------------------
# Slide de amostra (validação final por REAÇÃO, não auditoria de lista)
# ---------------------------------------------------------------------------
def build_sample_slide(nodes: dict[str, Node]) -> dict[str, Any]:
    """Gera um slide de carrossel com os valores confirmados/palpitados."""
    def val(nid: str, default: Any = None) -> Any:
        n = nodes.get(nid)
        return n.value if n and n.value is not None else default

    primary = val("primary_color", "#16367A")
    secondary = val("secondary_color", "#D4AF37")
    tagline = val("tagline", "Sua marca, em um slide.")
    tone = val("tone_of_voice", "autoridade acessível")
    typo = val("typography", "Inter")
    pillars = val("pillars", []) or []
    headline = pillars[0] if pillars else "Conteúdo que decide"

    return {
        "values": {"primary": primary, "secondary": secondary, "tagline": tagline,
                   "tone": tone, "typography": typo, "headline": headline},
        # HTML autocontido do slide (o front injeta num quadro 1080x1080)
        "html": f"""
<div style="width:100%;height:100%;background:{primary};color:#fff;
            display:flex;flex-direction:column;justify-content:space-between;
            padding:9% 8%;box-sizing:border-box;font-family:{typo},sans-serif">
  <div style="font-size:1.1rem;letter-spacing:.18em;color:{secondary};
              text-transform:uppercase">Ponto Zero · amostra</div>
  <div>
    <div style="font-size:3rem;font-weight:800;line-height:1.05">{headline}</div>
    <div style="margin-top:1rem;font-size:1.25rem;opacity:.92">{tagline}</div>
  </div>
  <div style="font-size:.95rem;opacity:.8">tom: {tone}</div>
</div>""",
    }


# ---------------------------------------------------------------------------
# Serialização do brand book
# ---------------------------------------------------------------------------
def brandbook_payload(sess: dict[str, Any]) -> dict[str, Any]:
    nodes: dict[str, Node] = sess["nodes"] or {}
    motor: Motor | None = sess["motor"]
    return {
        "nodes": [n.to_dict() for n in nodes.values()],
        "motor": motor.snapshot() if motor else None,
        "sample_slide": build_sample_slide(nodes) if nodes else None,
        # base global (estilo da casa) + referências específicas da sessão
        "style_references": _DEFAULT_REFS + sess.get("style_references", []),
        "generated_at": _now_iso(),
    }


# ===========================================================================
# Handler HTTP
# ===========================================================================
class Handler(BaseHTTPRequestHandler):
    server_version = "PontoZero/1.0"

    # -- helpers de resposta -------------------------------------------------
    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def log_message(self, fmt: str, *args: Any) -> None:  # log enxuto
        print(f"[{_now_iso()}] {self.address_string()} {fmt % args}")

    # -- roteamento ----------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/" or path == "/index.html":
                return self._serve_app()
            if path == "/api/health":
                return self._health()
            if path == "/api/motor/next":
                return self._motor_next(qs.get("session", [""])[0])
            if path == "/api/brandbook":
                return self._brandbook_get(qs)
            if path == "/api/sample":
                return self._sample(qs.get("session", [""])[0])
            if path == "/auth/instagram/start":
                return self._ig_start(qs)
            if path == "/auth/instagram/callback":
                return self._ig_callback(qs)
            self._json({"error": "not_found", "path": path}, 404)
        except Exception as exc:  # nunca derrubar o servidor por um request
            self._json({"error": "internal", "detail": str(exc)}, 500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_json()
            if path.startswith("/api/extract/"):
                return self._extract(path.rsplit("/", 1)[-1], body)
            if path == "/api/motor/posture":
                return self._motor_posture(body)
            if path == "/api/motor/answer":
                return self._motor_answer(body)
            if path == "/api/brandbook":
                return self._brandbook_post(body)
            if path == "/api/carousel":
                return self._carousel(body)
            if path == "/api/carousel/png":
                return self._carousel_png(body)
            if path == "/api/reference":
                return self._reference(body)
            self._json({"error": "not_found", "path": path}, 404)
        except Exception as exc:
            self._json({"error": "internal", "detail": str(exc)}, 500)

    # -- / -------------------------------------------------------------------
    def _serve_app(self) -> None:
        if APP_HTML.exists():
            self._html(APP_HTML.read_text(encoding="utf-8"))
        else:
            self._html("<h1>app.html ausente</h1>", 500)

    # -- /api/health ---------------------------------------------------------
    def _health(self) -> None:
        self._json({
            "status": "ok",
            "port": CFG.port,
            "integrations": CFG.integrations_status(),
            "missing_credentials": CFG.missing_credentials(),
            "llm_model": CFG.llm_model,
            "sessions_ativas": len(_SESSIONS),
            "time": _now_iso(),
        })

    # -- /api/extract/{source} ----------------------------------------------
    def _extract(self, source: str, body: dict[str, Any]) -> None:
        sid = body.get("session") or _new_session()
        sess = _get_session(sid)
        if sess is None:
            return self._json({"error": "sessao_invalida"}, 400)

        if source == "site":
            bundle = connectors.connect_site(body.get("url", ""))
        elif source == "instagram":
            lens = store.load_ig_token(LENS_KEY) or {}
            bundle = connectors.connect_instagram(
                body.get("handle", ""), has_meta_creds=CFG.has_meta_creds,
                lens_token=lens.get("access_token"), lens_ig_id=lens.get("ig_user_id"))
        elif source == "upload":
            bundle = connectors.connect_upload(
                body.get("files", []),
                vision_call=(extract_profile_signals if CFG.has_llm else None))
        else:
            return self._json({"error": "fonte_desconhecida", "source": source}, 400)

        with _LOCK:
            sess["bundles"].append(bundle)
            # (re)monta os nós com tudo que já chegou — respeitando MONOTONICIDADE
            # (passa os nós atuais p/ preservar confirmações do humano).
            sess["nodes"] = connectors.build_nodes(
                sess["bundles"], previous=sess["nodes"],
                llm_call=(call_llm if CFG.has_llm else None))
            if sess["motor"] is None:
                sess["motor"] = Motor(sess["nodes"])
            else:
                # preserva tau / métricas / postura / progresso da entrevista
                sess["motor"].replace_nodes(sess["nodes"])

        self._json({
            "session": sid,
            "bundle": bundle.to_dict(),
            "n_nodes": len(sess["nodes"]),
        })

    # -- /api/motor/posture --------------------------------------------------
    def _motor_posture(self, body: dict[str, Any]) -> None:
        sid = body.get("session", "")
        sess = _get_session(sid)
        if not sess or not sess["motor"]:
            return self._json({"error": "sem_extracao"}, 400)
        posture = body.get("posture", "")
        if posture not in ("consistente", "espalhada", "mudando"):
            return self._json({"error": "postura_invalida"}, 400)
        motor: Motor = sess["motor"]
        motor.apply_posture(posture)
        self._json({"session": sid, "snapshot": motor.snapshot(),
                    "next": self._question_payload(motor)})

    # -- /api/motor/next -----------------------------------------------------
    def _motor_next(self, sid: str) -> None:
        sess = _get_session(sid)
        if not sess or not sess["motor"]:
            return self._json({"error": "sem_extracao"}, 400)
        motor: Motor = sess["motor"]
        self._json({"session": sid, "next": self._question_payload(motor),
                    "snapshot": motor.snapshot()})

    # -- /api/motor/answer ---------------------------------------------------
    def _motor_answer(self, body: dict[str, Any]) -> None:
        sid = body.get("session", "")
        sess = _get_session(sid)
        if not sess or not sess["motor"]:
            return self._json({"error": "sem_extracao"}, 400)
        motor: Motor = sess["motor"]
        motor.answer(body.get("node_id", ""), body.get("action", ""), body.get("value"))
        self._json({"session": sid, "next": self._question_payload(motor),
                    "snapshot": motor.snapshot()})

    def _question_payload(self, motor: Motor) -> dict[str, Any] | None:
        q = motor.next_question()
        if q is None:
            return None  # suficiência
        # opções clicáveis normalizadas (palpite + fontes + catálogo)
        opt = catalog.build_options(q.id, q.value, q.alternatives)
        return {
            "node_id": q.id, "label": q.label, "group": q.group.value,
            "pergunta": llm_phrase_question(q),
            "palpite": q.value, "palpite_label": opt["palpite_label"],
            "status": q.status.value,
            "tipo": opt["tipo"], "opcoes": opt["opcoes"],
            "alternativas": q.alternatives,  # legado (compat)
            "confidence": round(q.confidence, 4), "impact": round(q.impact, 4),
            "score": round(motor.node_score(q), 4),
            "provenance": [p.to_dict() for p in q.provenance],
        }

    # -- /api/sample ---------------------------------------------------------
    def _sample(self, sid: str) -> None:
        sess = _get_session(sid)
        if not sess or not sess["nodes"]:
            return self._json({"error": "sem_extracao"}, 400)
        self._json({"session": sid, "sample_slide": build_sample_slide(sess["nodes"])})

    # -- /api/carousel (gera) / /api/carousel/png (exporta) -----------------
    def _carousel(self, body: dict[str, Any]) -> None:
        sid = body.get("session", "")
        sess = _get_session(sid)
        if not sess or not sess["nodes"]:
            return self._json({"error": "sem_extracao"}, 400)
        values = {nid: n.value for nid, n in sess["nodes"].items()}
        topic = (body.get("topic") or "").strip()
        n_slides = int(body.get("n_slides", 5) or 5)
        # passa o proxy de LLM só se houver chave (senão, copy heurística)
        llm = call_llm if CFG.has_llm else None
        result = carousel.build_carousel(values, topic, n_slides, llm)
        self._json({"session": sid, **result})

    def _reference(self, body: dict[str, Any]) -> None:
        """
        Sobe imagens de REFERÊNCIA de estilo -> visão extrai o estilo.
        scope='global' (padrão) = base de TODOS os usuários (arquivo versionado);
        scope='session' = só desta sessão.
        """
        sid = body.get("session") or _new_session()
        sess = _get_session(sid)
        if sess is None:
            return self._json({"error": "sessao_invalida"}, 400)
        if not CFG.has_llm:
            return self._json({"error": "sem_llm",
                               "detail": "leitura de estilo requer LLM_API_KEY (visão)."}, 400)
        scope = (body.get("scope") or "global").lower()
        added = []
        for f in body.get("files", []):
            b64 = f.get("b64")
            if not b64:
                continue
            style = extract_style_reference(b64)
            if style:
                style["fonte"] = f.get("name", "referência")
                if scope == "global":
                    style["global"] = True
                    _DEFAULT_REFS.append(style)
                else:
                    sess.setdefault("style_references", []).append(style)
                added.append(style)
        if scope == "global":
            save_default_references(_DEFAULT_REFS)  # persiste no arquivo versionado
        self._json({"session": sid, "scope": scope, "added": added,
                    "total_global": len(_DEFAULT_REFS),
                    "total_sessao": len(sess.get("style_references", []))})

    def _carousel_png(self, body: dict[str, Any]) -> None:
        html = body.get("html", "")
        if not html:
            return self._json({"error": "html_obrigatorio"}, 400)
        try:
            png = carousel.render_html_to_png(html)
            self._json({"png_base64": base64.b64encode(png).decode()})
        except Exception as exc:
            self._json({"error": "render_falhou",
                        "detail": f"{type(exc).__name__}: {exc}"}, 502)

    # -- /api/brandbook (GET/POST) ------------------------------------------
    def _brandbook_get(self, qs: dict[str, list[str]]) -> None:
        sid = qs.get("session", [""])[0]
        brand_id = qs.get("brand_id", [""])[0]
        if sid:
            sess = _get_session(sid)
            if not sess or not sess["nodes"]:
                return self._json({"error": "sem_extracao"}, 400)
            return self._json(brandbook_payload(sess))
        if brand_id:
            data = store.load_brandbook(brand_id)
            if data is None:
                return self._json({"error": "nao_encontrado"}, 404)
            return self._json(data)
        self._json({"brands": store.list_brands()})

    def _brandbook_post(self, body: dict[str, Any]) -> None:
        sid = body.get("session", "")
        brand_id = body.get("brand_id", "")
        sess = _get_session(sid)
        if not sess or not sess["nodes"]:
            return self._json({"error": "sem_extracao"}, 400)
        if not brand_id:
            return self._json({"error": "brand_id_obrigatorio"}, 400)
        payload = brandbook_payload(sess)
        store.save_brandbook(brand_id, payload, _now_iso())
        self._json({"ok": True, "brand_id": brand_id, "saved_at": payload["generated_at"]})

    # -- /auth/instagram/{start,callback} -----------------------------------
    def _ig_start(self, qs: dict[str, list[str]]) -> None:
        """
        Início do OAuth 2.0 da conta-LENTE (a da agência), via Facebook Login.
        Autorizada uma vez, ela alimenta o Business Discovery de qualquer @ público.
        Guarda o `state` (CSRF). Sem credenciais Meta, devolve instruções honestas.
        """
        if not CFG.has_meta_creds:
            return self._json({
                "error": "credenciais_meta_ausentes",
                "necessario": ["META_APP_ID", "META_APP_SECRET", "META_REDIRECT_URI"],
                "nota": "Basic Display API foi desligada (dez/2024). Usar Graph API "
                        "com App Review aprovado. Conta pessoal não tem API.",
            }, 503)
        session = qs.get("session", [""])[0] or _new_session()
        state = uuid.uuid4().hex
        with _LOCK:
            _OAUTH_STATES[state] = session
        # Com credenciais: monta a URL real de consentimento do Facebook Login.
        params = urllib.parse.urlencode({
            "client_id": CFG.meta_app_id,
            "redirect_uri": CFG.meta_redirect_uri,
            "scope": CFG.ig_scopes,
            "response_type": "code",
            "state": state,
        })
        url = f"https://www.facebook.com/v21.0/dialog/oauth?{params}"
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()

    def _ig_callback(self, qs: dict[str, list[str]]) -> None:
        """
        Callback do OAuth da LENTE — troca `code` por access_token (curto -> longo
        prazo), descobre o ig_user_id da lente e guarda tudo sob LENS_KEY. A partir
        daí, qualquer @ público é analisado por Business Discovery.
        """
        if not CFG.has_meta_creds:
            return self._json({"error": "credenciais_meta_ausentes"}, 503)
        code = qs.get("code", [""])[0]
        state = qs.get("state", [""])[0]
        with _LOCK:
            valid = _OAUTH_STATES.pop(state, None)
        if not code or valid is None:
            return self._json({"error": "state_ou_code_invalido"}, 400)
        try:
            access_token, expires_in = self._exchange_code_for_token(code)
            expires_at = (datetime.now(timezone.utc) +
                          timedelta(seconds=expires_in or 0)).isoformat() if expires_in else None
            lens_ig_id = self._discover_ig_user_id(access_token)
            store.save_ig_token(LENS_KEY, access_token, lens_ig_id, expires_at, _now_iso())
            ok = "✔" if lens_ig_id else "⚠ (sem IG Business ligado a uma Página)"
            self._html(
                f"<h2>Conta-lente conectada {ok}</h2>"
                f"<p>A lente foi autorizada. Agora é só digitar qualquer @ público "
                f"Business/Creator no Ponto Zero que o sistema analisa. Pode fechar.</p>")
        except Exception as exc:
            self._json({"error": "falha_oauth", "detail": f"{type(exc).__name__}: {exc}"}, 502)

    @staticmethod
    def _discover_ig_user_id(token: str) -> str | None:
        """Descobre o ig_user_id da lente: /me/accounts -> instagram_business_account."""
        try:
            accts = connectors._graph_get(
                "me/accounts", {"fields": "instagram_business_account", "access_token": token})
            for page in accts.get("data", []):
                iba = page.get("instagram_business_account")
                if iba and iba.get("id"):
                    return iba["id"]
        except Exception:
            pass
        return None

    @staticmethod
    def _exchange_code_for_token(code: str) -> tuple[str, int | None]:
        """
        code -> token curto -> token de longa duração. Usa o client_secret
        (segredo só no servidor). Retorna (access_token, expires_in).
        """
        short = connectors._graph_get("oauth/access_token", {
            "client_id": CFG.meta_app_id,
            "client_secret": CFG.meta_app_secret,
            "redirect_uri": CFG.meta_redirect_uri,
            "code": code,
        })
        short_token = short["access_token"]
        # troca por token de longa duração (~60 dias)
        long = connectors._graph_get("oauth/access_token", {
            "grant_type": "fb_exchange_token",
            "client_id": CFG.meta_app_id,
            "client_secret": CFG.meta_app_secret,
            "fb_exchange_token": short_token,
        })
        return long.get("access_token", short_token), long.get("expires_in")


def main() -> None:
    store.init_db()
    addr = ("0.0.0.0", CFG.port)
    httpd = ThreadingHTTPServer(addr, Handler)
    print(f"Ponto Zero subindo em http://0.0.0.0:{CFG.port}  (LLM={CFG.llm_model}, "
          f"Meta={'ok' if CFG.has_meta_creds else 'pendente'})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando.")
        httpd.shutdown()


if __name__ == "__main__":
    main()
