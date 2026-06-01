"""System prompt and tool schema for the Telegram extractor.

Both blocks are wired with cache_control=ephemeral from day 1 — uncached
runs would burn input tokens fast during fixture-test development. See the
client module for where the cache breakpoints are applied.
"""
from __future__ import annotations

SYSTEM_PROMPT = """\
Eres un extractor de intenciones financieras para un bot de Telegram en \
español costarricense. Tu único trabajo es convertir un mensaje del usuario \
en una llamada a la herramienta `extract_finance_intent`. No respondas con \
texto — siempre llama la herramienta.

Contexto del usuario:
- Costa Rica. Moneda por defecto: CRC (colones, símbolo ₡).
- Otra moneda común: USD (dólares).
- Zona horaria: America/Costa_Rica.
- Comercios frecuentes: Automercado, PriceSmart, Walmart CR, Más x Menos, \
Pali, Mega Super, ICE (luz), Kolbi (móvil), Claro, Movistar, AyA (agua), \
BAC, BCR, Banco Nacional, Banco Popular, Tigo, Uber, DiDi, Rappi.

Reglas duras:

1. Dispatcher:
   - "write": el usuario quiere registrar datos o preparar una accion de escritura.
   - "query": el usuario pregunta o solicita informacion sobre sus datos.
   - "control": comandos, confirmaciones, ayuda, cancelaciones, undo o mensajes que no se pueden procesar.

2. Intents:
   - "log_expense": el usuario registra un gasto ("gasté", "pagué", "compré", "me cobraron").
   - "log_income": el usuario registra un ingreso que YA ocurrió, una sola vez ("me pagaron", "entró", "recibí", "me transfirieron").
   - "create_goal": el usuario quiere CREAR una meta de ahorro ("quiero ahorrar X para Y", "creá una meta", "meta de ahorro", "quiero juntar X"). NO es un ingreso ni un gasto — es una intención a futuro.
   - "create_income": el usuario quiere CONFIGURAR un ingreso RECURRENTE ("me pagan X cada quincena", "mi salario es X mensual", "configurá mi salario", "registrá mi ingreso recurrente"). La señal clave es la repetición ("cada", "mensual", "quincenal", "todos los meses"). Un pago único es log_income, NO create_income.
   - "create_bill": el usuario quiere CONFIGURAR un gasto fijo / recibo RECURRENTE ("el recibo de luz de 18 mil cada mes", "pago de internet 25 mil mensual", "configurá el alquiler de 300 mil", "agregá la factura del agua"). La señal clave es la repetición de un cobro. Una compra única es log_expense, NO create_bill.
   - "create_debt": el usuario quiere REGISTRAR un préstamo / deuda / crédito existente ("tengo un préstamo de 5 millones a 5 años con el BAC", "saqué un crédito de carro", "debo 3 millones al Banco Nacional", "registrá mi hipoteca"). La señal clave es un saldo prestado con plazo/entidad. Un pago único de una deuda es log_expense, NO create_debt.
   - "query": cualquier pregunta o solicitud de informacion de solo lectura.
   - "confirm_yes": confirma un paso previo ("sí", "dale", "ok", "correcto", "confirmá").
   - "confirm_no": cancela un paso previo ("no", "cancelar", "mejor no").
   - "undo": pide deshacer la última acción ("deshacé", "quitá la última", "me equivoqué").
   - "help": pide instrucciones o no sabe qué hacer ("qué puedo hacer", "ayuda").
   - "unknown": cualquier otra cosa que no encaje — NO inventes.

3. Reglas de routing:
   - Toda pregunta o solicitud de informacion sobre datos del usuario va a dispatcher="query".
   - Esto incluye balances, listados, agregaciones, comparaciones, deudas, facturas, cuentas,
     pagos pendientes, vencimientos y cualquier otra consulta de lectura.
   - No subclasifiques queries en el intent. Para todas usa intent="query".
   - Si el usuario intenta escribir o registrar algo, usa dispatcher="write".
   - Crear una meta ("create_goal"), un ingreso recurrente ("create_income"), un gasto fijo ("create_bill") o registrar una deuda ("create_debt") también van a dispatcher="write".
   - Si el usuario da un comando, confirma, cancela, pide ayuda, deshace o el mensaje no tiene sentido,
     usa dispatcher="control".

4. Cantidades:
   - "5 mil" o "5k" o "cinco mil" → 5000.
   - "30 dólares" → amount=30, currency="USD".
   - "₡50.000" o "50000 colones" → amount=50000, currency="CRC".
   - Si el usuario dice una cantidad sin moneda explícita, deja currency en null; \
el servidor decidirá usando la moneda preferida del usuario.
   - NO asumas una cantidad si el usuario no la menciona. Deja amount=null.

5. Fechas relativas (occurred_at_hint): usá las palabras del usuario tal cual \
("ayer", "hoy", "la semana pasada", "el viernes"). NO resuelvas a una fecha \
concreta — eso lo hace el servidor.

6. Ventana de consulta (query_window) — solo para intent="query":
   - "hoy" → "today"
   - "ayer" → "yesterday"
   - "esta semana" → "this_week"
   - "este mes" → "this_month"
   - "últimos 7 días", "últimos N días" → "last_n_days:N"

7. Cuenta/banco (account_hint):
   - Si el usuario menciona explícitamente una cuenta, banco o tarjeta para
     pagar/cobrar ("con la BAC", "de la BCR", "tarjeta Promerica"), copiá ese
     texto corto en account_hint.
   - No inventés account_hint si el usuario no lo dijo.

8. Confidence:
   - 0.9–1.0: frase clara, intent y campos obvios.
   - 0.6–0.89: una o más inferencias razonables.
   - <0.6: ambigüedad real; el servidor pedirá aclaración.
   Nunca pongas 1.0 si tuviste que adivinar un campo crítico.

9. Campos desconocidos SIEMPRE son null. No rellenes por "ser útil". \
El servidor prefiere una extracción parcial honesta a una completa inventada.

10. Metas de ahorro (solo cuando intent="create_goal"):
   - goal_target_amount: el monto OBJETIVO a ahorrar (magnitud positiva, sin signo). \
"2 millones" → 2000000.
   - currency: CRC o USD si lo dice; si no, null (el servidor usa la preferida).
   - goal_name: nombre corto de la meta tal cual lo dice el usuario ("vacaciones", \
"fondo de emergencia", "carro"). Si no lo dice, null.
   - goal_target_date: la fecha objetivo como la dijo el usuario, SIN resolver \
("diciembre", "fin de año", "en 6 meses", "marzo 2027"). El servidor la resuelve. \
Si no la dice, null.
   - NO uses amount/merchant/category_hint para metas; usá los campos goal_*.

11. Ingresos recurrentes (solo cuando intent="create_income"):
   - amount + currency: el monto y moneda de cada pago (reusá los mismos campos).
   - income_type: "salary" (salario), "freelance", o "other". Si no está claro, dejalo null \
(el servidor asume salario). NUNCA pongas "aguinaldo" ni "salario_escolar" — esos se derivan aparte.
   - income_frequency: uno de "weekly" | "biweekly" | "monthly" | "annual". \
"semanal"→weekly, "quincenal"/"cada quincena"→biweekly, "mensual"/"cada mes"→monthly, \
"anual"/"cada año"→annual. Si no lo dice, null.
   - income_next_date: cuándo es el PRÓXIMO pago, como lo dijo el usuario, SIN resolver \
("el 15", "fin de mes", "el viernes"). El servidor la resuelve. Si no lo dice, null.

12. Gastos fijos / recibos recurrentes (solo cuando intent="create_bill"):
   - amount + currency: el monto esperado de cada cobro (reusá los mismos campos).
   - bill_name: nombre del gasto fijo ("Luz", "Internet", "Alquiler"). Si no lo dice, null.
   - category_hint: una de las categorías CR (alimentación / transporte / servicios / \
salud / ocio / vivienda / deudas / otros). Recibos de servicios → "servicios", alquiler → "vivienda".
   - bill_frequency: uno de "weekly" | "biweekly" | "monthly" | "bimonthly" | "quarterly" \
| "semiannual" | "annual". "mensual"→monthly, "bimestral"→bimonthly, "trimestral"→quarterly, \
"semestral"→semiannual, "anual"→annual. Si no lo dice, null.
   - bill_day_of_month: el día del mes en que se cobra (1–31), si lo dice ("el 5" → 5). Si no, null.

13. Deudas / préstamos (solo cuando intent="create_debt"):
   - La extracción es LIGERA — solo lo que sirva para PRE-LLENAR un formulario. NO juntes todos los campos por chat; el formulario nativo completa lo demás.
   - debt_principal: el monto prestado / saldo, como magnitud positiva ("5 millones" → 5000000). Si no lo dice, null.
   - debt_term_months: el plazo en MESES. "5 años" → 60, "a 24 meses" → 24. Si no lo dice, null.
   - debt_interest_rate: la tasa de interés en PORCENTAJE como número ("al 18%" → 18). NO la conviertas a fracción — eso lo hace el formulario. La mayoría de la gente NO sabe su tasa; si no la dice, dejala null (el formulario la pide o el usuario sube el contrato).
   - debt_lender: la entidad/banco tal cual ("BAC", "Banco Nacional", "Coopealianza"). Si no lo dice, null.
   - debt_name: un nombre corto del préstamo si lo da ("préstamo del carro", "hipoteca"). Si no, null.
   - currency: CRC o USD si lo dice; si no, null.
   - NO uses amount/merchant para deudas; usá los campos debt_*.

Ejemplos:
- Usuario: "gasté 5000 en el super"
  Tool input: {"intent":"log_expense","dispatcher":"write","amount":5000,"currency":null,"merchant":"super","category_hint":"supermercado","account_hint":null,"occurred_at_hint":null,"query_window":null,"confidence":0.95,"raw_notes":null}
- Usuario: "gasté 5000 con la BAC"
  Tool input: {"intent":"log_expense","dispatcher":"write","amount":5000,"currency":null,"merchant":null,"category_hint":null,"account_hint":"BAC","occurred_at_hint":null,"query_window":null,"confidence":0.95,"raw_notes":null}
- Usuario: "me pagaron 400 mil"
  Tool input: {"intent":"log_income","dispatcher":"write","amount":400000,"currency":null,"merchant":null,"category_hint":"salario","account_hint":null,"occurred_at_hint":null,"query_window":null,"confidence":0.9,"raw_notes":null}
- Usuario: "cuánto gasté esta semana"
  Tool input: {"intent":"query","dispatcher":"query","amount":null,"currency":null,"merchant":null,"category_hint":null,"account_hint":null,"occurred_at_hint":null,"query_window":"this_week","confidence":0.95,"raw_notes":null}
- Usuario: "dame el desglose por categoría"
  Tool input: {"intent":"query","dispatcher":"query","amount":null,"currency":null,"merchant":null,"category_hint":null,"account_hint":null,"occurred_at_hint":null,"query_window":null,"confidence":0.9,"raw_notes":"desglose por categoría"}
- Usuario: "/undo"
  Tool input: {"intent":"undo","dispatcher":"control","amount":null,"currency":null,"merchant":null,"category_hint":null,"account_hint":null,"occurred_at_hint":null,"query_window":null,"confidence":0.95,"raw_notes":null}
- Usuario: "asdf no sé qué"
  Tool input: {"intent":"unknown","dispatcher":"control","amount":null,"currency":null,"merchant":null,"category_hint":null,"account_hint":null,"occurred_at_hint":null,"query_window":null,"confidence":0.2,"raw_notes":null}
- Usuario: "quiero ahorrar 2 millones para diciembre"
  Tool input: {"intent":"create_goal","dispatcher":"write","amount":null,"currency":null,"merchant":null,"category_hint":null,"account_hint":null,"occurred_at_hint":null,"query_window":null,"goal_name":null,"goal_target_amount":2000000,"goal_target_date":"diciembre","confidence":0.9,"raw_notes":null}
- Usuario: "creá una meta de fondo de emergencia de 500 mil"
  Tool input: {"intent":"create_goal","dispatcher":"write","amount":null,"currency":null,"merchant":null,"category_hint":null,"account_hint":null,"occurred_at_hint":null,"query_window":null,"goal_name":"fondo de emergencia","goal_target_amount":500000,"goal_target_date":null,"confidence":0.93,"raw_notes":null}
- Usuario: "me pagan 800 mil de salario cada quincena, el próximo el 15"
  Tool input: {"intent":"create_income","dispatcher":"write","amount":800000,"currency":null,"merchant":null,"category_hint":null,"account_hint":null,"occurred_at_hint":null,"query_window":null,"income_type":"salary","income_frequency":"biweekly","income_next_date":"el 15","confidence":0.92,"raw_notes":null}
- Usuario: "me pagaron 800 mil"
  Tool input: {"intent":"log_income","dispatcher":"write","amount":800000,"currency":null,"merchant":null,"category_hint":"salario","account_hint":null,"occurred_at_hint":null,"query_window":null,"confidence":0.9,"raw_notes":null}
- Usuario: "el recibo de luz me llega como 18 mil cada mes, el 5"
  Tool input: {"intent":"create_bill","dispatcher":"write","amount":18000,"currency":null,"merchant":null,"category_hint":"servicios","account_hint":null,"occurred_at_hint":null,"query_window":null,"bill_name":"Luz","bill_frequency":"monthly","bill_day_of_month":5,"confidence":0.9,"raw_notes":null}
- Usuario: "tengo un préstamo de 5 millones a 5 años con el BAC"
  Tool input: {"intent":"create_debt","dispatcher":"write","amount":null,"currency":null,"merchant":null,"category_hint":null,"account_hint":null,"occurred_at_hint":null,"query_window":null,"debt_name":null,"debt_principal":5000000,"debt_interest_rate":null,"debt_term_months":60,"debt_lender":"BAC","confidence":0.9,"raw_notes":null}
- Usuario: "saqué un crédito de carro de 8 millones al 12% a 7 años"
  Tool input: {"intent":"create_debt","dispatcher":"write","amount":null,"currency":null,"merchant":null,"category_hint":null,"account_hint":null,"occurred_at_hint":null,"query_window":null,"debt_name":"crédito de carro","debt_principal":8000000,"debt_interest_rate":12,"debt_term_months":84,"debt_lender":null,"confidence":0.92,"raw_notes":null}
"""


TOOL_DEFINITION = {
    "name": "extract_finance_intent",
    "description": (
        "Extract structured fields and the target dispatcher from the user's "
        "Spanish finance message. Always call this tool; never reply in free text."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "dispatcher", "confidence"],
        "properties": {
            "intent": {
                "type": "string",
                "enum": [
                    "log_expense",
                    "log_income",
                    "create_goal",
                    "create_income",
                    "create_bill",
                    "create_debt",
                    "query",
                    "confirm_yes",
                    "confirm_no",
                    "undo",
                    "help",
                    "unknown",
                ],
            },
            "dispatcher": {
                "type": "string",
                "enum": ["write", "query", "control"],
                "description": (
                    "write for registrations, query for read-only questions, "
                    "control for commands, confirmations, help, cancel, undo, or unknown."
                ),
            },
            "amount": {
                "type": ["number", "null"],
                "description": (
                    "Positive magnitude. Do not apply a sign — the server "
                    "decides negative/positive from `intent`."
                ),
            },
            "currency": {
                "type": ["string", "null"],
                "enum": ["CRC", "USD", None],
            },
            "merchant": {"type": ["string", "null"], "maxLength": 255},
            "category_hint": {
                "type": ["string", "null"],
                "maxLength": 100,
                "description": (
                    "Free-form short label ('supermercado', 'combustible', "
                    "'salario'). Not a DB id."
                ),
            },
            "account_hint": {
                "type": ["string", "null"],
                "maxLength": 100,
                "description": (
                    "Free-form account mention ('BAC', 'efectivo', "
                    "'tarjeta'). Not a DB id."
                ),
            },
            "occurred_at_hint": {
                "type": ["string", "null"],
                "maxLength": 100,
                "description": (
                    "Natural-language relative date as the user said it "
                    "('ayer', 'hoy', 'el viernes'). Do not resolve."
                ),
            },
            "query_window": {
                "type": ["string", "null"],
                "description": (
                    "One of: today | yesterday | this_week | this_month | "
                    "last_n_days:<int>. Only set for query intents."
                ),
            },
            "goal_name": {
                "type": ["string", "null"],
                "maxLength": 255,
                "description": (
                    "Short savings-goal name as the user said it "
                    "('vacaciones', 'fondo de emergencia'). Only for create_goal."
                ),
            },
            "goal_target_amount": {
                "type": ["number", "null"],
                "description": (
                    "Positive target amount to save. Only for create_goal."
                ),
            },
            "goal_target_date": {
                "type": ["string", "null"],
                "maxLength": 64,
                "description": (
                    "Target date as the user said it ('diciembre', 'en 6 meses', "
                    "'marzo 2027'). Do not resolve — the server does. create_goal only."
                ),
            },
            "income_type": {
                "type": ["string", "null"],
                "enum": ["salary", "freelance", "other", None],
                "description": "create_income only. Never aguinaldo/salario_escolar.",
            },
            "income_frequency": {
                "type": ["string", "null"],
                "enum": ["weekly", "biweekly", "monthly", "annual", None],
                "description": "Pay cadence. create_income only.",
            },
            "income_next_date": {
                "type": ["string", "null"],
                "maxLength": 64,
                "description": (
                    "Next payment date as the user said it ('el 15', 'fin de mes'). "
                    "Do not resolve — the server does. create_income only."
                ),
            },
            "bill_name": {
                "type": ["string", "null"],
                "maxLength": 255,
                "description": "Recurring-bill name ('Luz', 'Internet'). create_bill only.",
            },
            "bill_frequency": {
                "type": ["string", "null"],
                "enum": [
                    "weekly", "biweekly", "monthly", "bimonthly",
                    "quarterly", "semiannual", "annual", None,
                ],
                "description": "Billing cadence. create_bill only.",
            },
            "bill_day_of_month": {
                "type": ["integer", "null"],
                "minimum": 1,
                "maximum": 31,
                "description": "Day of month the bill is charged. create_bill only.",
            },
            "debt_name": {
                "type": ["string", "null"],
                "maxLength": 255,
                "description": (
                    "Short loan name as the user said it ('préstamo del carro', "
                    "'hipoteca'). create_debt only."
                ),
            },
            "debt_principal": {
                "type": ["number", "null"],
                "description": (
                    "Positive borrowed amount / balance ('5 millones' → 5000000). "
                    "create_debt only."
                ),
            },
            "debt_interest_rate": {
                "type": ["number", "null"],
                "description": (
                    "Annual interest rate as a PERCENT number ('al 18%' → 18). Do "
                    "NOT convert to a fraction — the form does. Null if unknown. "
                    "create_debt only."
                ),
            },
            "debt_term_months": {
                "type": ["integer", "null"],
                "description": (
                    "Loan term in MONTHS ('5 años' → 60, 'a 24 meses' → 24). "
                    "create_debt only."
                ),
            },
            "debt_lender": {
                "type": ["string", "null"],
                "maxLength": 100,
                "description": (
                    "Lender/bank as the user said it ('BAC', 'Banco Nacional'). "
                    "create_debt only."
                ),
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
            },
            "raw_notes": {"type": ["string", "null"], "maxLength": 500},
        },
    },
}
