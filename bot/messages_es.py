"""All user-facing Spanish strings for the Telegram bot.

Centralized so we can audit tone, pluralization, and CR vocabulary in one
place — and so a future i18n layer can replace this module without
hunting through handlers.
"""
from __future__ import annotations

# ── pairing ───────────────────────────────────────────────────────────────────

PAIR_PROMPT = (
    "¡Hola! Para usar este bot, pedí tu código de emparejamiento en la API:\n\n"
    "POST /api/v1/users/me/telegram/pairing-code\n\n"
    "Después mandame: /start <code>código</code>"
)
PAIR_SUCCESS = "Listo, {name}. Ya podés registrar gastos e ingresos por acá."
PAIR_BAD_CODE = (
    "Ese código no es válido o ya expiró. Pedí uno nuevo en la API y volvé a probar."
)
PAIR_TG_ACCOUNT_TAKEN = (
    "Esta cuenta de Telegram ya está vinculada a otro usuario. "
    "Si fuiste vos, desvinculala primero."
)
PAIR_USER_ALREADY_PAIRED = (
    "Ya tenés una cuenta de Telegram vinculada. Si querés cambiarla, "
    "usá POST /api/v1/users/me/telegram/unpair primero."
)
PAIR_CODE_ISSUED = "Código generado: {code}. Válido por 5 minutos."
PAIR_UNPAIRED = "Cuenta de Telegram desvinculada."


# ── capabilities / help ───────────────────────────────────────────────────────

HELP_TEXT = (
    "Puedo ayudarte con:\n\n"
    "• Registrar gastos: «gasté 5000 en el super»\n"
    "• Registrar ingresos: «me pagaron 400 mil»\n"
    "• Consultar: «¿cuánto gasté esta semana?»\n"
    "• Deshacer la última acción: /undo\n"
    "• Cancelar lo que estoy esperando: /cancel\n\n"
    "Siempre te pido confirmación antes de guardar nada."
)

WHO_AM_I = "Usuario: {email}\nCuenta por defecto: {default_account}"


# ── confirmation flow ─────────────────────────────────────────────────────────

CONFIRM_BUTTONS_YES = "Sí ✅"
CONFIRM_BUTTONS_NO = "No ❌"
CONFIRM_BUTTONS_EDIT = "Editar ✏️"

PENDING_OVERWRITTEN = (
    "Descarté la propuesta anterior y armé una nueva con esto último."
)
PENDING_EXPIRED = "Se me venció lo que estábamos confirmando. Mandá el mensaje de nuevo si querés."
PENDING_NONE_TO_CONFIRM = "No tengo nada pendiente por confirmar."

COMMITTED_EXPENSE = "Guardado: gasto de {amount}. Mandá /undo si te equivocaste."
COMMITTED_INCOME = "Guardado: ingreso de {amount}. Mandá /undo si te equivocaste."
COMMITTED_DISCARDED = "Listo, no guardé nada."

EDIT_PROMPT = (
    "¿Qué campo querés cambiar? Respondé con: monto / cuenta / categoría / fecha."
)


# ── queries ───────────────────────────────────────────────────────────────────

QUERY_EMPTY = "No tenés movimientos en ese período."
QUERY_RECENT_HEADER = "Últimos {n} movimientos:"
QUERY_BALANCE_HEADER = "Resumen del período:"


# ── undo ──────────────────────────────────────────────────────────────────────

UNDO_SUCCESS = "Deshice la última acción."
UNDO_NOTHING = "No tengo nada que deshacer."
UNDO_NOT_FOUND = "No encontré la última acción (ya no existe)."
UNDO_WRONG_SOURCE = "Esa transacción no se registró por Telegram, no la puedo deshacer desde acá."


# ── Gmail onboarding (Phase 6b) ──────────────────────────────────────────────

GMAIL_CONNECT_INTRO = (
    "Para que pueda revisar las notificaciones de tu banco, necesito leer "
    "tus correos en modo lectura.\n\n"
    "Te dejo un link de Google. Vas a ver una pantalla que dice "
    "<b>«Google no verificó esta app»</b> — es esperado en esta etapa. "
    "Hacé clic en <b>Avanzado</b> y luego en <b>Continuar</b>.\n\n"
    "Después volvés acá automáticamente."
)
GMAIL_CONNECT_BUTTON = "Conectar Gmail 🔐"
GMAIL_CONNECT_ALREADY_CONNECTED = (
    "Ya tenés Gmail conectado. Si querés reconectar, primero mandame "
    "/desconectar_gmail."
)
GMAIL_CONNECT_FAILED_CONFIG = (
    "No puedo arrancar el OAuth ahora — falta configuración del lado del "
    "servidor. Avisale a Daniel."
)
GMAIL_CALLBACK_SUCCESS = (
    "¡Conectado! 🎉\n\n"
    "Ahora decime de qué bancos te llegan notificaciones por correo. "
    "Tapeá los que correspondan abajo, o si tu banco no está en la lista, "
    "mandame el correo de remitente directo (algo como "
    "<code>notificaciones@tubanco.com</code>).\n\n"
    "Cuando termines, dale a <b>Listo ✅</b>."
)
GMAIL_BANK_SELECTION_HEADER_EMPTY = (
    "Aún no agregaste ningún banco. Tapeá uno de los botones o mandame un "
    "correo custom."
)
GMAIL_BANK_SELECTION_HEADER_TPL = (
    "Bancos seleccionados:\n{lines}\n\n"
    "Podés tapear más, mandar un correo custom, o darle a <b>Listo ✅</b>."
)
GMAIL_BANK_SELECTION_LISTO = "Listo ✅"
GMAIL_BANK_SELECTION_CANCELAR = "Cancelar"
GMAIL_BANK_SELECTION_LISTO_EMPTY = "Tenés que agregar al menos un banco primero."
GMAIL_BANK_PRESET_ASK_EMAIL = (
    "Decime el correo desde el que te llegan las notificaciones de "
    "<b>{bank}</b>. Algo como <code>notificaciones@tubanco.com</code>."
)
GMAIL_BANK_PRESET_ASK_EMAIL_SHORT = "Esperando el correo de {bank}."
GMAIL_BANK_PRESET_BUTTON_ACK = "Decime el correo de {bank}."
GMAIL_BANK_PRESET_ALREADY = "Ese correo ya está en la lista."
GMAIL_BANK_AWAITING_TPL = "\n\n⏳ Esperando el correo de <b>{bank}</b>."
GMAIL_BANK_LISTO_PENDING_BANK = (
    "Te falta mandarme el correo de <b>{bank}</b>. Mandalo o tapeá otro banco."
)
GMAIL_BANK_CUSTOM_ADDED_KNOWN = (
    "Agregué <code>{email}</code> (parece de <b>{bank}</b>). "
    "¿Algún otro? Cuando termines dale a <b>Listo ✅</b>."
)
GMAIL_BANK_CUSTOM_ADDED_UNKNOWN = (
    "Agregué <code>{email}</code>. No reconocí el banco automáticamente, "
    "pero igual lo voy a revisar. Cuando termines dale a <b>Listo ✅</b>."
)
GMAIL_BANK_CUSTOM_INVALID = (
    "Eso no parece un email. Probá de nuevo o tapeá uno de los bancos de arriba."
)
GMAIL_BANK_CUSTOM_ADDED_FOR_PRESET = (
    "Agregué <code>{email}</code> para <b>{bank}</b>. ¿Algún otro banco? "
    "Cuando termines dale a <b>Listo ✅</b>."
)
GMAIL_BANK_CAP_REACHED = (
    "Llegaste al máximo de 8 bancos. Si necesitás más, remové alguno con "
    "/quitar_banco después de activar."
)
GMAIL_BANK_CONFIRM_TPL = (
    "Voy a revisar correos de:\n{lines}\n\n"
    "Voy a empezar revisando los <b>últimos 30 días</b>, y la primera "
    "semana te muestro lo que encuentre <b>sin agregarlo</b> a tus saldos. "
    "¿Activamos?"
)
GMAIL_BANK_CONFIRM_EDIT = "Editar lista ✏️"
GMAIL_BANK_CANCELLED = "Cancelado. Mandá /conectar_gmail si querés reintentar."
GMAIL_CALLBACK_DENIED = (
    "Cancelaste el permiso de Google. Si fue sin querer, mandá "
    "/conectar_gmail de nuevo."
)
GMAIL_CALLBACK_ERROR = (
    "Algo salió mal del lado de Google. Mandá /conectar_gmail otra vez "
    "y si sigue fallando avisale a Daniel."
)
GMAIL_DISCONNECT_CONFIRM = (
    "¿Seguro querés desconectar tu Gmail? Voy a borrar el permiso y dejar "
    "de revisar correos."
)
GMAIL_DISCONNECT_DONE = "Listo, desconectado. Mandá /conectar_gmail si cambiás de idea."
GMAIL_DISCONNECT_NOT_CONNECTED = "No tenés Gmail conectado, no hay nada que desconectar."
GMAIL_STATUS_DISCONNECTED = (
    "Gmail: <b>no conectado</b>.\n\nUsá /conectar_gmail para empezar."
)
GMAIL_STATUS_CONNECTED_TPL = (
    "Gmail: <b>conectado</b>\n"
    "• Conectado el: {granted_at}\n"
    "• Activado: {activated_at}\n"
    "• Última corrida: {last_refresh_at}\n"
    "{whitelist_section}"
)
GMAIL_STATUS_NO_WHITELIST = "• Sin bancos agregados todavía. Usá /agregar_banco para empezar."
GMAIL_STATUS_WHITELIST_HEADER = "Bancos activos ({count}):"


# ── /agregar_banco / /quitar_banco / /agregar_muestra ────────────────────────

GMAIL_ADD_BANK_NOT_ACTIVE = (
    "Primero activá Gmail con /conectar_gmail antes de agregar bancos."
)
GMAIL_ADD_BANK_ENTRY = (
    "Decime qué banco querés agregar. Tapeá un preset o mandame un correo "
    "directo."
)
GMAIL_ADD_BANK_DONE_TPL = (
    "Listo, agregué:\n{lines}\n\nVoy a empezar a revisarlos en la próxima "
    "corrida (mañana 3am, o forzá con /revisar_correos)."
)
GMAIL_ADD_BANK_CANCELLED = "Cancelado, no agregué nada."
GMAIL_REMOVE_BANK_NO_ACTIVE = (
    "No tenés bancos activos. Si conectaste Gmail pero no agregaste ninguno, "
    "usá /agregar_banco."
)
GMAIL_REMOVE_BANK_PROMPT = (
    "Tapeá el banco que querés quitar. Las transacciones ya registradas se "
    "quedan; solo dejo de buscar nuevos correos de ese sender."
)
GMAIL_REMOVE_BANK_DONE_TPL = (
    "Removido <code>{email}</code>. Las transacciones ya registradas se quedan; "
    "solo dejo de buscar nuevos correos de ahí."
)
GMAIL_REMOVE_BANK_CANCELLED = "Cancelado."
GMAIL_REMOVE_BANK_NOT_FOUND = "Ese banco ya no está en tu lista."
GMAIL_ADD_SAMPLE_NOT_ACTIVE = (
    "Primero conectá Gmail con /conectar_gmail."
)
GMAIL_ADD_SAMPLE_PROMPT = (
    "Mandame foto o texto de un correo bancario que quieras que aprenda mejor. "
    "Esto es opcional — si el extractor te está funcionando bien, no hace falta.\n\n"
    "Tenés 10 minutos. Si te arrepentís, ignorá este mensaje."
)
GMAIL_ADD_SAMPLE_ANALYZING = "Analizando la muestra…"
GMAIL_ADD_SAMPLE_SAVED_TPL = (
    "Guardado. {detail}Voy a usar esto para mejorar el extractor. Gracias."
)
GMAIL_ADD_SAMPLE_SAVED_DETAIL_KNOWN = (
    "Detecté que es de <b>{bank}</b> ({sender}). "
)
GMAIL_ADD_SAMPLE_SAVED_DETAIL_UNKNOWN = ""
GMAIL_ADD_SAMPLE_DOWNLOAD_FAILED = (
    "No pude bajar la foto de Telegram. Probá pegando el texto en su lugar."
)
GMAIL_ADD_SAMPLE_ERROR = (
    "Algo falló al analizar la muestra. Intentá de nuevo en un rato."
)
GMAIL_MANUAL_SCAN_NOT_ACTIVE = (
    "Primero activá Gmail con /conectar_gmail antes de revisar correos."
)
GMAIL_MANUAL_SCAN_COOLDOWN = (
    "Ya revisé hace poco. Probá de nuevo en {minutes} minutos, "
    "o esperá la corrida automática de mañana."
)
GMAIL_MANUAL_SCAN_QUEUED = (
    "Revisando los últimos 2 días… te aviso cuando termine."
)


# ── Block C — notifier ───────────────────────────────────────────────────────

# After-scan notifications. Different from the activation handshake,
# these come from the scanner's finish hook (api/services/gmail/notifier).
# `Detecté` is intentional: the user didn't author these — the scanner did.

GMAIL_SCAN_INVALID_GRANT = (
    "Se desconectó tu Gmail (parece que revocaste el acceso o el "
    "permiso expiró). Volvé a conectar con /conectar_gmail."
)
GMAIL_SCAN_NO_WHITELIST = (
    "No tengo bancos en tu whitelist. Agregá al menos uno con "
    "/agregar_banco antes de la próxima corrida."
)
GMAIL_SCAN_NO_RESULTS_FIRST_BACKFILL = (
    "Revisé los últimos 30 días y no encontré correos de los bancos que "
    "agregaste. ¿Pusiste bien las direcciones? Revisá con /estado_gmail "
    "o agregá otros con /agregar_banco."
)
GMAIL_SCAN_NO_RESULTS_MANUAL = (
    "Revisé los últimos 2 días y no hay correos nuevos. Te aviso si "
    "aparece algo en la próxima corrida automática."
)
GMAIL_SCAN_FINISH_SHADOW_TPL = (
    "Listo. Revisé {scanned} correos: {matched} ya estaban registradas, "
    "{created} son nuevas en <b>modo sombra</b> esta semana. "
    "Mañana te mando un resumen y podés decidir si las apruebo o no."
)
GMAIL_SCAN_FINISH_BATCH_TPL = (
    "Encontré {created} transacciones nuevas en {scanned} correos:\n{lines}"
    "{tail}"
)
GMAIL_SCAN_FINISH_QUIET_TPL = (
    "Listo. Revisé {scanned} correos: {matched} ya estaban registradas, "
    "{created} son nuevas."
)

# Per-transaction notification (≤ batch threshold, outside shadow).
GMAIL_TXN_DETECTED_EXPENSE_TPL = "Detecté un gasto de {amount} en {merchant}."
GMAIL_TXN_DETECTED_EXPENSE_NO_MERCHANT_TPL = "Detecté un gasto de {amount}."
GMAIL_TXN_DETECTED_INCOME_TPL = "Detecté un ingreso de {amount} de {origin}."
GMAIL_TXN_DETECTED_INCOME_NO_ORIGIN_TPL = "Detecté un ingreso de {amount}."


# ── shadow daily summary + /aprobar_shadow + /rechazar_shadow ────────────────

GMAIL_SHADOW_SUMMARY_HEADER_TPL = (
    "Resumen del día (modo sombra): encontré <b>{count}</b> "
    "transacciones que NO sumé a tus saldos todavía:"
)
GMAIL_SHADOW_SUMMARY_FOOTER = (
    "\n\nSi todo se ve bien, dale a /aprobar_shadow cuando termine la "
    "semana de prueba.\nPara descartar, /rechazar_shadow."
)
GMAIL_SHADOW_SUMMARY_ITEM_TPL = "• {amount} — {merchant_or_desc}"
GMAIL_SHADOW_SUMMARY_TAIL_TPL = "\n…y {n} más."

GMAIL_APPROVE_SHADOW_NONE = "No tenés transacciones en modo sombra."
GMAIL_APPROVE_SHADOW_DONE_TPL = (
    "Aprobadas <b>{count}</b> transacciones. Ya cuentan en tus saldos."
)
GMAIL_REJECT_SHADOW_CONFIRM_TPL = (
    "¿Estás seguro? Voy a borrar <b>{count}</b> transacciones detectadas "
    "en modo sombra. Esta acción no se puede deshacer."
)
GMAIL_REJECT_SHADOW_DONE_TPL = (
    "Borré <b>{count}</b> transacciones en modo sombra y las marqué para "
    "análisis. Si pensás que el bot está parsing mal, mandame /agregar_muestra "
    "con un correo de ejemplo."
)
GMAIL_REJECT_SHADOW_CANCELLED = "Cancelado, no toqué nada."
GMAIL_REJECT_SHADOW_BUTTON_YES = "Sí, descartar"


# ── Sample collection (Phase 6b — block 6) ───────────────────────────────────

GMAIL_SAMPLE_ANALYZING = "Analizando…"
GMAIL_SAMPLE_LOW_CONFIDENCE = (
    "No estoy seguro de qué banco es. ¿Tenés otro ejemplo? Probá con un "
    "correo más completo."
)
GMAIL_SAMPLE_GIVE_UP = (
    "No pude identificar el banco después de varios intentos. Vamos a "
    "dejar la activación por hoy — mandame /conectar_gmail más tarde "
    "para reintentar con otra muestra."
)
GMAIL_SAMPLE_CONFIRM_TPL = (
    "Detecté que es de <b>{bank}</b> ({sender}).\n\n¿Está bien?"
)
GMAIL_SAMPLE_CONFIRM_YES = "Sí, está bien ✅"
GMAIL_SAMPLE_CONFIRM_RETRY = "No, otro banco ❌"
GMAIL_SAMPLE_NOT_TEXT_OR_PHOTO = (
    "Mandame el ejemplo como <b>foto</b> o como <b>texto pegado</b>. "
    "Otros formatos no los puedo leer."
)
GMAIL_SAMPLE_PHOTO_DOWNLOAD_FAILED = (
    "No pude bajar la foto desde Telegram. Probá pegando el texto en su lugar."
)
GMAIL_ACTIVATE_PROMPT = (
    "Listo. Voy a revisar correos de <b>{sender}</b> y otros bancos "
    "conocidos.\n\nVoy a empezar revisando los últimos 30 días, y la "
    "<b>primera semana</b> te voy a mostrar lo que encuentre <b>sin "
    "agregarlo todavía a tus saldos</b>. Si te late, dale a activar."
)
GMAIL_ACTIVATE_BUTTON = "Activar 🚀"
GMAIL_ACTIVATED = (
    "¡Activado! Empiezo a revisar tus correos. Te aviso cuando haya "
    "novedades."
)

GMAIL_ONBOARDING_NOT_IN_FLOW = (
    "No tengo nada que esperar de vos ahora. Si querés conectar Gmail, "
    "mandá /conectar_gmail."
)
UNDO_LINKED = (
    "Esa transacción ya se usó para pagar una factura. "
    "Desmarcá la factura primero si querés revertir."
)


# ── rate limit / budget ───────────────────────────────────────────────────────

RATE_LIMIT_HIT = "Vas muy rápido. Esperá un minuto y seguimos."
DAILY_BUDGET_HIT = (
    "Ya usé bastantes tokens contigo hoy. Probemos mañana — "
    "si urge, avisame por otro canal."
)


# ── extractor / dispatcher failures ──────────────────────────────────────────

EXTRACTOR_FAILED = "Se me trabó el entendimiento. ¿Podés reescribir el mensaje más simple?"
CANCELLED = "Cancelado."
CONTEXT_CLEARED = "Listo, contexto limpio."


# ── nudge callbacks ───────────────────────────────────────────────────────────

NUDGE_EXPIRED = "Ese recordatorio ya no está activo."
NUDGE_ACK_ACT_MISSING_INCOME = (
    "¡Dale! Mandame cuánto te entra al mes, por ejemplo: "
    "«gano 500 mil al mes» o «me pagan quincenal 250 mil»."
)
NUDGE_ACK_ACT_STALE_PENDING = (
    "Perfecto. Mandame «sí» o tapeá Sí ✅ cuando estés listo para confirmarla."
)
NUDGE_ACK_ACT_UPCOMING_BILL = (
    "¡Genial! Si querés registrar el pago, mandame algo como "
    "«gasté ₡35000 en ICE»."
)
NUDGE_ACK_DISMISS_HARD = (
    "Entendido. Por 2 semanas no te molesto con recordatorios de este tipo."
)
NUDGE_ACK_DISMISS_SOFT = "Listo, descartado."
NUDGE_ACK_LATER = (
    "Okey. Te aviso la próxima vez que chequee, si sigue pendiente."
)
