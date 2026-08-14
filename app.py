import os
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from groq import Groq

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_HISTORY_MESSAGES = int(os.environ.get("MAX_HISTORY_MESSAGES", "40"))
TEMPERATURE = float(os.environ.get("TEMPERATURE", "0.8"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "1024"))
ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")

if not GROQ_API_KEY:
    print("WARNING: GROQ_API_KEY is not set. Set it in your environment / Render dashboard.")

client = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None

SYSTEM_PROMPT_PATH = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# In-memory session store (prototype-level "memory")
# ---------------------------------------------------------------------------
# Lives in process memory: resets on redeploy/restart, not shared across
# multiple instances. Swap for Redis/Postgres later if you need that.

SESSIONS: dict[str, list[dict]] = {}


def get_session_history(session_id: str) -> list[dict]:
    return SESSIONS.setdefault(session_id, [])


def trim_history(history: list[dict]) -> list[dict]:
    if len(history) > MAX_HISTORY_MESSAGES:
        return history[-MAX_HISTORY_MESSAGES:]
    return history


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    history: Optional[list[Message]] = None
    stream: bool = False


class ChatResponse(BaseModel):
    reply: str
    session_id: Optional[str] = None
    history: list[Message]
    model: str


class SessionResponse(BaseModel):
    session_id: str


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Mini-Me Backend", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGINS.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {"status": "ok", "service": "mini-me-backend", "model": GROQ_MODEL}


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/session", response_model=SessionResponse)
def create_session():
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = []
    return SessionResponse(session_id=session_id)


@app.delete("/session/{session_id}")
def reset_session(session_id: str):
    SESSIONS.pop(session_id, None)
    return {"status": "cleared", "session_id": session_id}


def build_messages(req: ChatRequest):
    if req.session_id:
        history = get_session_history(req.session_id)
    elif req.history:
        history = [m.model_dump() for m in req.history]
    else:
        history = []

    history.append({"role": "user", "content": req.message})
    history[:] = trim_history(history)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    return messages, history


@app.post("/chat")
def chat(req: ChatRequest):
    if client is None:
        raise HTTPException(status_code=500, detail="GROQ_API_KEY is not configured on the server.")

    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="'message' is required.")

    messages, history = build_messages(req)

    if req.stream:
        return StreamingResponse(
            _stream_response(messages, history, req.session_id),
            media_type="text/event-stream",
        )

    try:
        completion = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Groq API error: {e}")

    reply = completion.choices[0].message.content
    history.append({"role": "assistant", "content": reply})

    if req.session_id:
        SESSIONS[req.session_id] = trim_history(history)

    return ChatResponse(
        reply=reply,
        session_id=req.session_id,
        history=history,
        model=GROQ_MODEL,
    )


def _stream_response(messages, history, session_id):
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
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
