# Learning Guide — Personal Finance Agent

> A self-study path from "I can read Python" to "I can build and maintain this project alone."
> Pair this with `CLAUDE.md` (architectural reference) and `CODEBASE.md` (pre-Phase-5 snapshot).

---

## 0. How to use this guide

This is a 3-part document:

1. **Part A — Codebase tour.** What every directory does, in dependency order. Read top-to-bottom on day one.
2. **Part B — Navigation playbook.** Repeatable recipes for "I want to add a feature / debug X / understand Y." Use this when working.
3. **Part C — Learning path.** Books + video resources, ordered beginner → pro. Each topic maps to where in *this* codebase you'll see it.

**Don't try to learn everything before touching the code.** The codebase is the textbook. Run it locally, break it on purpose, and use Part C to fill the gaps you actually hit.

---

# PART A — Codebase Tour

## A.1 The big picture in 60 seconds

You're looking at a **FastAPI + Postgres + Redis** backend with a **Telegram bot** glued on. The product captures financial transactions from three sources (manual API, iPhone Shortcut, future Gmail parser), stores them in Postgres, and exposes a chat interface that uses an **LLM (Claude)** to extract intent from Spanish messages and answer financial questions via tool-use.

Three top-level Python packages matter:

| Package | Role | When you'll touch it |
|---|---|---|
| `api/` | FastAPI HTTP layer + ORM models + business services | Adding endpoints, schema changes, business logic |
| `app/queries/` | Read-only conversational query layer (LLM + tools) | Adding a new "answerable question" the bot can handle |
| `bot/` | Telegram pipeline (aiogram) — extractor → router → dispatcher → delivery | Bot behavior, Spanish messages, callback flows |

Plus three support directories:

| Directory | Role |
|---|---|
| `migrations/versions/` | Hand-written Alembic migrations, numbered `0001_…` through `0010_…` |
| `tests/` | Pytest async suite (254 tests as of Phase 6a) |
| `docs/curl/` and `scripts/` | Phase-gate smoke scripts you run after big changes |

Empty placeholders (`agent/`, `jobs/`, `parsers/`) exist for future phases. Ignore them.

---

## A.2 Read-order for new contributors

Follow this exact sequence. Each file builds on the previous.

### Step 1 — Boot path (understand startup)

1. `pyproject.toml` — pinned dependency versions. Note: SQLAlchemy 2.x async, Pydantic v2, anthropic SDK, aiogram v3.
2. `docker-compose.yml` — three services: `db` (Postgres 16), `redis` (Redis 7), `api` (Uvicorn).
3. `.env.example` — every config knob the app reads.
4. `api/config.py` — Pydantic `BaseSettings` that consumes `.env`.
5. `api/database.py` — async SQLAlchemy engine + `get_db` FastAPI dependency.
6. `api/redis_client.py` — singleton async Redis connection.
7. `api/main.py` — `lifespan` context manager, router registration, `/health` and `/health/ready`.

Stop here and run it: `docker compose up -d`, then `curl localhost:8000/health`.

### Step 2 — Domain model (understand the data)

8. `api/models/base.py` — `DeclarativeBase` for all ORM classes.
9. `api/models/__init__.py` — imports every model; this is what Alembic sees.
10. Read models in this order to understand relationships:
    - `user.py` → `account.py` → `transaction.py` (the spine)
    - `recurring_bill.py` → `bill_occurrence.py` → `notification_rule.py` → `notification_event.py` (Phase 4)
    - `pending_confirmation.py` → `user_nudge.py` (Phase 5d)
    - `llm_extraction.py` → `llm_query_dispatch.py` (Phase 5b/6a observability)
11. `migrations/versions/0001_initial_schema.py` through `0010_…` — read in numeric order. This is the schema's history.

### Step 3 — HTTP surface (understand the API)

12. `api/dependencies.py` — `current_user` (with the `X-User-Id` dev shim) and `current_user_via_token` (strict).
13. `api/schemas/transaction.py` — Pydantic v2 request/response models. Notice the dual `ShortcutTransactionCreate` schema.
14. `api/routers/transactions.py` — the most-developed router. Pattern to copy.
15. `api/routers/users.py` — registration + token rotation.
16. `api/routers/jobs.py` — the three Phase 4 batch jobs + Phase 5d evaluator/delivery jobs.
17. Then skim the rest of `api/routers/`. They follow the same shape.

### Step 4 — Services (understand the business logic)

18. `api/services/recurrence.py` — RRULE + frequency expansion → `bill_occurrences`. The hardest pure-Python file.
19. `api/services/nudges/` — read in this order:
    - `policy.py` (constants — rate limit, silence threshold, quiet hours)
    - `evaluators/` (3 pure functions that emit candidate nudges)
    - `orchestrator.py` (dedup + silence filter + insert)
    - `delivery.py` (the 4 anti-saturation rules in code)
    - `actions.py` (state machine — dismiss/act + auto-silence)
    - `phrasing.py` (LLM call that writes the Spanish copy)
20. `api/services/llm_extractor/` — the Anthropic tool-use call that powers the bot:
    - `schema.py` (`ExtractionResult` Pydantic contract)
    - `prompt.py` (system prompt + cache_control blocks)
    - `client.py` (Anthropic SDK wrapper)
    - `runner.py` (glue + persistence to `llm_extractions`)

### Step 5 — Bot pipeline (understand the chat layer)

21. `bot/redis_keys.py` — every Redis key the bot uses, with TTL contracts.
22. `bot/app.py` — aiogram `Bot` + `Dispatcher` + start/stop hooks for `lifespan`.
23. `bot/handlers.py` — aiogram routes (text, callbacks, commands).
24. `bot/pipeline.py` — **the brain.** ~540 lines. Resolve user → rate limit → command short-circuit → LLM → route → dispatch → reply. Read top-to-bottom twice.
25. `bot/pending.py` + `bot/pending_db.py` — the two-tier proposal store (Redis 5min + Postgres 48h).
26. `bot/delivery_send.py` — sanitize HTML → split for Telegram's 4096-char limit → send.

### Step 6 — Query layer (understand how questions are answered)

27. `app/queries/prompts/system.py` — system prompt for the query LLM.
28. `app/queries/tools/base.py` — the `Tool` abstraction.
29. `app/queries/tools/transactions.py` — the most-used tool. Read it carefully.
30. `app/queries/llm_client.py` — Anthropic tool-use loop (iteration cap, cache, token accounting).
31. `app/queries/dispatcher.py` — orchestrates the loop + history + audit row.
32. `app/queries/history.py` — Redis-backed conversation history (24h TTL).
33. `app/queries/delivery.py` — error→Spanish-message mapping.

### Step 7 — Tests (understand what "correct" means)

34. `tests/conftest.py` — the per-test NullPool engine pattern. **Critical.** Async tests share an event loop — connection pools that span loops cause flaky failures.
35. Pick one passing test from each phase and trace the assertions back to the code:
    - `tests/test_telegram_dispatcher.py` (write dispatch)
    - `tests/test_nudges_evaluators.py` (Phase 5d)
    - `tests/test_phase_6a_block5b_e2e.py` (full query loop)
36. `scripts/phase5b_smoke.sh` and `docs/curl/phase-5d.sh` — end-to-end manual smokes. Run these locally to see the system breathe.

---

## A.3 Mental model cheat sheet

Pin these in your head — they show up everywhere:

- **Money is `NUMERIC(12,2)` or `NUMERIC(14,2)`.** Negative = expense, positive = income. Never use `float`.
- **All timestamps are `TIMESTAMPTZ` in UTC.** The user's local timezone (`users.timezone`, default `America/Costa_Rica`) is applied at display/quiet-hours time.
- **All PKs are UUIDv4** via `gen_random_uuid()`.
- **Multi-tenancy = `user_id` FK on every domain table.** `ON DELETE RESTRICT`. The `current_user` dependency resolves the tenant.
- **The LLM never decides whether to act.** Extractor produces structured JSON; deterministic Python code routes and commits. The query dispatcher is the *only* LLM-on-the-hot-path component.
- **Redis is the source of truth for durable bot state.** aiogram FSM is for transient in-handler bookkeeping only.
- **Migrations are hand-written.** No `--autogenerate`. Every schema change → new numbered file.

---

# PART B — Navigation Playbook

Recipes you'll repeat dozens of times. Bookmark this section.

## B.1 "I want to add a new HTTP endpoint"

1. **Schema first.** Add Pydantic v2 request/response classes in `api/schemas/<resource>.py`. Use `model_config = ConfigDict(from_attributes=True)` for ORM read models.
2. **Model.** If a new table: create `api/models/<name>.py`, register it in `api/models/__init__.py`.
3. **Migration.** Copy the latest `migrations/versions/00XX_…py` as a template. Bump the prefix. Hand-write `upgrade()` and `downgrade()`.
4. **Router.** Create or edit `api/routers/<resource>.py`. Use `APIRouter(prefix="/api/v1/<resource>", tags=["<resource>"])`. Inject `db: AsyncSession = Depends(get_db)` and `user = Depends(current_user)` (or `current_user_via_token` if it must reject the dev shim).
5. **Mount.** Add `app.include_router(<resource>.router)` in `api/main.py`.
6. **Test.** New file in `tests/test_<resource>.py`. Follow the NullPool pattern from `conftest.py`.
7. **Smoke.** Add a curl example to `docs/curl/` if it's a phase-gate feature.

## B.2 "I want to add a new query the bot can answer"

1. **Tool definition.** New file in `app/queries/tools/<name>.py`. Subclass `Tool`. Define `input_schema` (JSON Schema dict), `name`, `description`, and `async def run(ctx, args)`.
2. **Register.** Add to `app/queries/tools/__init__.py`.
3. **Update system prompt.** `app/queries/prompts/system.py` — Claude needs to know the tool exists and when to call it.
4. **Test.** Write a unit test in `tests/test_tool_<name>.py` (no LLM). Then add an e2e to one of the `tests/test_phase_6a_block*_e2e.py` patterns (uses real Anthropic — gate behind an env var).
5. **No new dispatcher logic needed.** The loop in `llm_client.py` picks up registered tools automatically.

## B.3 "I want to add a Telegram command (e.g. `/foo`)"

1. **Handler.** Add an aiogram handler in `bot/handlers.py` decorated with `@router.message(Command("foo"))`.
2. **If it's a new pipeline branch:** edit `bot/pipeline.py`. Add the command short-circuit *before* the LLM extractor block — commands must never burn tokens.
3. **Spanish copy.** Add the user-facing strings to `bot/messages_es.py`. Don't inline them.
4. **Test.** Drive it via `POST /api/v1/telegram/_simulate` in dev.

## B.4 "I want to change a Pydantic schema"

- **Backwards compat.** Pydantic v2 `model_config = ConfigDict(extra="forbid")` is used in some places (e.g. `ExtractionResult`). Adding a field there breaks deserialization of stored rows. Use `extra="allow"` or migrate the data.
- **Schema is also part of the LLM contract.** If the field is in `ExtractionResult` or a query-tool input schema, the system prompt and fixture tests must be updated too. Re-record extractor fixtures: see notes in `tests/test_llm_extractor.py`.

## B.5 "I want to add a new migration"

```bash
# 1. Copy the latest migration as a template
cp migrations/versions/0010_phase6a_query_dispatch_cache_metrics.py \
   migrations/versions/0011_<your_change>.py

# 2. Edit revision/down_revision. Write upgrade()/downgrade() by hand.

# 3. Apply it
alembic upgrade head

# 4. Verify it can roll back cleanly
alembic downgrade -1 && alembic upgrade head
```

**Never run `alembic revision --autogenerate`.** Project policy.

## B.6 "Tests are flaky — what do I check first?"

In order of likelihood:

1. **Cross-event-loop asyncpg connection.** The pattern in `tests/conftest.py` creates a `NullPool` engine *per test* so connections never escape their loop. New test files must follow it.
2. **Redis state leaking between tests.** Use a fixture that flushes the test DB index, or scope keys to a unique test-run UUID.
3. **Tool-loop tests calling the real Anthropic API.** Mark them `@pytest.mark.skipif(not ANTHROPIC_KEY)` or use the `FixtureLLMClient` pattern from `tests/test_llm_extractor.py`.
4. **`datetime.utcnow()` deprecation warnings.** Tracked tech debt — not a real failure but pytest may surface them.

## B.7 "I want to run the system end-to-end locally"

```bash
# 1. Boot infra + API
docker compose up -d

# 2. Apply migrations
alembic upgrade head

# 3. Register a user (returns shortcut_token ONCE — save it)
curl -X POST localhost:8000/api/v1/users/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@test.local","full_name":"You"}'

# 4. Run the phase smokes
bash scripts/phase5a_smoke.sh
bash scripts/phase5b_smoke.sh
bash docs/curl/phase-5d.sh
bash docs/curl/phase-6a.sh
```

If `phase5b_smoke.sh` passes against `_simulate`, the entire bot pipeline (minus a real Telegram connection) is healthy.

## B.8 "I want to change the LLM prompt"

- **Extractor prompt:** `api/services/llm_extractor/prompt.py`. Has `cache_control={"type":"ephemeral"}` on the system + tool-schema blocks — keep those when editing.
- **Query prompt:** `app/queries/prompts/system.py`.
- **After any prompt change:** re-record the relevant fixtures (`tests/fixtures/`). Drift in test assertions is a *signal*, not a nuisance — investigate before relaxing them.
- **Cost watch.** Phase 6a uses Sonnet for queries, Haiku for extraction. Don't swap to Opus in production code without checking `LLM_DAILY_TOKEN_BUDGET_PER_USER`.

## B.9 "How do I find where X is implemented?"

| Looking for | Search this |
|---|---|
| An endpoint by URL | `grep -r '@router' api/routers/ \| grep -i <path-fragment>` |
| A Spanish error message | `grep -r '<text>' bot/messages_es.py` |
| A DB column | `grep -r '<column_name>' api/models/ migrations/versions/` |
| A Redis key | `grep -r '<prefix>' bot/redis_keys.py app/queries/history.py` |
| A tool the LLM calls | `app/queries/tools/__init__.py` — single registry |
| A migration that touched a table | `grep -l '<table>' migrations/versions/` |

---

# PART C — Learning Path

> Resources are listed by **canonical title and author/creator** so you can find the current edition. Avoid pirated or out-of-date copies — these technologies move fast (especially the LLM tooling).

Each section has:
- **Why this matters here** — the line in *this* codebase the topic shows up
- **Resources** — books and video creators worth your time, ordered beginner → pro

---

## C.1 Foundations: Modern Python (3.10+)

**Why this matters here:** Type hints everywhere, `async/await` on every DB and HTTP call, Pydantic models, `match` statements, dataclasses. If you can't read `async def get_user(db: AsyncSession) -> User | None:` instantly, start here.

### Books

1. **"Python Crash Course" — Eric Matthes** *(no Python at all? start here)*
2. **"Fluent Python" (2nd ed) — Luciano Ramalho** ⭐ *the* book for going from "I write Python" to "I understand Python." Chapters on iterators, decorators, async, and typing are directly applicable.
3. **"Robust Python" — Patrick Viafore** — type hints, protocols, structural patterns. Maps onto every `Mapped[...]` and `Annotated[...]` in this repo.
4. **"Python Concurrency with asyncio" — Matthew Fowler** — once you've seen `async`, this is where you understand it.

### Video creators (search by name on YouTube)

- **mCoding (James Murphy)** — short, dense, correct. "Async fundamentals" and "type hints" playlists.
- **ArjanCodes** — design + clean code in modern Python. Beginner-friendly.
- **Real Python** (free articles, paid videos at realpython.com) — solid reference quality.

---

## C.2 Web APIs: FastAPI

**Why this matters here:** The entire HTTP layer. Every router under `api/routers/` is a FastAPI `APIRouter`. Dependencies (`Depends(get_db)`, `Depends(current_user)`) are the auth + persistence injection model.

### Official docs (treat as a textbook)

- **fastapi.tiangolo.com** — the official tutorial is exceptional. Read "Tutorial - User Guide" cover to cover, then "Advanced User Guide."

### Books

1. **"FastAPI" — Bill Lubanovic** (O'Reilly, 2024) — the only mature print book on the topic.
2. **"Building Python Web APIs with FastAPI" — Abdulazeez Abdulazeez Adeshina** — project-driven, accessible.

### Videos

- **ArjanCodes** — multiple FastAPI architecture videos. Good for "how do I structure a real app."
- **Tiangolo's own talks** (search "Sebastián Ramírez FastAPI") — design rationale from the author.
- **TestDriven.io** — paid, but their FastAPI + async + Postgres course is the closest thing to "build this exact stack" in tutorial form.

---

## C.3 Databases: PostgreSQL + SQL

**Why this matters here:** Postgres-only project. UUIDs, JSONB, partial indexes, CHECK constraints, FKs with `ON DELETE` semantics, composite indexes. See `migrations/versions/0006_phase5a_users_multitenant.py` for a tour.

### Books

1. **"Learning SQL" (3rd ed) — Alan Beaulieu** *(ground floor)*
2. **"PostgreSQL: Up and Running" — Regina Obe & Leo Hsu** — Postgres-specific features. JSONB chapter is gold.
3. **"The Art of PostgreSQL" — Dimitri Fontaine** ⭐ Postgres as a *design tool*, not just a store. Window functions, CTEs, `LATERAL`, advanced JSONB. Read this when you stop being scared of writing SQL by hand.
4. **"Database Internals" — Alex Petrov** *(pro level)* — how the engine actually works. Read once you've hit a real performance problem.

### Videos

- **Hussein Nasser** (YouTube) — networking and database fundamentals. Pragmatic and excellent on indexes and replication.
- **PGCon / PostgresOpen recorded talks** — the conferences are on YouTube. "Postgres Indexing Internals" by Bruce Momjian is a classic.

---

## C.4 ORMs and Migrations: SQLAlchemy 2.x + Alembic

**Why this matters here:** Every model in `api/models/`, every `select(…).where(…)` in services and routers. SQLAlchemy 2.x's `Mapped` / `mapped_column` typed style is what this project uses — old tutorials show the legacy `Column()` syntax, **skip those**.

### Documentation (primary source)

- **docs.sqlalchemy.org** — the "Unified Tutorial" for 2.0 is the right starting point. The legacy 1.x tutorial is *still* indexed — make sure the URL says `2.0/` or `latest/`.
- **alembic.sqlalchemy.org** — official Alembic tutorial.

### Books / longform

1. **"Essential SQLAlchemy" (2nd ed) — Jason Myers & Rick Copeland** — pre-2.0 syntax but the conceptual model is unchanged.
2. **"Architecture Patterns with Python" — Harry Percival & Bob Gregory** ⭐ uses SQLAlchemy as the persistence layer of a clean-architecture app. The repository + unit-of-work patterns illuminate why this codebase keeps services and routers thin.

### Videos

- **Mike Bayer's PyCon talks** (the SQLAlchemy maintainer) — search "Mike Bayer SQLAlchemy 2.0." Authoritative.

---

## C.5 Validation: Pydantic v2

**Why this matters here:** Every request/response in `api/schemas/` and the LLM contract in `ExtractionResult`. v2 is a near-rewrite of v1 — `model_validate`, `model_config`, `Field(...)`, `Annotated[...]`, validators. Old StackOverflow answers will mislead you.

### Resources

- **docs.pydantic.dev** — official migration guide v1→v2 is essential if you've used v1 before.
- **Tiangolo + Pydantic talks on YouTube** — short, focused.

No book yet matches v2 in depth — the docs are the primary source.

---

## C.6 Async Python and Concurrency

**Why this matters here:** Every IO operation in this app is `async`. The bot's `typing_action()` background task, the query LLM loop, asyncpg connections, Redis. Mistakes here look like flaky tests (see B.6) or 30-second hangs.

### Resources

1. **"Python Concurrency with asyncio" — Matthew Fowler** ⭐ best single resource.
2. **"Using Asyncio in Python" — Caleb Hattingh** — short, opinionated, excellent.
3. **mCoding YouTube** — "Asyncio is hard but really good" and similar.
4. **Łukasz Langa's PyCon keynotes** on asyncio internals (search his name).

---

## C.7 Caching, Queues, Sessions: Redis

**Why this matters here:** Every line in `bot/redis_keys.py` and `app/queries/history.py`. Pairing codes, pending proposals, rate limits, query history all live in Redis.

### Books

1. **"Redis in Action" — Josiah Carlson** — older but the data-model chapters age well.
2. **"Redis: The Definitive Guide" — by O'Reilly** — newer reference.

### Videos

- **Hussein Nasser's Redis playlist.**
- **Redis University** (free courses at university.redis.com) — official, well-paced.

---

## C.8 LLMs and Tool-Use: Anthropic Claude API

**Why this matters here:** `api/services/llm_extractor/`, `app/queries/llm_client.py`, the system prompts, the cache-control blocks, the tool-use loop. This is the *core differentiator* of the project.

### Primary sources (treat as required reading)

- **docs.anthropic.com** — read in this order:
  1. "Messages API" reference
  2. "Tool use" guide (the entire flow this project implements)
  3. "Prompt caching" guide (`cache_control={"type":"ephemeral"}` is how Phase 5b/6a survives token costs)
  4. "Vision" and "Extended thinking" — useful background even if unused here
- **Anthropic Cookbook** (github.com/anthropics/anthropic-cookbook) — runnable notebooks for tool-use, structured output, RAG.

### Books and longform

LLM-engineering books age in months, not years. Treat any book older than ~12 months as stale on tool-use, but durable on principles.

1. **"Building LLM Apps" — Valentina Alto** — broad introduction.
2. **"AI Engineering" — Chip Huyen** — production patterns. The cost/eval/observability chapters apply directly to this codebase's `llm_extractions` and `llm_query_dispatches` tables.
3. **"Designing Machine Learning Systems" — Chip Huyen** *(adjacent but useful — production ML thinking)*.

### Videos

- **Anthropic's official YouTube channel** — short, frequent, and matched to current API features.
- **Hamel Husain** (blog + talks) — practical eval and prompt engineering.
- **Jason Liu** (Instructor library author) — structured output. The patterns he teaches are exactly what `ExtractionResult` does.

---

## C.9 Telegram Bots: aiogram v3

**Why this matters here:** The entire `bot/` package. v3 is a rewrite from v2 — old tutorials and StackOverflow answers reference incompatible APIs.

### Primary sources

- **docs.aiogram.dev** — official docs. The "Migration FAQ" v2→v3 is critical context.
- **core.telegram.org/bots** — Telegram's own bot platform docs. You need to understand webhooks, inline keyboards, callback data, and message limits (4096 chars — see `bot/delivery_send.py`).

### Videos

- aiogram doesn't have a flagship YouTube creator. Search the official docs first; community videos are often v2.

---

## C.10 Containers and Deployment

**Why this matters here:** `Dockerfile`, `Dockerfile.prod`, `docker-compose.yml`. Production target is a single container with Uvicorn + the bot in webhook mode.

### Resources

1. **"Docker Deep Dive" — Nigel Poulton** *(beginner)*
2. **"The Docker Book" — James Turnbull** *(reference)*
3. **NetworkChuck on YouTube** — Docker fundamentals, very approachable.
4. **Bret Fisher's Docker Mastery** (Udemy) — paid, comprehensive.

---

## C.11 Testing: Pytest + pytest-asyncio

**Why this matters here:** 254 async tests. The NullPool-engine-per-test pattern in `conftest.py` is the *only* way the suite stays stable.

### Resources

1. **"Python Testing with pytest" (2nd ed) — Brian Okken** ⭐ canonical.
2. **Brian Okken's "Test & Code" podcast** — short episodes, applied.
3. **pytest-asyncio docs** — read for the `mode=auto` and event-loop scoping rules used here.

---

## C.12 Software Architecture (the glue)

**Why this matters here:** The "deterministic dispatcher + LLM extractor" split, the "data before AI" gating, the "single migration per change" rule — these are *architectural decisions*. Reading the books below will help you make analogous decisions for new features without breaking the project's principles.

### Books

1. **"The Pragmatic Programmer" (20th anniv ed) — Hunt & Thomas** — universal.
2. **"Architecture Patterns with Python" — Percival & Gregory** ⭐ already mentioned. The single most relevant book.
3. **"Designing Data-Intensive Applications" — Martin Kleppmann** — durable, multi-year reference.
4. **"A Philosophy of Software Design" — John Ousterhout** — short, sharp, opinionated. "Modules should be deep" is exactly the discipline this codebase tries to maintain.
5. **"Refactoring" (2nd ed) — Martin Fowler** *(JavaScript edition, but principles apply).*

---

## C.13 Recommended order if you're starting from scratch

If you can read Python loops and functions but everything in this repo looks like static, work in this order:

| Week(s) | Focus | Resource |
|---|---|---|
| 1–2 | Modern Python + types | "Fluent Python" ch. 1–8 + "Robust Python" |
| 2–3 | SQL fundamentals | "Learning SQL" + run queries against the local Postgres |
| 3–4 | FastAPI tutorial | tiangolo.com/tutorial — build their toy app from scratch |
| 4–5 | SQLAlchemy 2.0 | Official 2.0 Unified Tutorial + read 5 models in `api/models/` |
| 5–6 | Async Python | "Python Concurrency with asyncio" |
| 6 | Pydantic v2 | Official docs migration guide |
| 7 | Pytest async | "Python Testing with pytest" + read `tests/conftest.py` |
| 7–8 | Anthropic API + tool use | docs.anthropic.com + Cookbook notebooks |
| 8 | aiogram v3 | Official docs + read `bot/handlers.py` and `bot/pipeline.py` |
| 9 | Architecture | "Architecture Patterns with Python" |
| 10+ | Postgres mastery | "The Art of PostgreSQL" + run `EXPLAIN ANALYZE` on this app's slow queries |

After week 4 you should be able to add a CRUD endpoint solo. After week 8, you can extend the bot. After week 10, you can confidently rework the architecture.

---

## C.14 What to skip (for now)

These are dead ends for *this* project. Save them for later or never:

- **Django, Flask tutorials** — wrong framework. Skills don't transfer cleanly to FastAPI's dependency-injection model.
- **SQLAlchemy 1.x material** — the syntax is incompatible. Verify everything you read targets 2.0+.
- **LangChain / LlamaIndex courses** — this project deliberately uses the raw Anthropic SDK. Frameworks would hide the cache-control discipline that keeps token cost under control.
- **Vector databases / RAG** — explicitly excluded by `CLAUDE.md` ("What NOT to Build").
- **Self-hosted LLM tutorials** — same reason.
- **WhatsApp Baileys tutorials** — banned by Meta; this project uses Telegram + (eventually) the official WhatsApp Cloud API.

---

## C.15 Two-hour weekly maintenance routine

Once you're up to speed, this keeps the project healthy:

1. `git log --since="1 week"` — review what changed.
2. `pytest -q` — confirm `254 passed` (or current count from `CLAUDE.md`).
3. Run `scripts/phase5b_smoke.sh` and `docs/curl/phase-5d.sh` against a clean DB.
4. Skim Anthropic's changelog (docs.anthropic.com → release notes) — model deprecations and new tool-use features land regularly.
5. Skim aiogram's GitHub releases for breaking changes.
6. Review the **Technical Debt** section of `CLAUDE.md`. Pick one item if you have spare cycles.

---

## Final note

The codebase rewards readers who follow the phase order. If something doesn't make sense, the answer is almost always in the migration that introduced the table or the phase section of `CLAUDE.md`. When in doubt, `git log -p <file>` tells you why.
