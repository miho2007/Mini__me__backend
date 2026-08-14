import os
import time
import uuid
from pathlib import Path

from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
from groq import Groq

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "40"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.8"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "1024"))

if not GROQ_API_KEY:
    print("WARNING: GROQ_API_KEY is not set. Set it in your environment / Railway variables.")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

# Load the Mini-Me system prompt from disk once at startup.
SYSTEM_PROMPT_PATH = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# In-memory session store (simple prototype-level "memory")
# ---------------------------------------------------------------------------
# NOTE: this is a prototype-grade store. It lives in process memory, so it
# resets on redeploy/restart and won't be shared across multiple Railway
# instances. If you outgrow it, swap this dict for Redis/Postgres later.

SESSIONS: dict[str, list[dict]] = {}


def get_session_history(session_id: str) -> list[dict]:
    return SESSIONS.setdefault(session_id, [])


def trim_history(history: list[dict]) -> list[dict]:
    if len(history) > MAX_HISTORY_MESSAGES:
        return history[-MAX_HISTORY_MESSAGES:]
    return history


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)  # allow your frontend (web/mobile) to call this from any origin


@app.get("/")
def root():
    return jsonify(
        {
            "status": "ok",
            "service": "mini-me-backend",
            "model": GROQ_MODEL,
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "healthy"})


@app.post("/session")
def create_session():
    """Create a fresh conversation session and return its id."""
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = []
    return jsonify({"session_id": session_id})


@app.delete("/session/<session_id>")
def reset_session(session_id: str):
    SESSIONS.pop(session_id, None)
    return jsonify({"status": "cleared", "session_id": session_id})


def build_messages(session_id: str | None, client_history: list | None, user_message: str):
    """
    Two supported modes:
      1) Stateful: pass "session_id" -> server keeps history in memory.
      2) Stateless: pass full "history" array from the client each call.
    If neither is given, it's treated as a single-turn conversation.
    """
    if session_id:
        history = get_session_history(session_id)
    elif client_history:
        history = client_history
    else:
        history = []

    history.append({"role": "user", "content": user_message})
    history[:] = trim_history(history)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    return messages, history


@app.post("/chat")
def chat():
    """
    Request body (JSON):
    {
      "message": "hey what's up",
      "session_id": "optional-uuid",       // for server-side memory
      "history": [ {role, content}, ... ], // optional, for stateless clients
      "stream": false
    }
    """
    if client is None:
        return jsonify({"error": "GROQ_API_KEY is not configured on the server."}), 500

    data = request.get_json(silent=True) or {}
    user_message = (data.get("message") or "").strip()
    session_id = data.get("session_id")
    client_history = data.get("history")
    stream = bool(data.get("stream", False))

    if not user_message:
        return jsonify({"error": "'message' is required."}), 400

    messages, history = build_messages(session_id, client_history, user_message)

    if stream:
        return Response(
            stream_with_context(
                _stream_response(messages, history, session_id)
            ),
            mimetype="text/event-stream",
        )

    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
    except Exception as e:
        return jsonify({"error": f"Groq API error: {e}"}), 502

    reply = completion.choices[0].message.content

    history.append({"role": "assistant", "content": reply})
    if session_id:
        SESSIONS[session_id] = trim_history(history)

    return jsonify(
        {
            "reply": reply,
            "session_id": session_id,
            "history": history,
            "model": GROQ_MODEL,
        }
    )


def _stream_response(messages, history, session_id):
    """Server-Sent Events generator for streaming replies."""
    full_reply = ""
    try:
        stream = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                full_reply += delta
                yield f"data: {delta}\n\n"
    except Exception as e:
        yield f"data: [ERROR] {e}\n\n"
        return

    history.append({"role": "assistant", "content": full_reply})
    if session_id:
        SESSIONS[session_id] = trim_history(history)

    yield "event: done\ndata: [DONE]\n\n"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=True)
