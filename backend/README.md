# Feature 2 backend

FastAPI adapter for the `roadmap_agent` package, living in the same repo as the
package it wraps.

## Local setup

This backend shares one virtual environment with the `roadmap_agent` package
instead of keeping its own — the package is installed into it as an editable
install. The shared venv lives one level above this repo (see "로컬 실행" in
`../README.md`). From this repository's root:

```bash
source ../.venv/bin/activate   # if it doesn't exist yet: python -m venv ../.venv
pip install -e .
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --port 8001
```

Run the SeedUp frontend in another terminal (from the `SeedUp` repo):

```bash
NEXT_PUBLIC_ROADMAP_API_URL=http://localhost:8001 npm run dev
```

- Health check: <http://localhost:8001/health>
- OpenAPI docs: <http://localhost:8001/docs>

The API always uses Roadmap-Agent's deterministic calculations. It uses local evidence by default.

At startup, the backend loads server-only settings from the repo-root `.env` first, then
`backend/.env` if one exists (values already set by the root `.env` win). For local
development, the repo-root `.env` alone is enough — see `../.env.example` for the
`CORS_ORIGINS`/`ENABLE_VECTOR_RAG` keys backend-specifically reads. A separate
`backend/.env` only matters for deployment, where the server gets a minimal, scoped
env file instead of the full repo-root one (see `.env.example` in this folder and the
"배포 (Docker)" section in `../README.md`). Browser code never receives these values.

- Set the `POSTGRES_*` variables to use structured savings and policy repositories.
- Set `ENABLE_VECTOR_RAG=true` with PostgreSQL and `GEMINI_API_KEY` to use pgvector retrieval.
- Set `ENABLE_GEMINI=true` with `GEMINI_API_KEY` to generate the final explanation.

Keep both flags disabled during ordinary local UI development to avoid external API costs.

## Conversation session storage

Conversation state (LangGraph checkpoints keyed by `threadId`) is stored in a local SQLite
file so a session survives backend restarts, including `uvicorn --reload` picking up a code
change mid-test. Each thread is deleted automatically once it has been idle past its TTL — no
chat content is kept permanently.

- `CONVERSATION_STORE_PATH` (default: `backend/app/.data/conversations.sqlite`): file location.
- `CONVERSATION_TTL_SECONDS` (default: `1800`, 30 minutes): idle time before a thread's state is deleted.

The file (and its `-wal`/`-shm` companions) is git-ignored and safe to delete at any time —
doing so just resets every active conversation.
