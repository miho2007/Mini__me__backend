# Mini-Me Backend

Flask backend that powers "Mini-Me" using the Groq API. It loads
`system_prompt.md` (your full Mini-Me instruction set) as the system prompt
on every request, so you can edit that file to tune personality without
touching code.

## Files

- `app.py` — the whole backend (Flask + Groq)
- `system_prompt.md` — your Mini-Me instructions (edit this to tweak personality)
- `requirements.txt` — Python deps
- `Procfile` / `railway.json` — Railway/Heroku-style deploy config
- `.env.example` — copy to `.env` for local dev

## 1. Local setup

```bash
cd mini-me-backend
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and paste your GROQ_API_KEY (from https://console.groq.com/keys)
python app.py
```

Server runs at `http://localhost:8080`.

## 2. API

### `GET /health`
Simple healthcheck. Returns `{"status": "healthy"}`.

### `POST /session`
Creates a new conversation session (server remembers history in memory).

```json
{ "session_id": "b1f0..." }
```

### `DELETE /session/<session_id>`
Clears that session's history.

### `POST /chat`
Main endpoint. Two ways to use it:

**A) Stateful (server remembers the conversation)**
```json
POST /chat
{
  "session_id": "b1f0...",
  "message": "yo what do you think about learning rust"
}
```

**B) Stateless (your frontend keeps the history and sends it every time)**
```json
POST /chat
{
  "message": "yo what do you think about learning rust",
  "history": [
    {"role": "user", "content": "hey"},
    {"role": "assistant", "content": "yo, what's up"}
  ]
}
```

Response:
```json
{
  "reply": "honestly rust is cool but the learning curve is real...",
  "session_id": "b1f0...",
  "history": [ ... full updated history ... ],
  "model": "llama-3.3-70b-versatile"
}
```

**Streaming**: pass `"stream": true` in the body and read the response as
`text/event-stream` (Server-Sent Events). Each event is a chunk of text;
a final `event: done` marks the end.

## 3. Deploy to Railway

1. Push this folder to a GitHub repo (or use `railway up` from the CLI).
2. In Railway: **New Project → Deploy from GitHub repo**, pick this repo.
3. Railway auto-detects Python via Nixpacks and will use the `Procfile` /
   `railway.json` start command automatically.
4. Go to your service **Variables** tab and add:
   - `GROQ_API_KEY` = your key
   - (optional) `GROQ_MODEL`, `TEMPERATURE`, `MAX_TOKENS`, `MAX_HISTORY_MESSAGES`
5. Deploy. Railway gives you a public URL — that's your backend endpoint for
   the frontend/app to call.

## 4. Notes / prototype-level caveats

- Session memory is stored **in-process (a Python dict)**. It resets on
  redeploy/restart, and won't be shared if you scale to multiple replicas.
  Fine for a first version — swap in Redis or Postgres later if you need
  persistent/multi-instance memory.
- CORS is wide open (`CORS(app)`) so you can hit this from any frontend
  while prototyping. Lock it down (`CORS(app, origins=[...])`) before you
  ship this somewhere public.
- Groq models change over time — check
  `https://console.groq.com/docs/models` if `GROQ_MODEL` ever 404s, and
  swap in whatever the current fast/large model name is.
