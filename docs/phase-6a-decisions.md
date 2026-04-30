# Phase 6a Decision Log

Este archivo es temporal. Se consolidara en `CLAUDE.md` al cierre de Phase 6a.

## 2026-04-27 - Telegram usa HTML, no Markdown

Decision: El formatter de 6a debe emitir HTML de Telegram (`<b>`, `<code>`, `<i>`, `<a>`), no Markdown.

Motivo: El bot de 5b ya corre con `ParseMode.HTML`. Cambiar el parse mode global puede romper respuestas existentes, asi que 6a debe adaptarse al formato activo.

## 2026-04-27 - Currency vive en users, no accounts

Decision: Las tools que devuelven montos usan `users.currency` como moneda por defecto, hoy CRC. No se agrega `accounts.currency`.

Motivo: El modelo `Account` no tiene columna `currency`. Multi-currency por cuenta queda fuera de scope para 6a y posiblemente para el roadmap actual.

## 2026-04-27 - Se eliminan query_recent y query_balance

Decision: No se mantiene compatibilidad con los intents `query_recent` y `query_balance`. El extractor usara un unico intent `query` con `dispatcher="query"`.

Motivo: El nuevo query dispatcher cubre estos casos componiendo tools como `aggregate_transactions` y `get_account_balance`. Mantener dos paths de queries conserva la complejidad que 6a viene a remover.

## 2026-04-27 - llm_query_dispatches separada de llm_extractions

Decision: El query dispatcher tendra una tabla `llm_query_dispatches` separada de `llm_extractions`.

Motivo: Las queries hacen loop multi-iteracion con tool use; forzar ese shape en `llm_extractions` rompería su schema y sus analytics.

Schema previsto para el bloque 3: `id`, `user_id`, `message_hash`, `total_iterations`, `total_input_tokens`, `total_output_tokens`, `tools_used` JSONB, `final_response_chars`, `error` TEXT NULL, `created_at`, `duration_ms`. Una fila por mensaje del usuario, no por iteracion.

## 2026-04-27 - Clarification es flujo write-only

Decision: El state machine de clarification (`telegram:clarify:{user_id}` en Redis) se aplica unicamente a writes (`Intent.LOG_EXPENSE`, `Intent.LOG_INCOME`). Queries (`Intent.QUERY`) no entran a clarification, ni siquiera cuando el extractor produce baja confianza.

Motivo: La ambiguedad en queries la resuelve el query dispatcher dentro del loop de tool use y conversation history (blocks 7+). El clarification original aplicaba defaults arbitrarios sin preguntar periodo/cuenta, y el query dispatcher tiene mejores herramientas para clarificar contextualmente.

## 2026-04-27 - Transacciones son day-granular

Decision: El modelo `transactions` usa `transaction_date DATE` sin hora de ocurrencia. Las tools de query respetan esta granularidad. `created_at` no se expone al LLM.

Motivo: `created_at` no representa cuando ocurrio la transaccion, solo cuando se inserto la fila. Si en el futuro algun caso de uso requiere hora exacta (recibos bancarios automaticos con timestamp, comparativas intra-dia, timezone edge cases), eso es Phase 1.5: refactor coordinado de modelo, schemas, services, dispatcher de write, Shortcut/Tasker, extractor LLM y tests. Fuera de scope de Phase 6a.

## 2026-04-27 - Transaction.amount conserva Decimal en tools

Decision: `Transaction.amount` esta anotado como `float` en el modelo y en schemas Pydantic (`api/models/transaction.py:26`, `api/schemas/transaction.py:25`), aunque la columna DB es `Numeric(12, 2)` y los services usan `Decimal`. Las tools de Phase 6a convierten a `Decimal` internamente y serializan montos como string en outputs al LLM.

Motivo: Esto preserva precision y evita ambiguedades de coma flotante en respuestas generadas por tools. La anotacion `float` es bug preexistente; arreglarlo masivamente requiere update coordinado de modelos, schemas, services, dispatcher y tests. Candidato a tech-debt sweep separado, no parche en Phase 6a.

## 2026-04-27 - compare_periods no devuelve delta por grupo

Decision: La tool `compare_periods` con `group_by` devuelve los grupos de cada periodo de forma independiente — `period_a.groups` y `period_b.groups` son listas separadas, sin un campo `delta_by_group` que cruce ambas. Solo `delta_amount` y `delta_percentage` a nivel total.

Motivo: Los grupos no necesariamente existen en ambos periodos. Una categoria puede aparecer en B pero no en A (o viceversa), el mismo `top_n` aplicado a cada periodo puede excluir grupos distintos, y forzar un schema de "delta_by_group" tendria que definir como manejar ausencias, lo que agrega complejidad sin claro beneficio. El LLM tiene los datos crudos de ambas listas y puede explicar diferencias, aparicion y desaparicion de grupos en lenguaje natural.

## 2026-04-28 - Conversation history para queries: text-only (Opción A)

Decision: El history del query dispatcher (Phase 6a, bloque 7) guarda
unicamente turnos `{role, content}` con texto plano. NO se persisten los
bloques `tool_use` ni `tool_result` que el LLM ve durante el loop. Los
campos `tool_calls?` y `tool_results?` del schema quedan opcionales y
vacios en este bloque.

Motivo:
- 3-5x menos tokens por turno guardado vs full-replay. Con cap de 10
  turnos y TTL 24h, esto se traduce directo en menor costo por
  follow-up.
- Cubre el 80% de los casos de follow-up esperados ("y la semana
  pasada?", "profundizá", "dame el desglose entonces"). El LLM ve la
  pregunta previa + la respuesta final y puede inferir que tool llamar
  de nuevo. La respuesta tipicamente contiene los datos relevantes
  (totales, categorias top, fechas), asi que no se pierde contexto
  practico.
- La auditoria completa de tool calls vive en `llm_query_dispatches`
  desde el bloque 3 (campos `tools_used`, `total_iterations`, etc.).
  Text-only no degrada trazabilidad — solo cambia que el LLM no ve los
  tool_results pasados, no que dejemos de loguearlos.

Failure mode esperado: follow-ups que referencian datos especificos de
un tool result previo sin que la respuesta los mencione textualmente
("y el segundo de esos?", "el del 12 de marzo"). Si aparecen en
pruebas/produccion, se escala a Opcion B (full-replay) o se introduce
un summarizer que extracte los datos relevantes a `content`.

## 2026-04-28 - Cache breakpoints con conversation history

Decision: La introduccion de history NO cambia la estrategia de cache.
Los 2 breakpoints existentes (fin del system prompt + fin del bloque
de tools) se quedan donde estan. El history NO lleva breakpoint
propio. Quedan 2 breakpoints libres de los 4 que permite Anthropic.

Motivo:
- El history cambia cada turno (cada user message agrega 2 entries).
  Cachearlo no da hits — el prefijo del prompt cacheado tiene que
  matchear exactamente, y el prefijo se rompe en cuanto agregas un
  turno nuevo.
- El system prompt (~5500 chars, ~1500 tokens) y el bloque de tools
  (~2000 tokens estimados) son los unicos componentes estables entre
  llamadas; ahi va el cache.
- Los 2 breakpoints libres se reservan para usos futuros: subdividir
  el system prompt (date context fuera del cache para extender hits
  cuando cambia la fecha) o cachear un summary de la conversacion si
  el history se vuelve muy largo en bloques posteriores.

## 2026-04-28 - Cap de history: 10 entries (no 10 round-trips)

Decision: El cap de 10 turnos del bloque 7 se aplica como 10 entries
en la lista (cada user message + cada assistant message es 1 entry),
no como 10 round-trips. Esto significa 5 round-trips en practica.

Motivo:
- La spec dice "lista de turnos `{role, content, ...}`. Cap: ultimos
  10 turnos. Truncar al inicio si excede." Cada item de la lista
  tiene `role`, asi que cada item es un turno. Interpretar "turno =
  round-trip" requiere agrupar por par (user, assistant) lo cual
  complica el truncate sin beneficio.
- 5 round-trips de history es suficiente para follow-ups encadenados
  ("qué gasté esta semana" -> "y la pasada?" -> "y el mes anterior?"
  -> ...). Si en uso real se ve que es poco, bumpear a 20 entries es
  un cambio de un numero.

## 2026-04-29 - Bloque 8: split policy = Opcion C (paragraph-aware con fallback)

Decision: `split_for_telegram` parte un texto largo asi:
1. Si `len(text) <= cap`, devolver `[text]`. (`cap=3900` por defecto,
   no 4096, para margen frente a entities y reescrituras de Telegram.)
2. Sino, dividir por parrafos (`\n\n`). Acumular parrafos en el chunk
   actual mientras quepan; cuando el siguiente parrafo lo excede,
   cerrar chunk y empezar otro.
3. Si un solo parrafo excede el cap, hard-break sobre ese parrafo:
   backscan desde `cap` buscando primero `\n`, luego ` `, y como
   ultimo recurso cortar exacto en `cap`.
4. Reescribir tags abiertos al cierre de cada chunk N y reabrirlos
   al inicio del chunk N+1 para que cada chunk sea HTML balanceado.
   El stack de tags abiertos se mantiene del walker de
   `sanitize_telegram_html` (mismo formato).

Motivo:
- Pure A (hard break) rompe `<b>...</b>` cuando el corte cae adentro;
  Telegram devuelve 400 "can't parse entities".
- Pure B (parrafos) falla cuando el LLM emite un parrafo gigante
  (tabla larga, lista densa); sin fallback, devolveriamos un chunk
  > 4096 = error de send.
- C es la union: por defecto respeta estructura, pero degrada
  graciosamente cuando el contenido no la tiene.

`cap=3900`: 4096 menos ~200 chars de margen para que Telegram pueda
agregar entities (links auto-detectados) sin pasarse del limite duro.

## 2026-04-29 - Bloque 8: HTML sanitization sin BeautifulSoup

Decision: `sanitize_telegram_html` usa una sola pasada con regex +
stack de tags abiertos. No se introduce dependencia (BeautifulSoup,
lxml, html5lib, bleach).

Motivo:
- Input tipico es ~500-3000 chars de texto generado por el LLM.
  Una pasada O(n) con tokenizer regex es ~50us en CPython; el
  setup de BeautifulSoup tarda mas que eso por mensaje.
- El subset HTML que acepta Telegram es pequeno (8 tags + atributo
  href en `<a>`). Whitelisting a mano en una funcion de 50 lineas es
  trivial y se debugea con `print` mejor que con un parser opaco.
- Defensa en profundidad sobre el system prompt: aunque el bloque 6
  prohibe asteriscos y backticks al LLM, la sanitizacion atrapa
  derivas (un tool result con `<` literal, una respuesta con `<ul>`
  por error) sin abortar el send. Tags desconocidos se strippean
  manteniendo el contenido. Tags huerfanos al cierre se completan
  (auto-close) en lugar de borrar el contenido del medio.
- `&`, `<`, `>` que NO son parte de un tag permitido se escapan a
  `&amp;`, `&lt;`, `&gt;`.

## 2026-04-29 - Bloque 8: catalogo de errores user-facing

Decision: el dispatcher mapea cada excepcion conocida a un mensaje
fijo en castellano. Mapping explicito en `app/queries/delivery.py::
handle_query_error(exc)`. La excepcion no llega al usuario; tampoco
trazas, IDs internos, ni nombres de tools.

| Excepcion | Origen | Mensaje al usuario | Log nivel |
|---|---|---|---|
| `IterationCapExceeded` | LLM client (loop) | "No pude completar tu consulta en el tiempo esperado. Probá reformulando." | WARNING |
| `QueryLLMClientError(category="server_error")` | LLM client (5xx) | "Hubo un problema temporal. Probá de nuevo en un minuto." | ERROR |
| `QueryLLMClientError(category="auth_error")` | LLM client (401/403) | "El servicio está temporalmente fuera de línea. Avisale al admin." | CRITICAL |
| `QueryLLMClientError(category="rate_limit")` | LLM client (429) | "Hubo un problema temporal. Probá de nuevo en un minuto." | WARNING |
| `QueryLLMClientError(category="client_error")` | LLM client (otros 4xx) | "Algo se rompió consultando tus datos. Avisale al admin." | ERROR |
| `QueryLLMClientError(category="timeout")` | LLM client | "No pude completar tu consulta en el tiempo esperado. Probá reformulando." | WARNING |
| `ToolExecutionError` | dispatcher | "Algo se rompió consultando tus datos. Avisale al admin." | ERROR |
| `BudgetExceeded` | dispatcher (pre-LLM) | "Llegaste al límite diario de consultas. Se renueva mañana." | INFO |
| `HTMLSanitizationFailed` | delivery | log + send `strip_html(text)` plano | ERROR |
| `ChunkOverflow` (chunk > 4096 tras split) | delivery | log CRITICAL + truncar con `…` | CRITICAL |
| Excepcion no clasificada | dispatcher | "Algo se rompió consultando tus datos. Avisale al admin." | ERROR |

Notas de implementacion:
- `QueryLLMClientError` lleva un atributo `category` con uno de
  `{timeout, rate_limit, server_error, client_error, auth_error,
  unknown}`. El llm_client mapea las excepciones del SDK Anthropic
  (`APITimeoutError`, `RateLimitError`, `InternalServerError`,
  `BadRequestError`, `AuthenticationError`, `PermissionDeniedError`)
  a esa categoria.
- `BudgetExceeded` se define en `app/queries/delivery.py` como un
  marker exception. Quien implemente la enforcement (sub-bloque 8.5)
  decide donde y como instanciarla — el handler ya la mapea.
- Retry automatico en 5xx queda fuera de scope. Se discute por
  separado.

## 2026-04-29 - Bloque 8 (PAUSED): budget enforcement diverge de la spec

La logica de budget cap ya existe pero no coincide con la spec del
bloque 8.5. Los cambios serian materiales, asi que pauso antes de
reescribir.

Estado actual (`bot/rate_limit.py`):
1. Storage: counter en Redis bajo `telegram:tokens:{user_id}:{yyyymmdd}`
   con TTL ~36h.
2. Check: `check_token_budget(user_id, redis, today)` — read del
   counter, compara con `settings.llm_daily_token_budget_per_user`.
3. Increment: `record_token_spend(user_id, redis, today, tokens)`
   despues de la llamada.
4. Cobertura: solo el extractor (write dispatcher) llama `record_token_spend`,
   con un FLAT de 500 tokens (`bot/pipeline.py:248`). El query
   dispatcher de Phase 6a NO incrementa nada.
5. Bucket: `today.strftime("%Y%m%d")` calculado con `_today_for(user)`
   que ya usa `user.timezone` (default America/Costa_Rica). El reset
   en CR midnight ya esta implicito.

Lo que pide la spec del bloque 8.5:
- Reset CR midnight: ya estamos. OK.
- Contabilidad: total_input + total_output, NO cache_read. Hoy es
  flat 500. Diverge.
- Storage: query a `llm_query_dispatches` filtrando por `user_id`
  y `created_at >= midnight_local`. Hoy es Redis. Diverge.
- Pre-check con buffer: estimar input + ~500 output, rechazar si
  excede. Hoy se chequea sin estimar el query siguiente. Diverge.

Diferencias materiales:
- Mover de Redis a DB: query extra por mensaje (~5ms agregados al
  hot path). A favor: la fuente de verdad es la tabla, no un counter
  paralelo que puede desync. En contra: depende del DB para gating
  de costos.
- Pasar de flat 500 a numeros reales: el extractor se queda igual o
  cambia tambien? El query dispatcher rinde input+output reales en
  `llm_query_dispatches`, pero la tabla `llm_extractions` tambien
  los tiene (ver bloque 5b). Si reescribimos, deberiamos unificar.
- Pre-check rechaza queries antes de gastar tokens en una respuesta
  que se va a frenar. Hoy una request que cruza el cap igual gasta
  esa request entera (porque el incrementa POST llamada).

Recomendacion: implementar la spec exactamente, contando ambos
dispatchers (extractor + query) desde sus tablas de log
respectivas. Reset CR midnight sigue. Costo extra por mensaje:
+1 query SELECT con indice en `(user_id, created_at)` — < 2ms en
local. La logica en `bot/rate_limit.py` se borra (record_token_spend,
check_token_budget) y se reemplaza por una funcion en
`api/services/budget.py` que el dispatcher llame antes del LLM.

PAUSADO: esperar visto bueno del usuario antes de hacer este cambio.

## 2026-04-29 - Bloque 9: typing indicator = refresh cada 4s en background task

Decision: el handler de texto del bot envuelve `process_message` en un
context manager async que crea una task de fondo. La task hace
`bot.send_chat_action(chat_id, "typing")` cada 4s hasta que el
context manager sale (exito o error). Cancelacion se hace en
`finally` con try/except para `CancelledError`.

Motivo:
- Telegram expira el typing indicator a 5s. Un dispatch corto
  (`hola`, confirm short-circuit) responde en 50ms y el typing nunca
  llega a verse. Pero el caso real que importa es la query con
  iteration_cap=4 + tools: 8-15s tipico, hasta 20s con 5xx retries.
  Sin refresh, el indicator se apaga a los 5s y la UX se siente
  caida.
- El modo simple "una sola llamada inicial" no cubre las queries
  largas, que son justamente las que mas demoran y donde el feedback
  visual importa. La complejidad extra es ~15 LOC.
- El indicator se dispara para *todo* mensaje de texto libre, incluso
  los que cortan via command/confirm short-circuit. Acceptable: el
  short-circuit responde antes de que el primer send_chat_action haga
  efecto visible. Filtrar por intent requeriria parsear antes de
  decidir, lo cual es lo opuesto al punto de un loading indicator.

Implementacion: `bot/handlers.py::typing_action(bot, chat_id)` async
context manager + `_typing_loop(bot, chat_id)` task helper.

## 2026-04-29 - Bloque 9: /clear borra query history, no toca pending writes

Decision: el comando `/clear` invoca `app.queries.history.clear_history(
user_id, redis)` y nada mas. El estado de pending writes
(`telegram:pending:{user_id}`), clarification
(`telegram:clarify:{user_id}`), pairing y rate limit no se tocan.

Mensaje de confirmacion: **"Listo, contexto limpio."** (1 frase, no
pregunta, no menciona "history" porque es jerga interna). Idempotente:
si no hay history en Redis, mismo mensaje — no exponemos al usuario
el detalle de si habia algo o no.

Motivo:
- Los pending writes los maneja `/cancel` (resuelve la fila de
  `pending_confirmations` como cancelled y limpia Redis). Mezclar
  responsabilidades complica el modelo mental: `/clear` para
  conversacion del query layer, `/cancel` para propuestas pendientes.
- "Listo, contexto limpio" es lo bastante claro para que un usuario
  no-tecnico entienda que la proxima query parte de cero, sin
  embarrar la spec con explicaciones de Redis ni de TTL.

## 2026-04-29 - Bloque 9: endpoint POST /api/v1/queries/test

Decision: `POST /api/v1/queries/test` corre la query a traves del
mismo dispatcher + delivery pipeline que el bot real. Auth via
`current_user` (X-Shortcut-Token preferido, X-User-Id shim aceptado
en dev — mismo dependency que el resto del API surface). No se usa el
webhook secret porque ese endpoint solo autentica requests firmados por
Telegram; este endpoint consulta datos de un usuario y debe seguir el
modelo tenant-scoped del API.

Body: `{"user_id": int, "query": str}`. `user_id` es el Telegram
`from.id` y se valida contra `current_user.telegram_user_id` cuando el
usuario esta pareado, pero NO selecciona el tenant. El tenant sale de
`current_user`; si no, un caller con token valido podria consultar datos
de otro usuario cambiando el body. `query` acepta 1..4096 chars.

Response 200: `{"reply", "chunks", "dispatch_id", "iterations",
"tools_used", "tokens"}`. `dispatch_id` se serializa como string porque
`llm_query_dispatches.id` es UUID en la migracion 0009. `chunks` es el
output del splitter post-sanitize (la lista que el bot real mandaria
como mensajes separados). `tokens` es `{"input", "output",
"cache_read", "cache_creation"}`.

Status codes:
- 401: ni X-Shortcut-Token ni X-User-Id presentes → mismo path que
  cualquier endpoint protegido.
- 429: presupuesto diario agotado (pre-check via
  `assert_within_budget` antes de llamar al dispatcher). El pre-check
  duplica el que hace el dispatcher internamente, pero permite
  responder con 429 explicito antes de que el dispatcher swallowee
  el `BudgetExceeded` y devuelva texto.
- 200 con error mapeado: `IterationCapExceeded` y demas errores
  caen al texto de `handle_query_error` y se devuelven en `reply`,
  con `iterations`/`tokens` parciales del intento. El cliente del
  endpoint distingue por contenido o `error_category` si lo
  agregamos despues.

Motivo:
- El endpoint sirve para curl smoke (bloque 11) y para debugging
  sin pasar por Telegram. Reusa el dispatcher real — duplicar logica
  de delivery (sanitize/split) garantiza divergencia con la primera
  feature que toque la una y olvide la otra.
- Refactor: `dispatcher.handle()` se mantiene como entrypoint que
  retorna `str` (compat con ~30 callers de tests + bot pipeline). Se
  agrega `dispatcher.run_dispatch()` que retorna `DispatchOutcome`
  con todos los counters; `handle()` delega y devuelve `outcome.text`.
- 429 vs 200: el bot path quiere texto plano para mostrar
  ("Llegaste al límite diario..."); un cliente HTTP quiere status
  code claro. El pre-check en el endpoint resuelve la asimetria sin
  cambiar la semantica del dispatcher.

## 2026-04-29 - Bloque 10: session factory inyectable para query layer

Decision: `app.queries.dispatcher` y las tools de `app.queries.tools.*`
usan `app.queries.session.AsyncSessionLocal`, un proxy callable que por
default delega a `api.database.AsyncSessionLocal`, pero que tests pueden
reemplazar con `set_query_session_factory(session_factory)`.

Motivo:
- El app engine global usa pool compartido. En pytest, cada test async
  corre con event loop propio; reutilizar conexiones asyncpg creadas en
  otro loop produce `Event loop is closed` / transport cerrado.
- El fixture `db_with_user` ya crea un engine `NullPool` por test. Bloque
  10 conecta dispatcher + tools a esa factory para que todas las sesiones
  vivan en el loop correcto.
- Se preserva compatibilidad con tests unitarios que monkeypatchean
  `module.AsyncSessionLocal`: cada modulo sigue teniendo ese nombre local.

Tambien se actualizaron fixtures legacy:
- `ExtractionResult` requiere `dispatcher`.
- `query_recent` y `query_balance` quedaron eliminados; las queries usan
  `intent="query"` + `dispatcher="query"`.
- El write dispatcher ahora rechaza `Intent.QUERY`; el routing debe
  interceptarlo antes y mandarlo a `app.queries.dispatcher`.

Los e2e que miden tool selection limpian `query_history` antes de cada
prompt para no confundir "eligio bien la tool" con "respondio desde
history". La continuidad sigue cubierta por los tests de bloque 7.

## 2026-04-29 - Bloque 11: curl guide ejecutable

Decision A - forma del archivo: usar script ejecutable
`docs/curl/phase-6a.sh`, con shebang, helpers compartidos y una funcion
bash por seccion (`section_1_health`, `section_2_query_simple`, etc.).
El dispatcher del final permite correr todo, listar secciones con
`--list`, o ejecutar una seccion por nombre completo o alias corto
(`section_2`). Esto lo hace util como guia leible y como smoke manual
repetible en staging/produccion.

Decision B - secretos: usar variables de entorno, sin valores reales
commiteados. Las obligatorias son `BASE_URL`, `INTERNAL_SECRET` y
`TEST_USER_ID`; `BASE_URL` defaulta a `http://localhost:8000` y las otras
dos deben venir seteadas. Como el endpoint real usa `current_user`, el
script agrega `AUTH_HEADER_NAME` con default `X-Shortcut-Token`; en dev se
puede usar `AUTH_HEADER_NAME=X-User-Id` e `INTERNAL_SECRET=<user_uuid>`.
`TEST_USER_ID` sigue siendo el Telegram `from.id` enviado en el body. Para
secciones de DB/Redis se aceptan env vars opcionales (`APP_USER_ID`,
`DATABASE_URL`, `PSQL_URL`, `REDIS_URL`); si faltan, la seccion queda
marcada como TODO/skip en runtime y no inventa endpoints.

Decision C - output: usar `curl -sS -w "\n%{http_code}\n"` y `jq` para
validar campos. Para no filtrar tokens en terminal ni reportes, los curls
con auth imprimen un comando redacted (`<redacted>`) en vez de `set -x`
literal sobre la cabecera. Los comandos sin secretos (psql/redis-cli) se
muestran antes de ejecutarse y todos los casos esperados de error HTTP se
manejan sin abortar el script.

Notas de contrato real:
- `POST /api/v1/queries/test` devuelve `dispatch_id` como UUID string, no
  entero numerico.
- El presupuesto agotado en este endpoint devuelve HTTP 429 por pre-check.
  El bot/dispatcher de Telegram lo convierte en texto para el usuario, pero
  el endpoint de debugging conserva status code explicito.
- No existe endpoint HTTP para leer o limpiar `query_history`; el guide usa
  `redis-cli` cuando `APP_USER_ID`/`REDIS_URL` estan disponibles, o
  `docker compose exec redis redis-cli` como fallback local. Si no hay forma
  de llegar a Redis, deja TODO claro y no inventa endpoints.

## 2026-04-27 - Debt.interest_rate es decimal fraccional, no porcentual

Decision: La columna `Debt.interest_rate` es `Numeric(5,4)` y se guarda como decimal fraccional, no como porcentaje. Un valor de `0.0850` en la DB representa 8.5% anual. Las tools de Phase 6a (`list_debts`, `get_debt_details`) convierten a porcentaje multiplicando por 100 antes de exponer al LLM, y la calculadora `api/services/amortization.generate_schedule` espera el valor fraccional directo.

Motivo: Bug-trap clasico. Cualquier codigo nuevo que lea `Debt.interest_rate` debe saber que es fraccional, no porcentual. Si en el futuro se cambia el formato (a `Numeric(5,2)` con porcentaje directo, o a basis points), hay que actualizar las tools, la calculadora de amortizacion, y todos los seeds de tests al mismo tiempo.
