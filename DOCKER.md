# Docker Guide for This Project

## The Big Picture

This project runs three containers together:

```
Your Code (host machine)
      |
      | (volume mount — live sync)
      v
┌─────────────┐     ┌──────────────┐     ┌────────────┐
│   api:8000  │────▶│  db:5432     │     │ redis:6379 │
│  (FastAPI)  │     │ (PostgreSQL) │     │  (cache)   │
└─────────────┘     └──────────────┘     └────────────┘
```

- **api** — your FastAPI app, the code you write
- **db** — PostgreSQL, stores all data persistently
- **redis** — cache, used for rate limiting / session data later

---

## Two Environments: Host vs Container

You have two Python environments and it is important to understand which one is which.

| | Host (your laptop) | Container (Docker) |
|---|---|---|
| Python | `/usr/bin/python3` (system) | `/usr/local/bin/python3` (3.12-slim image) |
| venv path | `.venv/` | `.venv-docker/` |
| Used for | one-off scripts (`scripts/`) | running the API server |
| Command | `uv run <script>` | `docker compose up` |

They share the same source code (via the volume mount) but have **separate virtual environments**.
Never mix them up — that is what caused the `Permission denied` error earlier.

---

## Day-to-Day Development Workflow

### The short answer: you do NOT need to rebuild the image for most changes.

Here is why: `docker-compose.yml` mounts your entire project folder into the container:

```yaml
volumes:
  - .:/app        # your local folder IS /app inside the container
```

And uvicorn runs with `--reload`, which watches for file changes:

```yaml
command: uv run --frozen uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

So the loop for everyday coding is:

```
1. docker compose up          (start everything once)
2. Edit a .py file            (save in your editor)
3. Uvicorn detects the change (auto-reloads in ~1 second)
4. Test with curl / browser   (no restart needed)
```

That's it. The container is always running live code from your disk.

---

## When DO You Need to Rebuild the Image?

Only in these cases:

| Situation | Command |
|---|---|
| Added/removed a Python package (`pyproject.toml` changed) | `docker compose up --build` |
| Changed the `Dockerfile` itself | `docker compose up --build` |
| First time ever running the project | `docker compose up --build` |

For everything else (editing `.py` files, changing `.env`, adding new routes, fixing bugs) a rebuild is **not needed**.

---

## Common Commands

### Start everything
```bash
docker compose up
```

### Start in background (detached)
```bash
docker compose up -d
```

### Stop everything
```bash
docker compose down
```

### Stop and wipe the database (fresh start)
```bash
docker compose down -v
```
> Warning: `-v` deletes the postgres volume — all data is lost.

### Rebuild the API image (after adding packages)
```bash
docker compose up --build
```

### Restart only the API (after changing .env)
```bash
docker compose restart api
```

### View live logs
```bash
docker compose logs -f api
docker compose logs -f db
```

### Open a shell inside the running API container
```bash
docker compose exec api bash
```

### Run a database migration
```bash
docker compose exec api uv run --frozen alembic upgrade head
```

### Run a one-off script (on your HOST, not inside Docker)
```bash
uv run scripts/create_user.py --name "Daniel"
```
> Scripts that connect to the database use `localhost:5433` (the port exposed to your host).
> The API container uses `db:5432` (internal Docker network). Both point to the same database.

---

## Adding a New Python Package

```bash
# 1. Add it on your host
uv add some-package

# 2. Rebuild the image so the container has it too
docker compose up --build
```

---

## Database Migrations (Alembic)

When you add or change a model:

```bash
# 1. Generate a new migration file
docker compose exec api uv run --frozen alembic revision --autogenerate -m "describe your change"

# 2. Apply it
docker compose exec api uv run --frozen alembic upgrade head
```

The migration files are created inside the container but land in your `migrations/versions/` folder
because of the volume mount — so they show up on your host immediately and can be committed to git.

---

## The `.venv-docker` Directory

This is the virtual environment used **inside the container only**. It is created automatically
the first time `docker compose up` runs and lives at `/app/.venv-docker` inside the container
(which appears as `.venv-docker/` in your project folder on disk).

You should:
- **Ignore it** — it is not for you to use directly
- **Not delete it manually** (Docker will recreate it, but it takes ~30 seconds)
- **Not commit it** — add it to `.gitignore` if not already there

Your local `.venv/` (created by `uv run` on your host) is completely separate and is what
`scripts/create_user.py` uses.

---

## The Full Pipeline (Summary)

```
Write code on host
      │
      │  (volume mount makes it instantly available in container)
      ▼
Container runs it live with --reload
      │
      │  (when you add a package or change Dockerfile)
      ▼
docker compose up --build  ←── only then do you rebuild
      │
      │  (when ready to ship / deploy)
      ▼
docker build -t finance-api .   ←── build a production image
docker push ...                 ←── push to a registry (future)
```

---

## Quick Reference Card

| Task | Command |
|---|---|
| Start dev environment | `docker compose up` |
| Stop dev environment | `docker compose down` |
| Rebuild after adding package | `docker compose up --build` |
| Apply DB migrations | `docker compose exec api uv run --frozen alembic upgrade head` |
| Reload env vars | `docker compose restart api` |
| Shell inside container | `docker compose exec api bash` |
| Run a local script | `uv run scripts/myscript.py` |
| View API logs | `docker compose logs -f api` |
| Wipe all data and restart | `docker compose down -v && docker compose up --build` |
