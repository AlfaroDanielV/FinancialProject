# Phase 6d — Onboarding & Self-Registration

**Status:** Decisions locked, implementation pending
**Predecessor:** Phase 6c (User memory / behavioral profiling)
**Successor:** Phase 6e (Centro Financiero SPA — full)
**Date locked:** 2026-05-05

---

## 1. Resumen y objetivo

Permitir que cualquier usuario (incluyendo a Daniel, vía dogfooding) auto-registre el set completo de entidades estructurales que el bot necesita para operar bien:

- **Cuentas** (bancarias, efectivo, tarjetas como pasivo)
- **Deudas** (préstamos, hipotecas, líneas de crédito) con French amortization completa
- **Ingresos recurrentes** (incluyendo ciclos CR-specific: aguinaldo, salario escolar)
- **Gastos fijos recurrentes**

Onboarding es **opcional progresivo, no bloqueante**: el bot funciona con DB vacía y va pidiendo información lazy cuando la necesita, complementado con un único prompt al `/start` que ofrece el SPA para setup completo.

**6d desbloquea P8 (beta users)** — ningún beta user puede empezar con DB vacía sin un flow guiado de onboarding.

---

## 2. Out of scope (explícito)

Lo siguiente NO entra en 6d, queda para 6e o más adelante:

- Dashboards, gráficos, reportes en SPA
- Vista de transacciones individuales en SPA
- Edición masiva
- Filtros avanzados
- Categorías custom (6d seedea defaults; edición en 6e)
- Metas u objetivos financieros
- Tracking de cuotas pagadas individualmente (la amortización en 6d es proyección, no histórico de pagos)
- Abonos extraordinarios y recálculo (Ley 7472)
- Sesión persistente en SPA (auth en 6d es single-use magic-link)
- Refresh tokens en SPA

---

## 3. Decisiones arquitectónicas congeladas

### 3.1 Modalidad: híbrida (chat + web link)

Cuentas e ingresos pueden registrarse conversational (4–5 campos, validación simple). Deudas siempre delegan al SPA por la complejidad de validaciones cruzadas y preview de amortización. Gastos fijos pueden ambos canales; default conversational con link como alternativa.

### 3.2 Onboarding opcional progresivo

- Al `/start` con DB vacía o incompleta → un único mensaje sugiere el link del SPA, sin bloquear el uso del bot.
- De ahí en adelante, **lazy detection**: si el extractor de transacciones encuentra una cuenta o banco no registrado, el bot pregunta antes de descartar.
- El usuario puede ignorar el link inicial y registrar transacciones directamente — el bot le va pidiendo lo que falta on-demand.

### 3.3 Magic-link auth flow

- Comando `/setup` en Telegram (también enviado al `/start` la primera vez) genera un JWT firmado HS256.
- **TTL: 30 minutos.**
- **Single-use:** el JWT se invalida al primer uso exitoso (tabla `magic_link_tokens` con flag `used` + `jti`).
- Claims: `user_id`, `iat`, `exp`, `jti`.
- El SPA valida el token en el endpoint `/api/v1/onboarding/validate` y mantiene el token en memoria durante la sesión de onboarding (no localStorage, no cookies persistentes).
- **Sin refresh tokens:** si expira, el usuario pide un nuevo link en Telegram. Refresh tokens son scope de 6e.

### 3.4 Lazy detection en write dispatcher

Cuando el extractor (Haiku) identifica una cuenta o banco que no existe en la DB del usuario:

- **Match fuzzy contra cuentas existentes** (ILIKE + variantes whitespace-stripped, igual que Phase 6a).
- **Si confidence ≥ 0.85:** asume match, registra transacción.
- **Si confidence < 0.85:** el bot responde "detecté que pagaste con [banco], pero no tengo esa cuenta registrada. ¿La creamos rápido acá o preferís usar el setup web? [link]".
- Si el usuario elige "acá": entra al mini-flow conversational de account creation (B9).

Telemetría en tabla nueva `lazy_detection_events` (hint, confidence, resolución elegida) para iterar el threshold.

### 3.5 Aguinaldo y salario escolar como first-class citizens

El schema de `recurring_incomes` modela explícitamente los ciclos CR-specific. Tipos enumerados:

- `monthly` (default)
- `biweekly`
- `aguinaldo` (se paga en diciembre, proporcional al tiempo trabajado)
- `salario_escolar` (enero–marzo según sector público/privado)
- `annual`
- `custom`

Esto es Costa Rica-specific moat — vale la complejidad.

### 3.6 Amortización francesa: parámetros sólo, schedule on-demand

**Decisión:** la entidad `debts` guarda únicamente los parámetros del préstamo (principal, tasa, plazo, fecha de inicio, día de pago, cuota fija). La tabla de amortización **NO** se persiste como rows.

**Razones:**

1. La amortización francesa es determinística desde los parámetros. Calcularla es <1ms para cualquier plazo razonable.
2. Coherente con "data quality before AI": derived state siempre correcto, imposible quedar stale.
3. Schema más simple en 6d — sin tabla `payment_schedule` pesada (una hipoteca a 30 años son 360 rows).
4. Cuando llegue tracking de pagos individuales y abonos extraordinarios (post-6d), se modela como tabla aparte `debt_payments` y la "tabla actual de amortización" sigue siendo derived: `parámetros + historial de pagos = schedule actual`. Cero migration de rows existentes.
5. Si performance se vuelve issue (improbable a la escala actual), cache en Redis del schedule computado por `debt_id` es trivial.

**Implicancia para B6:** el SPA muestra la tabla calculada on-demand vía endpoint `GET /api/v1/debts/{id}/schedule`. El form de creación tiene preview en vivo (calculado client-side en JavaScript con la misma fórmula).

### 3.7 Lazy creation: cuentas conversational, deudas siempre SPA

| Entidad | Canal default lazy | Razón |
|---|---|---|
| Cuentas | Conversational (4 turnos) | 4–5 campos cabe sin fricción |
| Deudas | SPA siempre | 8+ campos + validaciones cruzadas + preview de amortización |
| Ingresos recurrentes | Conversational | Selector de ciclo cabe en 4–5 turnos |
| Gastos fijos | Conversational | Similar a ingresos |

Cuando el bot delega al SPA, manda link generado on-demand vía `/setup`.

### 3.8 Migration path para usuario actual (Daniel)

Daniel pasa por el mismo flow que cualquier beta user. **No hay seed manual paralelo.** Es la mejor manera de dogfoodear el flow antes de exponerlo a beta users en P8. La DB en producción está limpia (sin accounts ni debts registrados), no hay riesgo de data corruption.

### 3.9 Categorías

Seed determinístico de categorías default por idioma (CR Spanish) en B1 migration. Edición de categorías custom queda fuera de 6d, va a 6e.

### 3.10 Stack del SPA

- **Build tool:** Vite
- **Framework:** React 18
- **Hosting:** Azure Static Web Apps (free tier)
- **Estilo:** Tailwind CSS
- **State:** local state (`useState` / `useReducer`); zero global state manager en 6d (sesión efímera de onboarding)
- **HTTP client:** `fetch` nativo con wrapper que inyecta JWT
- **Validación de forms:** Zod + react-hook-form
- **Deploy pipeline:** GitHub Actions → Azure Static Web Apps (preview deployments en PRs, production en `main`)

---

## 4. Bloques de implementación

13 bloques. Cada bloque tiene done-when criteria explícito y approval gate antes del siguiente.

### B1 — Schema design + migrations

**Scope:**
- Modelos SQLAlchemy 2.0 async para `accounts`, `debts`, `recurring_incomes`, `recurring_expenses`, `magic_link_tokens`.
- Pydantic v2 schemas (Create, Update, Read variants) — usar `Decimal` no `float` para amounts.
- Alembic migration única que crea las 5 tablas + categorías default seed.
- Index strategy: `user_id` index en las 4 tablas de entidades; `jti` unique index en `magic_link_tokens`.

**Done-when:**
- Migration corre limpia en local dev y staging.
- Tests de modelo (round-trip create/read) verdes.
- `Numeric(12,2)` en DB **y** `Decimal` en Pydantic schemas — esta es la oportunidad de no propagar el tech debt heredado de 5b a entidades nuevas.

**Open question a resolver en B1:** ¿`debts.principal_outstanding` es column derived (calculada al leer) o stored y actualizada en cada pago? Para 6d sin tracking de pagos, **stored** es suficiente y se actualiza manualmente desde el form. Decisión final en B1.

### B2 — Backend CRUD endpoints

**Scope:**
- REST endpoints en FastAPI:
  - `GET/POST /api/v1/accounts`, `GET/PATCH/DELETE /api/v1/accounts/{id}`
  - Equivalentes para `debts`, `recurring_incomes`, `recurring_expenses`
  - `GET /api/v1/debts/{id}/schedule` (computa amortización on-demand)
- Auth: dependency que acepta tanto session de Telegram (para mini-flow conversational) como JWT magic-link (para SPA).
- Validación Pydantic v2 estricta.
- Tests unitarios + integration con cobertura mínima 80% de los endpoints.

**Done-when:**
- 16+ endpoints corriendo verdes en tests.
- Curl smoke script en `scripts/curl-onboarding.sh` que verifica end-to-end de los 4 CRUD.
- Schedule endpoint retorna tabla amortización francesa correcta para casos test (verificados contra calculadora bancaria CR — Promerica o BAC).

### B3 — Magic-link auth flow

**Scope:**
- Endpoint `/api/v1/onboarding/magic-link` (interno, llamado por bot) que genera JWT y guarda `jti` en tabla.
- Endpoint `/api/v1/onboarding/validate` (público) que valida JWT + invalida en primer uso.
- Comando `/setup` en aiogram que llama el endpoint y manda link al usuario.
- Middleware FastAPI para validación JWT en endpoints SPA.

**Done-when:**
- `/setup` desde Telegram retorna URL válida.
- Click en URL valida token y permite acceso a endpoints CRUD.
- Reuso del mismo token retorna 401.
- Token expirado retorna 401.
- Tests E2E del flow completo verdes.

### B4 — SPA scaffold

**Scope:**
- Decisión: subdirectorio `web/` en monorepo (o repo aparte — resolver en B4).
- Vite + React + Tailwind + Zod + react-hook-form configurados.
- Layout base con header (logo + saludo) y navegación lateral.
- Routing: `/onboarding`, `/onboarding/cuentas`, `/onboarding/deudas`, `/onboarding/ingresos`, `/onboarding/gastos`.
- Fetch wrapper con JWT injection y manejo de 401.
- Pipeline GitHub Actions → Azure Static Web Apps (preview + production).

**Done-when:**
- SPA deployada en URL público (`*.azurestaticapps.net`).
- Click desde magic-link autentica y muestra layout.
- 401 redirige a página de "token expirado, pedí uno nuevo en Telegram".

### B5 — SPA pantallas de cuentas + ingresos recurrentes

**Scope:**
- Pantalla `/onboarding/cuentas`: list + form de creación + edición inline + delete con confirm.
- Pantalla `/onboarding/ingresos`: list + form con selector de ciclo (incluye aguinaldo y salario escolar) + edición + delete.
- UX: cards limpios, validación inline, mensajes de error en voseo.

**Done-when:**
- Crear/editar/borrar cuentas funciona end-to-end (UI → API → DB → UI).
- Ciclos CR-specific se pueden crear y aparecen labelados correctamente en la list view.
- Tests E2E (Playwright o similar — decisión en B11) cubren happy paths.

### B6 — SPA pantalla de deudas

**Scope:**
- Form completo con todos los parámetros de French amortization.
- Preview en vivo de la cuota fija calculada client-side mientras el usuario edita.
- Tabla de amortización con paginación (mostrar primeras 12 cuotas + total) al guardar.
- Validación de Ley 7472: prepayment fees no pueden exceder lo permitido por ley (warning UI, no bloqueante en 6d).

**Done-when:**
- Crear deuda con todos los tipos (`mortgage`, `personal_loan`, `credit_card_balance`) funciona.
- Cuota fija calculada client-side coincide con cálculo server-side (test).
- Tabla de amortización mostrada matchea valores de calculadora bancaria CR para casos test.

### B7 — SPA pantalla de gastos fijos

**Scope:**
- Pantalla `/onboarding/gastos`: list + form similar a ingresos pero más simple (sin ciclos exóticos, sólo `monthly` / `biweekly` / `annual` / `custom`).
- Selector de categoría desde categorías default seedeadas en B1.

**Done-when:**
- CRUD completo funcional.
- Categoría default presente en el dropdown.

### B8 — Lazy detection en write dispatcher

**Scope:**
- Modificar Haiku extractor para incluir `account_hint` en respuesta cuando detecta banco/cuenta.
- Lógica determinística post-extractor: match fuzzy de `account_hint` contra cuentas del usuario (ILIKE + whitespace-stripped, threshold 0.85).
- Si no hay match: bot responde con prompt de acción (registrar acá / link al SPA).
- Telemetría: registrar eventos en tabla nueva `lazy_detection_events` (tipo, hint, confidence, resolución).

**Done-when:**
- Mensaje "gasté 5000 con la BAC" sin cuenta BAC → bot pregunta si crear.
- Mensaje "gasté 5000" sin mención de cuenta → comportamiento actual (asume default si existe, sino pregunta).
- Tests cubren ambos paths.

### B9 — Mini-flow conversational de account creation

**Scope:**
- Extender state machine Redis de Phase 5b con estado `creating_account` y sub-estados (`asking_name`, `asking_type`, `asking_currency`, `asking_balance`, `confirming`).
- Persona voseo consistente con resto del bot.
- Cancelable con `/cancel`.
- Al confirmar, llama endpoint `POST /api/v1/accounts` de B2.

**Done-when:**
- Usuario completa flow de 4 turnos y aparece la cuenta creada.
- Cancelación en cualquier paso vuelve al estado idle sin escribir.
- Tests cubren happy path + cancelación + timeout.

### B10 — `/start` welcome message rediseñado

**Scope:**
- Reemplazar mensaje actual con uno que detecta estado del usuario:
  - **Usuario nuevo (cero entidades):** mensaje de bienvenida + sugerencia explícita del SPA con link generado.
  - **Usuario existente con DB poblada:** mensaje breve recordatorio de comandos.
- Comando `/setup` siempre disponible para volver a generar link.

**Done-when:**
- `/start` con DB vacía muestra mensaje correcto con link.
- `/start` con DB poblada muestra mensaje corto.
- `/setup` siempre genera nuevo link válido.

### B11 — Tests E2E del flow completo

**Scope:**
- Test E2E que simula:
  1. Usuario nuevo manda `/start`.
  2. Recibe link al SPA.
  3. SPA: crea cuenta, ingreso, deuda, gasto fijo.
  4. Vuelve a Telegram.
  5. Manda "gasté 5000 en el super con la BAC".
  6. Bot extrae correcto contra la cuenta BAC creada en SPA.
- Test E2E del path lazy puro (sin SPA): usuario crea cuenta vía mini-flow conversational.

**Done-when:**
- Ambos E2E tests verdes en CI.
- Performance: SPA carga en <2s, response time backend p95 <500ms.

### B12 — Self-onboarding para Daniel + ajustes

**Scope:**
- Daniel pasa por el flow completo en producción.
- Notas de fricción documentadas en `docs/phase-6d-retrospective.md`.
- Patches de UX según notas.
- **Cap: 1 día de ajustes**, no scope creep — fricciones grandes van a backlog post-6d.

**Done-when:**
- Daniel tiene cuentas, deudas, ingresos y gastos fijos registrados en producción.
- Retrospective documentada.

### B13 — `CLAUDE.md` + freezing de este doc

**Scope:**
- Actualizar `CLAUDE.md` con scope de 6d, entidades nuevas, endpoints nuevos, comandos nuevos.
- Marcar este doc como `STATUS: FROZEN`.
- Crear stub de `docs/phase-6e-decisions.md` con scope heredado.
- Actualizar roadmap: 6e (era 6d), siguientes fases shifted en consecuencia.

**Done-when:**
- `CLAUDE.md` refleja el estado post-6d.
- Roadmap correcto en `CLAUDE.md`.

---

## 5. Riesgos y open questions

### 5.1 Riesgos

- **Scope creep en SPA:** la tentación de meter dashboards en 6d porque "ya está el SPA" es alta. *Mitigación:* out-of-scope explícito (sección 2) + check de scope en cada PR.
- **Magic-link UX en mobile:** abrir link de Telegram en navegador móvil puede tener fricciones (browser default vs in-app browser de Telegram). *Mitigación:* testing real en iOS y Android antes de B12.
- **Validación de amortización francesa:** error de fórmula puede pasar tests internos pero generar valores incorrectos vs banco real. *Mitigación:* test cases con valores reales de Promerica/BAC verificados contra estados de cuenta.
- **Lazy detection false positives:** el threshold 0.85 puede generar falsos matches (ej. "BCR" matchea "BAC"). *Mitigación:* telemetría en `lazy_detection_events` para iterar el threshold post-launch.

### 5.2 Open questions (a resolver durante 6d, no bloqueantes para empezar)

- ¿`debts.principal_outstanding` stored o derived? → resuelve B1.
- ¿Repo del SPA en monorepo o repo aparte? → resuelve B4.
- ¿Playwright vs alternativa para E2E? → resuelve B11.
- ¿Categorías default: lista corta (8–10) o larga (20–30)? → resuelve B1.

---

## 6. Estimación de esfuerzo

Estimaciones aproximadas, asumen cadencia histórica de Phase 6a.

| Bloque | Días |
|---|---|
| B1 | 0.5 |
| B2 | 1.5 |
| B3 | 1 |
| B4 | 1 |
| B5 | 1.5 |
| B6 | 2 |
| B7 | 0.5 |
| B8 | 1 |
| B9 | 1 |
| B10 | 0.5 |
| B11 | 1 |
| B12 | 1 |
| B13 | 0.5 |
| **Total trabajo concentrado** | **~13 días** |

Calendar realista (con interrupciones, feedback, debug): **3–4 semanas.**

---

## 7. Done de Phase 6d (gate para iniciar 6b o 6c)

Phase 6d se considera cerrada cuando:

- Los 13 bloques tienen done-when verdes.
- Daniel completó self-onboarding en producción.
- E2E tests corriendo verdes en CI.
- `CLAUDE.md` actualizado.
- Roadmap shifted (6e era 6d, etc.).
- Magic-link flow probado en iOS y Android real.

---

## 8. Roadmap actualizado post-6d-decisions

1. **6b** — Gmail ingestion + automated transaction reconciliation
2. **6c** — User memory / behavioral profiling
3. **6d** — Onboarding & Self-Registration *(este doc)*
4. **6e** — Centro Financiero SPA (full)
5. **P7** — Affordability/pushback engine
6. **P8** — Beta users onboarding *(desbloqueado por 6d)*
7. **P9** — SaaS hardening
8. **Diferidos:** P5c (WhatsApp), P5d (nudges)

---

## 9. Resoluciones registradas 2026-05-08

Resueltas con Daniel antes de arrancar B1. Sobreescriben las decisiones inline donde haya conflicto. Se folded into §3 al frozen del doc en B13.

### 9.1 Auth flow — opaque magic link + cookie post-exchange

**Flipped 2026-05-08 (post-aprobación inicial).** El magic link lleva un token opaco, NO un JWT. El JWT solo aparece DESPUÉS del exchange, dentro de una cookie httpOnly.

- **Magic link token**: opaco, formato `<selector>.<verifier>` (ver §9.9). Sin info del user en el URL. ~65 caracteres.
- **Endpoint público único**: `POST /api/v1/auth/magic-link/exchange` con body `{token}`. La validación (selector lookup + bcrypt verifier) y consumo (atomic `UPDATE used_at`) viven dentro de una transacción.
- **Cookie post-exchange**: JWT HS256 firmado con `MAGIC_LINK_SESSION_SECRET`, `exp=4h`, `jti = magic_link_tokens.jti`. Atributos `HttpOnly; Secure; SameSite=Lax; Path=/`. Dev: cookie host-only + Vite proxy. Prod: `Domain=.dominio.cr`.
- **Sin refresh tokens** — si la sesión expira, el usuario pide otro link. Refresh tokens son scope de 6e.

Razón del flip: opaque token en URL evita leak en referer/history; JWT-en-link era fácil de inspeccionar y agregaba claims redundantes. Cookie httpOnly post-exchange es robusta contra XSS.

### 9.2 Recurring expenses → reuso de `recurring_bills` (Phase 4)

La tabla `recurring_bills` ya existe desde Phase 4 con `bill_occurrences`, `notification_rules` y jobs. **NO se crea `recurring_expenses` nueva**. Se reutiliza `recurring_bills` para los gastos fijos del onboarding. Esto reemplaza la mención a `recurring_expenses` en B1.

### 9.3 Magic-link mint — bot llama service directo, sin endpoint público

**Flipped 2026-05-08 (post-aprobación inicial).** El bot importa `api.services.auth.magic_link.generate_link` directamente. **No hay endpoint mint público.** El único endpoint público de magic-link es `POST /api/v1/auth/magic-link/exchange` (consumo, no creación).

Razón del flip: bot y API corren en el mismo proceso (FastAPI + aiogram en lifespan). Un endpoint mint solo agregaría una capa HTTP sin beneficio. Si en P9 separamos bot/API en procesos distintos, podemos exponer el mint como endpoint con auth de service-to-service en ese momento.

### 9.4 `debts.principal_outstanding` → stored

Lockeado: stored, actualizado manualmente desde el form en 6d. Sin tracking de pagos en 6d, no hay nada de qué derivar. Migración a derived es trivial cuando lleguen pagos individuales en 6e/post-6d.

### 9.5 Categorías default → lista corta de 10

Set determinístico CR Spanish, voseo: `alimentación, transporte, servicios, salud, ocio, vivienda, ingreso_salario, ingreso_extra, deudas, otros`. Categorías custom quedan fuera de 6d (van a 6e).

### 9.6 SPA en monorepo (`web/` dentro de FinancialProject)

Backend + SPA comparten contratos (Zod ↔ Pydantic). Un PR por feature cross-cutting, un solo CI a configurar. Repo separado solo gana con equipo aparte — no aplica.

### 9.7 E2E framework → Playwright

Mejor mobile emulation (crítico para B12 iOS/Android), multi-context limpio (bot + SPA en un test), recomendación del prompt operativo.

### 9.8 `recurring_incomes.base_salary_link_id` → `ON DELETE CASCADE`

Self-FK usa CASCADE, no SET NULL. Razón: aguinaldo y salario_escolar son derivados de un empleo concreto en CR. Si se elimina el salario base (ej: cambio de trabajo), los rows derivados son data inválida — el CHECK `ck_recurring_incomes_cr_cycles_need_base` los rechazaría con NULL anyway. CASCADE es semánticamente honesto y evita dependencia circular entre FK y CHECK. Cuando el usuario cambia de trabajo, registra salario nuevo + aguinaldo nuevo. Lockeada durante B1.

### 9.9 Magic-link bcrypt lookup → selector + verifier pattern

El token raw del link tiene formato `<selector>.<verifier>`:

- `selector = secrets.token_urlsafe(16)` (~22 chars, plain, indexed UNIQUE).
- `verifier = secrets.token_urlsafe(32)` (~43 chars, bcrypt'd como `token_hash`).

Lookup en exchange: `SELECT * FROM magic_link_tokens WHERE selector = :s AND expires_at > now() AND used_at IS NULL` (O(1) via UNIQUE index), luego `bcrypt.checkpw(verifier_bytes, row.token_hash)`. Match → atomic `UPDATE ... SET used_at = now() WHERE id = :id AND used_at IS NULL` con RETURNING.

**Razón vs alternativas:**

- **Naive scan** (SELECT todas las rows válidas + iterar bcrypt): O(N) por request, DoS-eable cuando crece el set.
- **uid en URL**: leakea `user_id` en referer/history; el endpoint exchange exige conocer el usuario antes de validar el token (anti-pattern).
- **Selector + verifier**: O(1) lookup, sin info de usuario en URL, bcrypt sigue protegiendo contra DB leak. Patrón estándar (Rails secure_compare, OWASP ASVS V2.1.5).

**Implicancia schema (B3 antes de B3 service code):** se agrega columna `selector String(32) NOT NULL UNIQUE` a `magic_link_tokens`. Migration 0016 se edita en place — la tabla nunca se escribió en producción (B1 sólo aplicó schema vacío).

### 9.10 B4 SPA scaffold → Vite proxy + credentialed API calls

B4 queda en `web/` dentro del monorepo, según §9.6. El scaffold usa Vite +
React 18 + TypeScript + Tailwind + Zod + react-hook-form + react-router-dom +
axios. Rutas iniciales: `/`, `/expired`, `/accounts/new`, `/incomes/new`,
`/debts/new`, `/bills/new`.

- **Dev auth:** cookie host-only `fa_session` + Vite proxy `/api/* →
  localhost:8000`. El browser ve un solo origen (`localhost:5173`), y axios
  usa `withCredentials: true`.
- **Prod auth:** cookie `Domain=.dominio.cr` cuando `SESSION_COOKIE_DOMAIN`
  está configurado. La API habilita CORS con credenciales contra
  `SPA_BASE_URL` y/o `SPA_CORS_ORIGINS`; no se usa `*` con cookies.
- **Current user:** `current_user` acepta auth en orden
  `X-Shortcut-Token` → cookie `fa_session` → shim dev `X-User-Id`.
- **Deploy:** workflow `.github/workflows/azure-static-web-apps.yml` usa
  Azure Static Web Apps con `app_location=web` y `output_location=dist`.
- **Status B4:** `npm run build` y `npm run lint` verdes localmente; B1–B3
  enfocado verde con `42 passed`. Smoke con browser real y URL pública queda
  pendiente hasta tener Static Web App provisionada y token
  `AZURE_STATIC_WEB_APPS_API_TOKEN`.

### 9.11 B5 accounts + incomes SPA → CRUD sobre endpoints B2

B5 implementa las pantallas existentes del scaffold (`/accounts/new` y
`/incomes/new`) y agrega aliases compatibles con el decisions doc
(`/onboarding/cuentas` y `/onboarding/ingresos`).

- **Cuentas:** list + create + edit inline + delete con confirmación. El form
  usa `react-hook-form` + Zod y pega a `GET/POST/PATCH/DELETE
  /api/v1/accounts`. Tipos permitidos siguen el contrato backend actual:
  `checking`, `savings`, `credit`, `investment`.
- **Ingresos recurrentes:** list + create + edit inline + delete con
  confirmación. El form pega a `GET/POST/PATCH/DELETE
  /api/v1/recurring-incomes`.
- **Ciclos CR:** `aguinaldo` y `salario_escolar` ocultan monto, piden salario
  base, bloquean submit si no hay salary registrado y delegan la derivación al
  backend B2. La UI muestra una explicación corta del ciclo al seleccionar cada
  tipo.
- **Moneda/montos:** helpers en `web/src/lib/money.ts` parsean y formatean CRC
  con miles `.` y USD con miles `,`. La validación impide saldo inicial
  negativo excepto en cuentas `credit`.
- **Redirect UX:** al crear cuenta o ingreso, vuelve al landing con
  `?created=...&name=...`; `Landing` muestra un aviso descartable.
- **Status B5:** `npm run lint` y `npm run build` verdes; rutas
  `/accounts/new` y `/incomes/new` responden 200 en el dev server Vite;
  backend B2+B3 sigue verde con `27 passed`. No hay Playwright todavía; E2E
  real queda para B11 según §9.7.

### 9.12 B6 debts SPA → create + preview + schedule

B6 implementa la pantalla de deudas en el SPA existente (`/debts/new`) y el
alias de onboarding (`/onboarding/deudas`). La pantalla queda centrada en
captura inicial, no en edición avanzada.

- **Formulario:** cubre `mortgage`, `personal_loan`,
  `credit_card_balance`, `auto_loan`, `student_loan` y `other`; el backend
  acepta el alias `credit_card_balance` y lo persiste como el tipo canónico
  `credit_card` por compatibilidad con el enum existente.
- **Amortización:** `web/src/lib/amortization.ts` comparte la fórmula PMT
  francesa con el backend para preview local de cuota. El schedule definitivo
  se pide al servidor con `GET /api/v1/debts/{id}/schedule`.
- **Tabla:** el SPA muestra resumen total y una tabla paginada de 12 cuotas
  por página, calculada desde la respuesta del servidor.
- **Ley 7472:** el form muestra warning cuando el usuario registra comisión de
  prepago con dos o más cuotas pagadas. Es warning, no bloqueo, porque B6 no
  modela pagos individuales.
- **Casos bancarios:** `tests/test_phase_6d_b6_debts.py` fija fixtures de
  cuota nivelada estilo BAC/Promerica para proteger la fórmula contra drift.
  Comparación con estados de cuenta reales queda para B12 dogfooding.
- **Status B6:** `npm run lint`, `npm run build`, route smoke
  `/debts/new -> 200`, focused B6 `7 passed`, y regresión B2+B3+B6
  `34 passed`. No hay Playwright todavía; E2E real queda para B11 según §9.7.

### 9.13 B7 recurring bills SPA → CRUD sobre Phase 4 `recurring_bills`

B7 implementa la pantalla de gastos fijos en el SPA existente (`/bills/new`) y
el alias de onboarding (`/onboarding/gastos`). Se reutiliza `recurring_bills`;
no se crea `recurring_expenses`.

- **Formulario/list:** create + list + edit inline + soft delete contra
  `POST/GET/PATCH/DELETE /api/v1/recurring-bills`.
- **Frecuencias B7:** el SPA ofrece `monthly`, `biweekly`, `annual` y
  `custom`. El backend conserva las frecuencias Phase 4 existentes para
  compatibilidad con datos previos.
- **Categorías:** el dropdown usa `GET /api/v1/categories` (`CATEGORIES_CR`
  locked en §9.5). El endpoint de `recurring_bills` ahora acepta esas
  categorías CR y mantiene compatibles los valores legacy de `BillCategory`.
  Esto resuelve el drift entre la resolución B1 y el contrato Phase 4 antiguo.
- **Custom:** `frequency=custom` exige `recurrence_rule` RRULE; el form lo pide
  sólo cuando hace falta.
- **Status B7:** `npm run lint`, `npm run build`, route smoke
  `/onboarding/gastos -> 200`, focused B7 `4 passed`, y regresión
  B2+B3+B6+B7 `38 passed`. Playwright E2E queda para B11 según §9.7.

### 9.14 B8 lazy detection → account/bank hints in write dispatcher

B8 implementa lazy detection para menciones explícitas de cuenta o banco en el
write dispatcher. La extracción sigue siendo LLM-only para estructura; la
resolución y decisión son determinísticas.

- **Extractor:** el prompt Haiku ahora instruye explícitamente llenar
  `account_hint` cuando el usuario diga "con la BAC", "de la BCR", "tarjeta
  Promerica", etc. El schema ya tenía el campo desde Phase 5b.
- **Matcher:** `api/services/dispatch/lazy_detection.py` usa matching
  determinístico con threshold `0.85`, variante whitespace-stripped y guard
  contra hints genéricos como `banco`, `cuenta` o `tarjeta`.
- **Write dispatcher:** si el hint matchea una cuenta existente, el flujo sigue
  normal y linkea esa cuenta. Si no matchea, responde:
  `No tengo registrada una cuenta llamada X. ¿La creamos? Decime crear... o link...`
  sin crear transacción todavía.
- **No-hint:** mensajes como `gasté 5000` conservan el comportamiento previo:
  default si hay una cuenta clara, aclaración si hay varias.
- **Telemetría:** cada hint explícito registra `lazy_detection_events` con
  `hint_type`, `hint_text`, `fuzzy_match_score`, `resolution` y
  `matched_account_id` cuando aplica.
- **Status B8:** focused B8 DB `2 passed`, dispatcher unit `21 passed`,
  extractor/router/dispatcher smoke `33 passed`, y regresión
  B2+B3+B6+B7+B8 `40 passed`.
