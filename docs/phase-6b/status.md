# Phase 6b — Status (Gmail ingestion + reconciliation)

> **CLOSED 2026-05-06.** Phase 6b shipped end-to-end. Decisions locked
> in `docs/phase-6b-decisions.md`. Snapshot below preserved for the
> historical record; pivot points (re-scope, addenda blocks A→D) noted.

## Re-scope

La fila "**6b** — Pushback engine" del roadmap original se renombró a
**6c** y queda diferida. **6b** ahora es la ingesta automática de
notificaciones bancarias desde Gmail con reconciliación contra
transacciones existentes (Shortcut / Telegram).

Razón del cambio: la pushback engine sin un ledger confiable dice
mentiras con voz autoritaria. Primero hay que cerrar el loop de captura
automática de gastos (lo que el iPhone Shortcut cubre parcialmente
hoy), y *después* viene la pushback. La filosofía "data before AI" del
CLAUDE.md aplica directo acá.

## Done — bloques cerrados

| Bloque | Status | Tests | Notas |
|---|---|---|---|
| **B1** schema + migración 0011 | ✅ | — | 4 tablas nuevas (gmail_credentials, bank_notification_samples, gmail_messages_seen, gmail_ingestion_runs) + 3 columnas nuevas en transactions. Migración chequea limpia con `alembic upgrade head`. |
| **B2** GCP setup + OAuth helper | ✅ | 14 | Auth Code flow, scope `gmail.readonly`, JWT HS256 state con nonce one-time en Redis. Doc `docs/phase-6b/gcp-setup.md`. PyJWT agregada como dep. |
| **B3** SecretStore | ✅ | 7 | Protocol + EnvSecretStore (dev) + AzureKeyVaultStore (lazy import; azure libs en `[project.optional-dependencies] azure`). |
| **B4** OAuth endpoints + static | ✅ | 8 | `POST /oauth/start`, `GET /oauth/callback`, `GET /status` + páginas `gmail-connected.html` / `gmail-error.html` + pub/sub `gmail_callback:{user_id}`. **Smoke real validado** — Daniel completó OAuth real y `/status` devolvió `connected:true`. |
| **B5** Telegram handlers + listener | ✅ | 18 | `/conectar_gmail`, `/desconectar_gmail`, `/estado_gmail`, state machine en Redis (`gmail_onboarding:{user_id}`, TTL 30 min), `bot/gmail_listener.py` con `psubscribe("gmail_callback:*")`. |
| **B6** Sample collection + Haiku | ✅ | 9 | Two-step analyzer (vision OCR + text classify) en `services/gmail/sample_analyzer.py`. Photo + text handlers gateados por `_is_awaiting_sample`. Confianza ≥ 0.7 → confirm + activate; < 0.7 → reintenta hasta 3x. |

**Bug fixes aplicados durante smoke** (en bot, no en Phase 6b core):

- `bot/app.py`: `delete_webhook(drop_pending_updates=True)` antes de polling. Sin esto, un webhook viejo del lado de Telegram bloqueaba `getUpdates` con `TelegramConflictError`.
- `bot/handlers.py`: el catch-all `@router.message(F.text)` se cambió a `F.text & ~F.text.startswith("/")` para que slash commands de routers registrados después (como `/conectar_gmail`) tengan oportunidad de matchear. Sin esto, todos los slash commands no-Phase-5b caían en el extractor y devolvían `HELP_TEXT`.

## Re-scope addenda (2026-05-06)

After completing B1–B6 of the original plan, the onboarding model
pivoted from "sample-based bank inference" to "explicit email-based
multi-bank selection". The remaining work was re-grouped as four
addenda blocks:

| Addenda block | Scope |
|---|---|
| **A** ✅ | Multi-bank email-based onboarding. Migration 0012 (`gmail_sender_whitelist`), `/agregar_banco`, `/quitar_banco`, `/estado_gmail` shows full whitelist. Preset taps ask for the user's actual bank email — no preloaded sender lists. |
| **B** ✅ | Original B7–B9: scanner, reconciler, backfill async runner. Wired into `on_activate_callback` via `asyncio.create_task` with `_run_backfill_safe` wrapper. Email extractor split into `services/extraction/email_extractor.py`. |
| **C** ✅ | Original B10–B11: notifier with shadow / batch / per-transaction branches, daily worker `workers/gmail_daily.py` (Container Apps Job), `/aprobar_shadow`, `/rechazar_shadow`, `maybe_send_shadow_summary`. |
| **D** ✅ | Closes phase: `FileSecretStore` (preventive — solves the env-store-loses-tokens trap), real `/agregar_muestra`, `scripts/test_phase_6b_full.sh`, CLAUDE.md "Phase 6b" section, `docs/phase-6b/deployment.md`, `docs/phase-6b/secret-store.md`. |

## Approval gates (final)

- ✅ Después de B4: schema + OAuth flow con curl + browser real.
- ✅ Después de Block A: bank selection con 2 presets + 1 custom.
- ✅ Después de Block B: scanner + reconciler + backfill real.
- ✅ Después de Block C: daily worker via `/admin/gmail/run-daily`.
- ✅ Block D cierra fase.

## Suite state (final)

- **Phase 6b suite**: 226 passing.
- **Pre-existing flaky tests**: 2 (`*_real_llm_*` Phase 6a) — hit Anthropic API, unrelated to 6b.
- **Skipped**: handful of `requires_db` tests when Postgres isn't reachable; pass when it is.

## Out-of-scope explícitamente

Diferido y **registrado** para no perderse:

- `/whitelist` para ver/editar remitentes de la whitelist. Hoy se deriva on-the-fly desde `bank_notification_samples.detected_sender`.
- Nudge a 10 min si el usuario no completó OAuth. Necesita scheduler real.
- `/agregar_muestra` para múltiples samples post-onboarding. El sample obligatorio cubre el caso base.
- Análisis de attachments (PDFs de estados de cuenta). Posible Phase 6b.1.
- UI web de gestión (samples, whitelist, runs). Phase 6d.
- Métricas / dashboards de ingestion. Solo logs estructurados por ahora.
- Soporte Outlook / iCloud / Yahoo. Solo Gmail por ahora.
