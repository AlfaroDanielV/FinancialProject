# Phase 6b Decision Log

Este archivo es temporal. Se consolida en `CLAUDE.md` al cierre de Phase
6b (B12). Decisiones se registran en orden cronológico.

## 2026-05-05 — OAuth flow: Authorization Code, sin PKCE

Decision: Google OAuth Authorization Code flow server-side. No PKCE, no
device flow, no service account, no IMAP/SMTP, no contraseñas de aplicación.

Motivo: Es el flow estándar para una webapp con backend confiable. PKCE
agrega complejidad sin valor cuando el cliente es nuestro servidor (no un
SPA o mobile). Las alternativas (IMAP, app passwords) requieren que el
usuario toque su Gmail — violación directa del principio de "cero
instrucciones técnicas al usuario".

## 2026-05-05 — Scope único: gmail.readonly

Decision: Solicitamos `https://www.googleapis.com/auth/gmail.readonly`.
No `gmail.modify`, no `gmail.metadata`, no `gmail.compose`.

Motivo: Solo necesitamos leer notificaciones bancarias. Pedir más scopes
infla la pantalla de consent y aumenta la fricción de aprobación cuando
el GCP project pase de Testing a Production. Si en el futuro queremos
marcar correos como leídos o etiquetarlos, ampliamos a `modify` con un
re-consent — los refresh tokens existentes siguen siendo válidos para el
scope ya autorizado.

## 2026-05-05 — GCP project en Testing mode con test users manuales

Decision: El project en Google Cloud Console queda en estado **Testing**.
Daniel agrega cada email beta tester manualmente en
`OAuth consent screen → Test users` (límite duro de 100). No publicamos
para verification hasta que haya un caso de negocio (Phase 8 SaaS).

Motivo: Verification de Google para apps con scopes sensibles tarda
semanas y exige un dominio público, política de privacidad publicada,
demo video y assessment de seguridad. Para uso personal + beta cerrada
no aplica. La pantalla "Google hasn't verified this app" es esperada en
Testing mode — la anticipamos en el bot para que el usuario sepa que es
normal y cómo pasarla (Avanzado → Continuar).

Implicación operativa: cada nuevo beta tester requiere intervención
manual de Daniel en GCP Console **antes** de que pueda completar el
OAuth. El bot debe rechazar limpiamente el flujo si el usuario no está
en la lista de test users (Google devuelve `error=access_denied` con
`error_description=The given user is not in the test list`).

## 2026-05-05 — State parameter como JWT HS256

Decision: El `state` del OAuth flow es un JWT firmado con HS256, secreto
en `GMAIL_OAUTH_STATE_SECRET`. Payload `{user_id, nonce, exp}` con TTL
10 min. Validación estricta en el callback: firma + nonce one-time
(quemado en Redis) + exp.

Motivo: CSRF protection es no-negociable. Un nonce one-time previene
replay attacks. El TTL corto evita que un state robado quede usable. JWT
nos deja embeber el `user_id` sin tener que guardar estado server-side
adicional (el nonce sí, pero sólo para invalidación).

Alternativa rechazada: `state` opaco con backing en Redis. Funciona pero
duplica I/O y no aporta sobre JWT firmado para este caso.

## 2026-05-05 — Token storage: Azure Key Vault en prod, EnvSecretStore en dev

Decision: Refresh tokens viven en Azure Key Vault con naming
`gmail-refresh-{user_id}`. Access tokens nunca se persisten — se
obtienen on-demand y viven en memoria del request. Local dev usa
`EnvSecretStore` con prefijo `DEV_SECRET_`.

Motivo: Refresh tokens son el material sensible (acceso continuo a
inbox). KV cumple con cualquier auditoría de seguridad razonable y
soporta rotación. Access tokens duran 1h y son cheap de regenerar — no
amerita almacenar.

Selección por env var `SECRET_STORE_BACKEND` (`azure_kv | env`). El
`Protocol` `SecretStore` mantiene el resto del código agnóstico.

## 2026-05-05 — Onboarding state machine en Redis

Decision: El estado de onboarding por usuario vive en Redis bajo
`gmail_onboarding:{user_id}` con TTL 30 min. Estados:
`awaiting_oauth → oauth_done → awaiting_sample → analyzing_sample → confirming → active`.

Motivo: Mismo patrón que Phase 5b transactions y nudges — Redis es la
fuente de verdad para state durable de bot ("State storage policy"
memory). FSM de aiogram es transient, no sobrevive restart de webhook
mode. TTL 30 min es generoso pero finito: si el usuario abandona, no
queda colgado para siempre.

## 2026-05-05 — Sample collection es requisito obligatorio

Decision: Antes de activar la ingesta no se acepta saltarse el paso de
sample. Sin sample no hay scan, no hay backfill, no hay daily worker.

Motivo: El sample sirve para tres cosas críticas:
1. Capturar el `sender_email` real (varía por banco/país).
2. Validar que Haiku reconoce el formato antes de hacer backfill ciego
   contra 30 días de correos.
3. Construir la whitelist personalizada (filtro principal del scanner).

Sin esos tres datos el daily worker correría a ciegas y produciría ruido
o falsos negativos. Mejor frustrar al usuario en onboarding que ingerir
basura durante una semana.

Implicación: si el usuario no tiene un correo bancario a mano cuando
hace `/conectar_gmail`, el bot lo invita a volver más tarde — el OAuth
queda concedido, el state se guarda en Redis, pero la ingesta no arranca
hasta que mande un sample.

## 2026-05-05 — `transactions.source` extiende, no reemplaza

Decision: El nuevo CHECK constraint en `transactions.source` admite
`('manual', 'shortcut', 'telegram', 'gmail', 'reconciled')`. La spec
original listaba sólo `(shortcut, gmail, manual, reconciled)` pero
existen filas con `source='telegram'` desde Phase 5b que un CHECK
estricto rechazaría.

Motivo: Reescribir filas existentes (e.g. mapear `telegram` → `manual`)
borraría señal de origen útil para debugging. Extender el conjunto
admitido es la opción no-destructiva. El default sigue siendo `manual`,
y el código nuevo sólo escribe `gmail` o `reconciled` — los valores
adicionales son sólo para rows preexistentes.

## 2026-05-05 — `gmail_message_id` separado de `source_ref`

Decision: Se agrega columna nueva `transactions.gmail_message_id`
(string nullable, UNIQUE per-user via partial index). No se reusa
`source_ref` aunque ese campo en docs/CLAUDE.md está descrito como "email
Message-ID for dedup".

Motivo: `source_ref` nunca se usó en producción (Phase 2 nunca shipeó).
Tener una columna dedicada con su propio índice y constraint es más
honesto y evita acoplar el dedup de Gmail a un campo genérico que en el
futuro podría usarse para otros origins (Outlook, banco vía API, etc.).
`source_ref` queda libre para Phase 2 cuando llegue.

## 2026-05-05 — `transactions.status` agrega un eje ortogonal a `parse_status`

Decision: Se agrega columna nueva `transactions.status` con CHECK
`('confirmed', 'shadow', 'pending_review')`, default `'confirmed'`.
Convive con la columna existente `parse_status` (que también default a
`'confirmed'`).

Motivo: `parse_status` es legacy de Phase 1 — su único uso actual es
flagear desde el endpoint manual review (`parse_status='flagged'`). El
nuevo `status` es semánticamente distinto: indica si la transacción
suma al balance del usuario (`confirmed`) o queda invisible hasta que
el usuario apruebe (`shadow`). No quiero acoplar dos conceptos en una
columna y migrar de string a string-con-más-valores en una sola tabla
con datos en producción.

Deuda técnica registrada: `parse_status` debería renombrarse o
eliminarse en Phase 7 cuando consolidemos modelos. No es scope de 6b.

## 2026-05-05 — Reconciliation: matching window 7 días, tolerancia ±1 CRC y ±1 día

Decision: El reconciler busca transactions existentes (creadas por
Shortcut o Telegram) en una ventana de 7 días anterior al timestamp
del correo, exigiendo `|amount - candidate.amount| <= 1` (CRC),
`transaction_date` dentro de ±1 día calendario, y `currency` igual.

Motivo: Los bancos suelen notificar el correo el mismo día o al día
siguiente del cargo; 7 días cubre delays raros (e.g. modificaciones
post-fact). La tolerancia de ±1 CRC absorbe diferencias de redondeo
entre lo que el usuario reportó por Shortcut (montos enteros) y lo que
el banco notifica (con céntimos). Currency match es estricto para no
mezclar CRC con USD.

Si el reconciler encuentra ≥2 candidates, gana el de mayor score
(amount exacto > date exacto > last4 match si existe). Empate raro,
pero documentado.

## 2026-05-05 — Shadow mode obligatorio primera semana post-activación

Decision: La columna `gmail_credentials.activated_at` define el inicio
de la shadow window. Toda transacción detectada por Gmail que NO
matchea con una existente, durante 7 días contados desde
`activated_at`, entra a `transactions` con `status='shadow'`. No suman
al balance, no se notifican individualmente. El bot manda un resumen
agregado diario a las 8am CR.

Motivo: Es la red de seguridad antes de confiar el balance al ingest.
Si el extractor falla y mete ruido, el usuario lo ve como un resumen
sospechoso y descarta con `/rechazar_shadow` antes de que afecte sus
saldos. Una semana es el mínimo razonable para cubrir bills mensuales,
quincenales y bisemanales.

Comandos:
- `/aprobar_shadow` → promueve todas las shadow del usuario a
  `confirmed`. Aplicable también después de la ventana si quedaron rows
  sin promover.
- `/rechazar_shadow` → borra y loguea outcome `rejected_by_user` en
  `gmail_messages_seen` para análisis.

## 2026-05-05 — Polling diario, sin Gmail watch + Pub/Sub

Decision: El daily worker corre `0 9 * * * UTC` (= 3am CR) como Azure
Container Apps Job. No usamos Gmail push notifications (watch +
Pub/Sub).

Motivo: Push agrega Pub/Sub topic, IAM, y un endpoint público adicional.
Para uso personal + beta cerrada con notificaciones que llegan dentro
de horas (no segundos), el cron diario es suficiente. El usuario que
quiera frescura inmediata puede correr `/revisar_correos` (rate-limited
a 1 cada 30 min). Cuando haya >50 usuarios o un caso de uso real-time,
revaluamos.

## 2026-05-05 — Anti-saturation Telegram: batching > 5 transacciones

Decision: Si un ciclo de scan detecta más de `GMAIL_BATCH_THRESHOLD=5`
transacciones nuevas para un usuario, se manda **un solo** mensaje
agregado con resumen + lista compacta. Por debajo del threshold, una
notificación por transacción (formato Phase 5b).

Motivo: Un backfill de 30 días puede traer ~50 transacciones. Mandar
50 mensajes seguidos por Telegram triggerearia rate limits del bot
(30 msg/sec global, pero también 1 msg/sec por chat) y enterraría al
usuario en notificaciones. El threshold de 5 es heurística — alta
para no batchear casos normales (3-4 cargos en un día), baja para
backfills.

## 2026-05-05 — Reusar el extractor Haiku de Phase 5b

Decision: El parsing del body del correo bancario reusa
`api/services/llm_extractor`. Si está acoplado a la ruta de Telegram,
se refactoriza a `api/services/extraction/transaction_extractor.py`
con una API que acepte `{text, source_kind=email|chat, hints}` antes
de Phase 6b. No se escribe un extractor paralelo.

Motivo: Tener dos extractores genera drift inmediato — el del bot
mejora, el de Gmail no, o viceversa. Un solo extractor con prompt
condicional según `source_kind` es la simplificación correcta.

Pendiente B7: validar el grado de acoplamiento del extractor actual
con la ruta de Telegram antes de empezar a usarlo desde el scanner.
Si el refactor se complica, escalar a Daniel antes de seguir.

## 2026-05-06 — Onboarding email-based, no sample-based (addenda)

Decision: el onboarding pide directamente los correos del banco. Sample
collection vía foto/texto se rebaja a comando opcional post-activación
(`/agregar_muestra`), no es gate de activación.

Motivo: el dato que necesitamos (sender email) es trivial de pedir
explícitamente. Las fotos pueden estar recortadas, mal iluminadas, o
el texto pegado puede llegar truncado — todos puntos de falla
innecesarios para inferir un campo que el usuario sabe escribir. Los
samples siguen siendo útiles para calibrar el extractor frente a
formatos raros, pero como mejora opcional, no como prerequisito.

Implicación: el state `awaiting_sample` se reemplaza por
`selecting_banks`. Los handlers de foto/texto en estado de onboarding
del block 6 original quedan inactivos hasta que `/agregar_muestra` los
re-active vía un nuevo state `awaiting_optional_sample` (Block D).

## 2026-05-06 — Multi-banco desde el primer onboarding

Decision: el bot ofrece una lista curada de bancos CR comunes (presets)
y permite agregar emails custom. El usuario puede acumular múltiples
bancos en una sola sesión de onboarding. Soft cap de 8 senders activos
por user.

Motivo: la mayoría de usuarios CR usan 2–3 bancos en paralelo (cuenta,
tarjeta, ahorro). Forzarlos a re-onboardear por banco es fricción
gratuita. El cap de 8 corta el abuso y limita el costo del scanner.

Implementación: tabla `gmail_sender_whitelist` con soft-delete
(`removed_at`), unique `(user_id, sender_email)` para idempotencia.
Comandos `/agregar_banco` y `/quitar_banco` post-activación reutilizan
los mismos handlers.

## 2026-05-06 — Lista de bancos CR como data-driven config

Decision: `api/data/bank_senders_cr.py` exporta `KNOWN_BANK_SENDERS_CR`
como dict literal. No se hardcodea en handlers ni se carga de DB.

Motivo: queremos editarlos sin redeploy ni migración. Un dict literal
en código vive mejor en revisión que una tabla seed. Cuando crezca a
docenas de entries, evaluamos moverlo a YAML/JSON, pero hoy no aplica.

Importante: la lista inicial es **propuesta**. Cada dominio se valida
contra correos reales antes de promover a beta tester. El comment del
archivo lo deja explícito.

## 2026-05-06 — Detección de banco por dominio del email

Decision: cuando el usuario escribe un email custom, el bot intenta
inferir el banco mirando el dominio (`@bac.cr` → BAC). Si no matchea,
queda con `bank_name=NULL` — no es error, solo "no etiquetado".

Motivo: poder etiquetar samples y métricas por banco aún cuando el
usuario no pasa por un preset. Mantener el `NULL` como estado válido
evita inventar banco cuando no hay señal.

## 2026-05-06 — Backfill se dispara desde `on_activate_callback` vía `asyncio.create_task`

Decision: el backfill arranca como tarea async fire-and-forget desde
el handler de "Activar 🚀", envuelto en un wrapper `_run_backfill_safe`
con `try/except log.exception(...)`. No hay endpoint HTTP intermedio
en el flujo normal. El endpoint admin
`POST /api/v1/admin/gmail/run-backfill` queda solo para trigger manual.

Motivo: el camino más corto entre "user tapeó Activar" y "scanner está
corriendo" no debería pasar por la red. `asyncio.create_task` mantiene
el runner en el event loop del API y permite responder al user al
toque sin bloquear. El wrapper con `log.exception` es la lección del
diagnóstico de hoy: tareas async sin try/except interno se tragan
errores en silencio.

## 2026-05-06 — Whitelist append-only con soft delete

Decision: `gmail_sender_whitelist.removed_at` es nullable. `/quitar_banco`
seta `removed_at=now()`, no DELETE. La query de scan filtra
`WHERE removed_at IS NULL`. Las transactions ya ingestadas de un sender
removido se quedan; solo se deja de scanear ese sender adelante.

Motivo: dos razones. (1) Auditabilidad: si una transacción "rara"
aparece, queremos rastrear de qué sender vino, aún si el usuario
después removió ese sender. (2) Re-add fácil: si el usuario remueve
y vuelve a agregar el mismo email, el upsert del `add_sender` nullea
`removed_at` en lugar de insertar un duplicado.

## 2026-05-06 — Reset del 657093c4 abandoned-onboarding queda manual

Decision: el user `657093c4-…f52c` (OAuth done sin activación) se deja.
No se purga automáticamente.

Motivo: es un caso real de drop-off que conviene observar para futuras
métricas de funnel. Si llega a molestar (e.g. `/status` ruidoso), se
borra con SQL ad-hoc.

## 2026-05-06 — Cleanup en `on_activate_callback`: persist whitelist + kick backfill

Decision: el handler hace tres cosas en orden:
1. `cred.activated_at = now()` + `db.commit()`.
2. Para cada email en `pending_senders` del state Redis,
   `whitelist.add_sender(...)`.
3. `asyncio.create_task(_run_backfill_safe(user.id))`.
4. `gmail_onboarding.clear(user_id)` + responder al user.

Motivo: el `commit()` antes del `create_task` garantiza que el backfill
arranque con un DB state consistente — `activated_at` definido y
whitelist populada. Si el `create_task` se programara antes, hay una
race donde el scanner podría leer la whitelist vacía.

## 2026-05-06 — Extractor de emails separado del de chat (Block B)

Decision: el extractor para bodies de correo bancario vive en
`api/services/extraction/email_extractor.py`. NO es el mismo módulo
que `api/services/llm_extractor` (Phase 5b chat). Comparten el
`AnthropicLLMClient` shape (cache_control, tool_use forzado) pero
ninguna otra cosa.

Motivo: el spec original sugería un único `transaction_extractor.py`
compartido con `source_kind=email|chat`. Pero las dos rutas tienen:
- Prompts con voces y vocabularios distintos (CR slang en chat vs.
  templates de banco en email).
- Schemas diferentes — el chat captura intent + dispatcher para
  routing del bot; el email no necesita intent (sabemos que es una
  transacción) pero sí transaction_type, last4, raw_email_subject.
- Métricas distintas: chat se loguea en `llm_extractions`, email se
  cuenta en `gmail_ingestion_runs`.

Compartir un módulo significaría un prompt monstruo con condicionales
que cada cambio en una ruta puede romper la otra. Dos extractores
especializados son más fácil de versionar.

## 2026-05-06 — Scanner: HTTP directo a Gmail, sin SDK

Decision: `api/services/gmail/scanner.py` usa `httpx.AsyncClient` contra
`https://gmail.googleapis.com/gmail/v1/users/me/messages` y
`messages/{id}`. No usamos `google-api-python-client` ni
`google-auth-httplib2`.

Motivo: la API que necesitamos son tres endpoints (list, get, modify
opcional). Las libs de Google traen autenticación oblicua, retries
opacos y dependencias de transporte sync. Con `oauth.refresh_access_token`
ya tenemos el access_token; un `Authorization: Bearer ...` header en
httpx es la forma más directa. Mantiene el surface 100% async y
testeable con `httpx.MockTransport`.

## 2026-05-06 — Preset tap pide correo, no precarga senders

Decision: tapear un botón de banco preset (BAC, Promerica, etc.) ya
NO agrega los senders canónicos del dict `KNOWN_BANK_SENDERS_CR`.
En su lugar, el bot setea un sub-estado `awaiting_bank=<bank>` y le
pide al usuario que tipee el correo desde el cual recibe notificaciones
de ese banco. Cuando el usuario lo manda, se asocia con
`bank_name=<bank>`, `source='preset_tap'`.

Motivo: los dominios canónicos cambian sin aviso ("notificaciones@bac.cr"
deja de recibir, los avisos vienen ahora de "alertas@credomatic.cr",
etc.), y los usuarios reportan correos reales que difieren del default.
La whitelist debe reflejar lo que el usuario ESTÁ recibiendo, no lo
que el código piensa que recibe. Asumir es el camino al falso negativo
silencioso ("¿por qué no me detectó la última compra?").

Implicación de código:
- `KNOWN_BANK_SENDERS_CR.values()` quedan como referencia documental
  (qué dominios eran "canon" en algún momento), pero no se consumen
  desde handlers.
- `preset_senders_for()` queda en el módulo pero deja de llamarse
  desde producción.
- Agregamos `OnboardingState.awaiting_bank` (str | None) para recordar
  qué bank label nombró el usuario antes de mandar el correo.
- Si el usuario tapea otro preset mientras hay un `awaiting_bank`
  pendiente, sobreescribe — UX permisiva, mejor que bloquear.

## 2026-05-06 — Batching de notificaciones Telegram (Block C.1)

Decision: el notifier elige el formato del aviso post-scan según
contexto:
- En shadow window y `mode in (backfill, daily)`: NO se notifica
  per-transaction. Las nuevas IDs se acumulan en
  `gmail_shadow_summary:{user_id}:{date}` (TTL 48h) y el daily worker
  manda un resumen agregado a las 8am CR del día siguiente.
- Fuera de shadow window:
  - Si `transactions_created > GMAIL_BATCH_THRESHOLD` (default 5):
    1 mensaje resumen "Encontré N transacciones nuevas: ..." con top
    3 + "y N más".
  - Caso normal (≤ threshold): 1 mensaje por transacción.
- En `mode=manual` (`/revisar_correos`): SIEMPRE manda el "Listo,
  revisé X correos" final, sin importar shadow window — el usuario
  pidió la corrida y espera respuesta inmediata.

Motivo: balance entre "no spamear" y "no hacer al usuario poll DB para
ver qué pasó". El shadow window justifica silencio porque el resumen
diario llega; fuera de shadow, una compra real merece aviso individual
hasta que se vuelve enterramiento (>5 en una corrida).

## 2026-05-06 — Shadow summary cadence

Decision: el resumen "modo sombra" se manda **una vez al día** desde el
daily worker, leyendo el set acumulado de transaction_ids de Redis del
DÍA ANTERIOR. No se manda en tiempo real al final de cada scan.

Motivo: si el backfill de activación produce 30 shadow rows, el usuario
recibe UN resumen mañana en lugar de 30 notificaciones esta tarde. El
shadow window de 7 días es para auditoría humana, no para feedback
inmediato. El usuario tiene `/aprobar_shadow` y `/rechazar_shadow` para
actuar sobre el lote completo, no sobre cada fila.

## 2026-05-06 — `/rechazar_shadow` borra y registra `rejected_by_user`

Decision: `/rechazar_shadow` confirma con un botón inline ("Sí,
descartar") y entonces:
1. DELETE de todas las transactions con `user_id=? AND status='shadow'
   AND source='gmail'`.
2. UPDATE de los `gmail_messages_seen` correspondientes a
   `outcome='rejected_by_user'` (no se borran — mantenemos la
   evidencia de qué se rechazó).

Motivo: shadow es nuestro safety net; rechazarlo significa "el
extractor / reconciler te equivocó masivamente". Borrar las
transactions limpia el balance, pero queremos saber QUÉ rechazaste
para mejorar el extractor — de ahí el `rejected_by_user` en seen.

`/aprobar_shadow` por su lado solo flipea status='confirmed', sin
tocar gmail_messages_seen (que ya quedó como `created_shadow`, lo cual
sigue siendo cierto sobre cómo entró la fila a la DB).

## 2026-05-06 — `FileSecretStore` para dev persistente (Block D.1)

Decision: agregamos un tercer backend `file` a `SecretStore`, que
escribe a `.dev_secrets.json` en cwd (gitignored). Backends ahora:
- `env` — process-local, ideal para CI / tests / smoke breve. Default.
- `file` — persistente entre restarts de uvicorn. Recomendado para
  dev local cuando se itera contra Gmail real.
- `azure_kv` — prod.

Motivo: la trampa que descubrimos en el diagnóstico de Block B —
`EnvSecretStore` pierde refresh tokens al reiniciar uvicorn — frenó el
smoke real durante una iteración. Un backend `file` resuelve eso sin
agregar dependencias (azure-* libs siguen siendo opcionales). El
trade-off: los tokens en `.dev_secrets.json` están en disco en
plaintext. Aceptable para una caja de desarrollo individual; el
gitignore evita que terminen en git por accidente.

`bot/app.py::start_bot()` loguea WARNING al boot cuando `backend='env'`
explicando la limitación, para que el dev sepa qué esperar.

## 2026-05-06 — `/agregar_muestra` reusa el sample analyzer de B6

Decision: el comando `/agregar_muestra` post-activación usa una flag
Redis dedicada (`gmail_optional_sample:{user_id}`, TTL 10 min) e
invoca `services/gmail/sample_analyzer.py` (B6) sobre la foto o el
texto que mande el usuario. Persiste en `bank_notification_samples`.
NO agrega automáticamente al whitelist — la decisión queda con
`/agregar_banco`.

Motivo: el flujo de samples opcionales es ortogonal al onboarding
multi-banco — un usuario activo manda un sample para mejorar el
extractor, no para cambiar la lista de senders. Mantener Redis keys
independientes evita que el sample handler interfiera con el flujo de
`selecting_banks`.

El analyzer + tabla ya existen de B6 — Block D solo wirea el
end-to-end del comando, sin reescribir.

## 2026-05-06 — Daily worker como Container Apps Job, no in-process scheduler

Decision: el cron diario corre como un **Container Apps Job** separado
del API, con imagen propia (`Dockerfile.worker`). NO es un BackgroundTask
ni un APScheduler ni un asyncio.create_task del API.

Motivo: tres razones.
1. Aislamiento de recursos: un scan que tarde 10 minutos no debe
   pegarle al pool de conexiones del API ni a la latencia de las
   queries del bot.
2. Idempotencia desde el orquestador: si el job falla, Container Apps
   lo retrasa y reintenta sin que el API se entere.
3. Costo: el API corre 24/7 con autoscale; el worker corre 1 minuto/día
   y se apaga. Mantenerlos separados es más barato.

El endpoint admin `POST /api/v1/admin/gmail/run-daily` ejecuta el mismo
código en-proceso para testing manual sin esperar al cron.

## 2026-05-06 — Sign convention en email transactions

Decision: el extractor devuelve `amount` siempre POSITIVO + un campo
`transaction_type` (charge/withdrawal/fee/payment/deposit/refund/transfer).
El reconciler aplica el signo:
- charge | withdrawal | fee | payment | transfer → negativo (gasto).
- deposit | refund → positivo (ingreso).

Motivo: separar "qué pasó" de "cómo afecta el balance" facilita
reportar auditorías ("se cargaron X colones") y mantiene el modelo
de signo del DB consistente (negativo=expense, positivo=income, igual
que Phase 5b chat).

`payment` y `transfer` se cuentan como gasto del lado de la cuenta
notificada porque casi todas las notificaciones bancarias son del lado
"sale plata de aquí". El usuario puede corregir manualmente con un
ingreso espejo si es transfer entre cuentas propias.
