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

import json
import threading
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import connectors
import store
from config import Config
from motor import Motor
from schema import Node, Status

CFG = Config.from_env()
APP_HTML = Path(__file__).with_name("app.html")

# ---------------------------------------------------------------------------
# Sessões em memória (uma entrevista por sessão). Protegidas por lock porque
# usamos ThreadingHTTPServer.
# ---------------------------------------------------------------------------
_SESSIONS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

# Estados de OAuth pendentes (CSRF): state -> session. Em memória de propósito.
_OAUTH_STATES: dict[str, str] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_session() -> str:
    sid = uuid.uuid4().hex[:12]
    with _LOCK:
        _SESSIONS[sid] = {"bundles": [], "nodes": None, "motor": None}
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
            tok = store.load_ig_token(sid) or {}
            bundle = connectors.connect_instagram(
                body.get("handle", ""), has_meta_creds=CFG.has_meta_creds,
                token=tok.get("access_token"), ig_user_id=tok.get("ig_user_id"))
        elif source == "upload":
            bundle = connectors.connect_upload(body.get("files", []))
        else:
            return self._json({"error": "fonte_desconhecida", "source": source}, 400)

        with _LOCK:
            sess["bundles"].append(bundle)
            # (re)monta os nós com tudo que já chegou — respeitando monotonicidade
            sess["nodes"] = connectors.build_nodes(sess["bundles"])
            sess["motor"] = Motor(sess["nodes"])

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
        return {
            "node_id": q.id, "label": q.label, "group": q.group.value,
            "pergunta": llm_phrase_question(q),
            "palpite": q.value, "status": q.status.value,
            "alternativas": q.alternatives,
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
        Início do OAuth 2.0 (conta Business/Creator do cliente, via Graph API).
        Guarda o `state` (CSRF) ligado à sessão. Sem credenciais Meta, devolve
        instruções honestas (não inventamos URL).
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
        Callback do OAuth — troca `code` por access_token (curto -> longo prazo)
        e guarda o token (server-side). IMPLEMENTADO e guardado por credenciais:
        só executa com META_APP_ID/SECRET/REDIRECT presentes e App Review aprovado.
        """
        if not CFG.has_meta_creds:
            return self._json({"error": "credenciais_meta_ausentes"}, 503)
        code = qs.get("code", [""])[0]
        state = qs.get("state", [""])[0]
        with _LOCK:
            session = _OAUTH_STATES.pop(state, None)
        if not code or not session:
            return self._json({"error": "state_ou_code_invalido"}, 400)
        try:
            access_token, expires_in = self._exchange_code_for_token(code)
            expires_at = (datetime.now(timezone.utc) +
                          timedelta(seconds=expires_in or 0)).isoformat() if expires_in else None
            # ig_user_id é descoberto preguiçosamente no 1º fetch (connect_instagram).
            store.save_ig_token(session, access_token, None, expires_at, _now_iso())
            self._html(
                f"<h2>Instagram conectado ✔</h2>"
                f"<p>Sessão <code>{session}</code> autorizada. Pode fechar esta aba "
                f"e voltar ao Ponto Zero — a próxima extração lerá a mídia real.</p>")
        except Exception as exc:
            self._json({"error": "falha_oauth", "detail": f"{type(exc).__name__}: {exc}"}, 502)

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
