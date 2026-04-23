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
