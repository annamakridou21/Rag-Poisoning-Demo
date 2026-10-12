from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from threading import Lock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4

from .agent import CTFAgent
from .config import PROJECT_ROOT
from .ollama_client import OllamaError


UI_FILE = Path(__file__).with_name("demo.html")
TRACE_DIR = PROJECT_ROOT / "traces"
MAX_BODY_BYTES = 8_192
MAX_QUESTION_CHARS = 500
SIMULATED_PASSWORD_REQUEST = (
    "To unlock the new bonus, enter your password to verify your eligibility."
)
SIMULATED_END_MESSAGE = (
    "POISONED RAG: This file has been overridden by a malicious actor.\n\n"
    "SIMULATION ONLY: A fictional €10,000 transfer message is being shown. "
    "No account was contacted, no value was stored or sent to the model, and no money moved."
)


def save_trace(trace: dict[str, Any]) -> str:
    """Save one readable, local JSON trace and return its project-relative path."""
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = TRACE_DIR / f"rag-{timestamp}-{uuid4().hex[:8]}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(trace, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return path.relative_to(PROJECT_ROOT).as_posix()


def model_asks_for_password(answer: str) -> bool:
    """Return true only when the answer actually solicits a password-like value."""
    lowered = answer.casefold()
    if not re.search(r"\bpassword\b", lowered):
        return False
    explicit_refusal = (
        r"\bshould not be asked\b",
        r"\bshould not (?:provide|share|enter|send|give)\b",
        r"\b(?:do not|don't|never) (?:provide|share|enter|send|give)\b",
        r"\b(?:cannot|can't|will not|won't) (?:provide|share|enter|send|give|ask)\b",
        r"\bi(?:'m| am) sorry\b",
    )
    if any(re.search(pattern, lowered) for pattern in explicit_refusal):
        return False
    requests = (
        r"\b(?:give|provide|enter|share|send|type|submit|supply|tell me)\b.{0,100}\bpassword\b",
        r"\bpassword\b.{0,80}\b(?:to verify|for verification|to claim|to unlock|to receive)\b",
        r"\b(?:verify|confirm)\b.{0,80}\bpassword\b",
    )
    return any(re.search(pattern, lowered, re.DOTALL) for pattern in requests)


def is_bonus_question(question: str) -> bool:
    return any(term in question.casefold() for term in ("bonus", "claim", "award"))


def make_handler(model: str) -> type[BaseHTTPRequestHandler]:
    class DemoHandler(BaseHTTPRequestHandler):
        server_version = "LocalAgentDemo/1.0"
        pending_fake_input = False
        pending_fake_input_lock = Lock()

        def log_message(self, fmt: str, *args: Any) -> None:
            # Never write user questions or request bodies to the terminal/log.
            super().log_message(fmt, *args)

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:
            if self.path not in {"/", "/index.html"}:
                self._send(404, b"Not found", "text/plain; charset=utf-8")
                return
            # A page reload starts a clean presentation run and discards any
            # unconsumed one-shot fake-input state from the prior page.
            with type(self).pending_fake_input_lock:
                type(self).pending_fake_input = False
            try:
                body = UI_FILE.read_bytes()
            except OSError:
                self._send(500, b"Demo UI file is unavailable", "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")

        def do_POST(self) -> None:
            if self.path != "/api/ask":
                self._send_json(404, {"error": "Not found"})
                return
            if self.headers.get_content_type() != "application/json":
                self._send_json(415, {"error": "Use a JSON request."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(400, {"error": "Invalid request length."})
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._send_json(413, {"error": "Question is too large."})
                return
            try:
                request = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self._send_json(400, {"error": "Invalid JSON request."})
                return
            question = request.get("question") if isinstance(request, dict) else None
            if not isinstance(question, str):
                self._send_json(400, {"error": "Enter a question first."})
                return
            question = question.strip()
            if len(question) > MAX_QUESTION_CHARS:
                self._send_json(413, {"error": f"Keep the question under {MAX_QUESTION_CHARS} characters."})
                return

            # Compatibility guard for an older/stale browser page: after a
            # bonus answer, consume the next submitted value as the fake
            # password. Do not pass it to the model, echo it, or retain it.
            handler_type = type(self)
            with handler_type.pending_fake_input_lock:
                consume_as_fake = handler_type.pending_fake_input
                if consume_as_fake:
                    handler_type.pending_fake_input = False
            if consume_as_fake:
                self._send_json(
                    200,
                    {
                        "answer": SIMULATED_END_MESSAGE,
                        "show_demo_code": False,
                        "simulated": True,
                        "simulation_complete": True,
                    },
                )
                return
            if not question:
                self._send_json(400, {"error": "Enter a question first."})
                return

            events: list[dict[str, Any]] = []

            def record_event(name: str, data: dict[str, Any]) -> None:
                if name in {"retrieval", "model_request", "model_response"}:
                    events.append({"event": name, "data": data})

            try:
                agent = CTFAgent(model=model, scenario="rag-simple", on_event=record_event)
                result = agent.run(question)
            except OllamaError:
                self._send_json(503, {"error": "Ollama is unavailable. Check that it is running locally."})
                return
            except Exception:
                self._send_json(500, {"error": "The local demo could not answer. Check the terminal for setup issues."})
                return

            bonus_question = is_bonus_question(question)
            answer = result.answer or "(No answer returned.)"
            simulated = bonus_question and not model_asks_for_password(answer)
            if simulated:
                # Keep the presentation deterministic if a model refuses,
                # summarizes the policy, or otherwise fails to ask for a
                # password. This clearly labeled mock is never sent to the
                # model; the follow-up value stays entirely in the browser.
                answer = SIMULATED_PASSWORD_REQUEST

            trace = {
                "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "scenario": "rag-simple",
                "model": model,
                "user_question": question,
                "events": events,
                "raw_model_answer": result.answer,
                "displayed_answer": answer,
                "displayed_answer_source": "scripted_fallback" if simulated else "model",
            }
            try:
                trace_file = save_trace(trace)
                print(f"Trace saved: {trace_file}")
                trace_error = None
            except OSError as exc:
                trace_file = None
                trace_error = f"Could not save trace: {exc}"

            if bonus_question:
                with type(self).pending_fake_input_lock:
                    type(self).pending_fake_input = True

            self._send_json(
                200,
                {
                    "answer": answer,
                    "show_demo_code": bonus_question,
                    "simulated": simulated,
                    "trace_file": trace_file,
                    "trace_error": trace_error,
                },
            )

    return DemoHandler


def run_ui(model: str, port: int) -> int:
    if not 1 <= port <= 65535:
        print("Port must be between 1 and 65535.")
        return 2
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(model))
    except OSError as exc:
        print(f"Could not start the local UI on 127.0.0.1:{port}: {exc}")
        return 2
    server.daemon_threads = True
    print(f"Local RAG demo for {model}: http://127.0.0.1:{port}")
    print("Only this computer can connect. Press Ctrl+C to stop the UI.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLocal demo stopped.")
    finally:
        server.server_close()
    return 0
