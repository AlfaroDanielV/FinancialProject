"""Phase 6f debt slice (D2) — PDF loan-term extraction.

The user uploads a loan contract/statement (PDF); we send it to Claude as a
`document` content block and extract the loan terms into a validated
`DebtTermsExtraction`. The interest rate is the hardest field for a real user
to know off the top of their head — reading it from the contract removes that
friction.

Two-pass strategy mirrors `vision.py`: Haiku first, Sonnet retry if confidence
< 0.65. This obeys "LLM extracts; rules decide": the model only PROPOSES terms
to pre-fill the native form; the deterministic `POST /debts` is still the only
write path. Nothing is created here.

The base64-encoded PDF is stored inside `llm_extractions.extraction` under the
key "pdf_b64" so uploads can be audited later. Azure Blob migration is tracked
as P8 tech debt in CLAUDE.md (same as receipt images).
"""
from __future__ import annotations

import base64
import logging
import re
import time
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.llm_extraction import LLMExtraction
from ...models.user import User
from ...schemas.card_terms import CardTermsExtraction
from ...schemas.debts import DebtTermsExtraction
from ...schemas.statements import (
    StatementExtractionV2,
    StmtAccount,
    StmtProductExtraction,
)
from .client import LLMClient, LLMClientError

log = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.65
_DOCUMENT_TIMEOUT_S = 30.0
_PDF_MEDIA_TYPE = "application/pdf"
# A bank statement normalizes into a large NESTED tool-use JSON
# (accounts[] -> currency_legs[] -> flows[] + closing_candidates[]). The default
# 512-token cap (fine for a one-line transaction or the flat debt/card schemas)
# TRUNCATES that JSON mid-`accounts`, so it validated into an empty extraction
# (`accounts=[]`) and surfaced as "No pude leer el estado" / 0 productos. Real CR
# statements need 1.3k-5.8k output tokens (a BAC 5-product bundle measured 5754),
# so the statement path asks for generous headroom. max_tokens is a CEILING billed
# only on tokens actually generated, so the extra room is free.
_STATEMENT_MAX_TOKENS = 8192
# Opus generating up to 8192 tokens over a multi-page PDF runs well past the 30s
# default (sized for a 512-token transaction) → APITimeoutError → a 502 ("No pude
# leer"). Statement parsing is a rare, user-initiated upload, so it gets a
# generous read timeout. (Anthropic's guidance is to STREAM for high max_tokens;
# raising the timeout is the minimal fix — streaming is a tracked follow-up.)
_STATEMENT_TIMEOUT_S = 275.0

_DOCUMENT_SYSTEM_PROMPT = (
    "Sos un extractor de términos de préstamos para un sistema financiero "
    "costarricense. Tu único trabajo es leer el documento adjunto (contrato o "
    "estado de cuenta de un préstamo) y llamar la herramienta extract_loan_terms. "
    "No respondas con texto — siempre llamá la herramienta. Si un dato no está "
    "en el documento, dejalo en null; NO inventes. Preferimos una extracción "
    "parcial honesta a una completa inventada."
)

_DOCUMENT_PROMPT = (
    "Adjunté el documento de mi préstamo. Extraé los términos usando la "
    "herramienta extract_loan_terms. La tasa de interés (interest_rate) debe "
    "ir como FRACCIÓN anual entre 0 y 1: por ejemplo '18%' → 0.18, '12,5%' → "
    "0.125. Si no encontrás algún dato, dejalo en null."
)

_LOAN_TERMS_TOOL = {
    "name": "extract_loan_terms",
    "description": (
        "Extract loan terms from the attached Costa Rican loan contract or "
        "statement PDF. Always call this tool; never reply in free text. Leave "
        "any field that is not present in the document as null."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["confidence"],
        "properties": {
            "original_amount": {
                "type": ["number", "null"],
                "description": "Original principal of the loan (positive).",
            },
            "interest_rate": {
                "type": ["number", "null"],
                "description": (
                    "Annual interest rate as a FRACTION 0–1 (18% → 0.18). NOT a "
                    "percent."
                ),
            },
            "term_months": {
                "type": ["integer", "null"],
                "description": "Loan term in months (5 years → 60).",
            },
            "minimum_payment": {
                "type": ["number", "null"],
                "description": "Monthly payment / cuota (positive).",
            },
            "lender": {
                "type": ["string", "null"],
                "description": "Lending institution ('BAC', 'Banco Nacional').",
            },
            "start_date": {
                "type": ["string", "null"],
                "description": "Loan start/disbursement date as YYYY-MM-DD.",
            },
            "rate_type": {
                "type": ["string", "null"],
                "enum": ["fixed", "variable", None],
                "description": "Fixed or variable rate.",
            },
            "includes_insurance": {
                "type": ["boolean", "null"],
                "description": "Whether the payment includes insurance.",
            },
            "insurance_monthly": {
                "type": ["number", "null"],
                "description": "Monthly insurance amount, if itemized.",
            },
            "currency": {
                "type": ["string", "null"],
                "enum": ["CRC", "USD", None],
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "How confident the extraction is. Low when the document is "
                    "a scan, unclear, or not a loan contract."
                ),
            },
        },
    },
}


# ── Phase 7b B3: credit-card statement/contract extraction ────────────────────

_CARD_SYSTEM_PROMPT = (
    "Sos un extractor de términos de tarjetas de crédito para un sistema "
    "financiero costarricense. Tu único trabajo es leer el documento adjunto "
    "(el CONTRATO de la tarjeta, o un estado de cuenta si eso fue lo que "
    "subieron) y llamar la herramienta extract_card_terms. No respondas con "
    "texto — siempre llamá la herramienta. Si un dato no está en el "
    "documento, dejalo en null; NO inventes. Preferimos una extracción "
    "parcial honesta a una completa inventada."
)

_CARD_PROMPT = (
    "Adjunté el contrato (o estado de cuenta) de mi tarjeta de crédito. "
    "Extraé los términos usando la herramienta extract_card_terms. Las tasas "
    "(annual_interest_rate, cash_advance_rate, minimum_payment_pct) van como "
    "FRACCIÓN entre 0 y 1: '45%' → 0.45, '2,5%' → 0.025. statement_day es el "
    "día de corte y payment_due_day el día límite de pago (1–31). OJO: muchas "
    "tarjetas costarricenses operan en AMBAS monedas — si el documento "
    "distingue términos en colones y en dólares, poné los de colones en los "
    "campos base y los de dólares en los campos *_usd; si la tarjeta es de "
    "una sola moneda, dejá los *_usd en null. Si no encontrás algún dato, "
    "dejalo en null."
)

_CARD_TERMS_TOOL = {
    "name": "extract_card_terms",
    "description": (
        "Extract credit-card terms from the attached Costa Rican card "
        "contract (or statement) PDF. Always call this tool; never reply in "
        "free text. Leave any field that is not present in the document as "
        "null. Many CR cards operate in BOTH currencies: base fields are the "
        "COLONES terms; fill the *_usd fields only when the document lists "
        "separate dollar terms."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["confidence"],
        "properties": {
            "issuer": {
                "type": ["string", "null"],
                "description": "Issuing bank ('BAC', 'Promerica').",
            },
            "credit_limit": {
                "type": ["number", "null"],
                "description": (
                    "Credit limit / límite de crédito in COLONES (positive)."
                ),
            },
            "statement_balance": {
                "type": ["number", "null"],
                "description": (
                    "Statement balance owed / saldo al corte in COLONES "
                    "(positive magnitude). Usually only on statements, not "
                    "contracts."
                ),
            },
            "annual_interest_rate": {
                "type": ["number", "null"],
                "description": (
                    "Annual PURCHASE interest rate in COLONES as a FRACTION "
                    "0–1 (45% → 0.45). NOT a percent."
                ),
            },
            "cash_advance_rate": {
                "type": ["number", "null"],
                "description": (
                    "Annual CASH-ADVANCE rate as a FRACTION 0–1, if listed."
                ),
            },
            "annual_interest_rate_usd": {
                "type": ["number", "null"],
                "description": (
                    "Annual PURCHASE rate in DOLLARS as a FRACTION 0–1, only "
                    "when the document lists a separate USD rate (CR dual-"
                    "currency cards usually do; it's often lower than the "
                    "colones rate)."
                ),
            },
            "credit_limit_usd": {
                "type": ["number", "null"],
                "description": (
                    "Credit limit in DOLLARS, only when listed separately."
                ),
            },
            "statement_balance_usd": {
                "type": ["number", "null"],
                "description": (
                    "Statement balance owed in DOLLARS (positive magnitude), "
                    "only when the statement carries a separate USD balance."
                ),
            },
            "minimum_payment_pct": {
                "type": ["number", "null"],
                "description": (
                    "Minimum payment as a FRACTION of the balance 0–1 "
                    "('pago mínimo 2.5%' → 0.025), if the formula is listed."
                ),
            },
            "minimum_payment_amount": {
                "type": ["number", "null"],
                "description": (
                    "The minimum payment AMOUNT printed on this statement "
                    "(positive)."
                ),
            },
            "statement_day": {
                "type": ["integer", "null"],
                "description": "Día de corte (1–31).",
            },
            "payment_due_day": {
                "type": ["integer", "null"],
                "description": "Día límite de pago (1–31).",
            },
            "currency": {
                "type": ["string", "null"],
                "enum": ["CRC", "USD", None],
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "How confident the extraction is. Low when the document "
                    "is a scan, unclear, or not a credit-card document."
                ),
            },
        },
    },
}


# ── Bank-statement reconciliation: normalize into semantic primitives ─────────
# The SAME shape for every bank and account type. The LLM tags account_type /
# direction / role / contingent and COPIES amounts verbatim; it computes nothing.
# Deterministic code (statement_normalize.build_reconcile_plan) does conservation,
# dedup, target-role selection, and ledger resolution.

_STATEMENT_SYSTEM_PROMPT = (
    "Sos un extractor de estados de cuenta bancarios costarricenses. Tu único "
    "trabajo es leer el estado de cuenta adjunto (PDF) y llamar la herramienta "
    "extract_statement. No respondas con texto — siempre llamá la herramienta. "
    "Copiás los montos TAL CUAL aparecen impresos (positivos); NO calculás, NO "
    "sumás, NO decidís cuál saldo es 'el correcto'. Solo etiquetás. Si un dato "
    "no está, dejalo en null; NO inventes. Preferimos una extracción parcial "
    "honesta a una inventada."
)

_STATEMENT_PROMPT = (
    "Adjunté mi estado de cuenta. Llamá extract_statement con UNA entrada por "
    "cada CUENTA o producto (no por cada tarjeta física).\n\n"
    "Un estado de cuenta SIEMPRE tiene al menos una cuenta: incluí TODO lo que "
    "encontrés (cuentas de depósito, tarjetas de crédito, préstamos, líneas de "
    "crédito, inversiones). Una tarjeta de crédito es una cuenta válida. NUNCA "
    "devolvás 'accounts' vacío si el documento es un estado de cuenta; si dudás "
    "de un saldo, igual incluí la cuenta y dejá ese monto en null.\n\n"
    "account_type: 'checking'/'savings' para cuentas a la vista o de ahorro, "
    "'credit_card' para tarjeta de crédito, 'loan' para préstamo, "
    "'line_of_credit' para línea de crédito, 'investment' para inversión.\n\n"
    "UNA tarjeta puede tener varias tarjetas físicas (titular + adicionales) con "
    "distintos números: es UNA sola cuenta con UN saldo por moneda. Poné cada "
    "número en 'instruments' (sin monto). NO creés una cuenta por cada tarjeta "
    "física. Si la tarjeta opera en colones Y dólares con saldos separados, eso "
    "SÍ son dos 'currency_legs' dentro de la MISMA cuenta.\n\n"
    "Por cada currency_leg:\n"
    "- opening_balance = el 'saldo anterior' impreso (positivo), o null si no "
    "aparece.\n"
    "- flows = cada movimiento del período. amount = monto POSITIVO impreso. "
    "direction = 'inflow' para lo que ENTRA / reduce la deuda (depósitos, pagos "
    "recibidos, abonos, créditos) y 'outflow' para lo que SALE / aumenta la "
    "deuda (compras, retiros, cargos, comisiones). contingent = true SOLO para "
    "montos que todavía NO forman parte del saldo a pagar de contado este corte "
    "— típicamente los intereses corrientes que se cobran solo si no pagás de "
    "contado, o cuotas futuras de un plan. El interés ya capitalizado NO es "
    "contingente.\n"
    "- closing_candidates = cada saldo final impreso CON su rol. Roles:\n"
    "  • 'payoff' = saldo de contado / pago total de una TARJETA ('para no "
    "generar intereses').\n"
    "  • 'financed' = saldo financiado de una TARJETA de crédito (lo que sigue "
    "financiándose). NO lo uses para el total de un préstamo.\n"
    "  • 'minimum' = pago mínimo. 'closing' = saldo al corte de una cuenta a la "
    "vista / ahorro. 'available' = saldo disponible.\n"
    "  • 'principal_outstanding' = el saldo de principal ACTUAL de un préstamo, "
    "AL CORTE / a la fecha (lo que falta por pagar de principal HOY). NO es el "
    "monto original NI el total a pagar con intereses futuros.\n"
    "  • 'original_principal' = el monto ORIGINAL / financiado / desembolsado "
    "del préstamo (con el que se abrió).\n"
    "  • 'total_with_interest' = un 'saldo total a pagar' o 'total de cuotas "
    "pendientes' que YA INCLUYE intereses futuros (suele ser bastante mayor que "
    "el principal).\n"
    "  • 'market_value' = valor de mercado de una inversión. 'previous' = saldo "
    "anterior.\n"
    "  En un préstamo vas a ver VARIOS montos (ej.: 'Monto original "
    "₡10.000.000', 'Saldo a la fecha ₡6.200.000', 'Total a pagar ₡8.900.000'): "
    "etiquetá CADA uno con su rol (original_principal, principal_outstanding, "
    "total_with_interest respectivamente). Copiá TODOS los saldos con su rol; "
    "NO elijas vos cuál es el correcto.\n\n"
    "identifiers = IBAN / número de cuenta / últimos 4 dígitos si aparecen. "
    "period_end = la fecha de corte como YYYY-MM-DD (solo el día).\n\n"
    "confidence = qué tan seguro estás de la extracción, de 0 a 1 (bajo si el "
    "PDF es un escaneo borroso o no parece un estado de cuenta). SIEMPRE incluí "
    "este campo."
)

_BALANCE_ROLES = [
    "closing",
    "available",
    "financed",
    "payoff",
    "minimum",
    "principal_outstanding",
    "market_value",
    "previous",
    "original_principal",
    "total_with_interest",
]

_STATEMENT_TOOL = {
    "name": "extract_statement",
    "description": (
        "Normalize a Costa Rican bank statement PDF into semantic primitives: "
        "one entry per ACCOUNT (not per physical card), each with its currency "
        "legs, sign-tagged flows, and role-tagged closing balances. Always call "
        "this tool; never reply in free text. Copy amounts verbatim as positive "
        "magnitudes; only TAG them. Leave any absent field null."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["accounts", "confidence"],
        "properties": {
            "bank": {
                "type": ["string", "null"],
                "description": "Issuing bank ('BAC', 'Promerica', 'BCR').",
            },
            "period_start": {
                "type": ["string", "null"],
                "description": "Statement period start as YYYY-MM-DD.",
            },
            "period_end": {
                "type": ["string", "null"],
                "description": (
                    "Fecha de corte (period end) as YYYY-MM-DD — the anchor date."
                ),
            },
            "due_date": {
                "type": ["string", "null"],
                "description": "Payment due date as YYYY-MM-DD, if printed.",
            },
            "accounts": {
                "type": "array",
                "description": "One entry per account/product (NOT per card).",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["account_type", "currency_legs"],
                    "properties": {
                        "account_type": {
                            "type": "string",
                            "enum": [
                                "checking",
                                "savings",
                                "credit_card",
                                "loan",
                                "line_of_credit",
                                "investment",
                            ],
                        },
                        "issuer": {
                            "type": ["string", "null"],
                            "description": "Issuing institution if distinct from bank.",
                        },
                        "product_name": {
                            "type": ["string", "null"],
                            "description": (
                                "Product name as printed ('Cuenta a la vista', "
                                "'VISA Emerald Infinite')."
                            ),
                        },
                        "identifiers": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["kind", "value"],
                                "properties": {
                                    "kind": {
                                        "type": "string",
                                        "enum": [
                                            "iban",
                                            "account_number",
                                            "masked_pan",
                                            "last4",
                                            "product_code",
                                        ],
                                    },
                                    "value": {"type": "string"},
                                },
                            },
                        },
                        "instruments": {
                            "type": "array",
                            "description": (
                                "Physical cards under this account — attribution "
                                "ONLY, never a balance."
                            ),
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "masked_pan": {"type": ["string", "null"]},
                                    "holder_name": {"type": ["string", "null"]},
                                    "is_supplementary": {"type": "boolean"},
                                },
                            },
                        },
                        "currency_legs": {
                            "type": "array",
                            "description": "One per currency the account runs in.",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["currency"],
                                "properties": {
                                    "currency": {
                                        "type": "string",
                                        "enum": ["CRC", "USD"],
                                    },
                                    "opening_balance": {
                                        "type": ["number", "null"],
                                        "description": (
                                            "'Saldo anterior' as a positive "
                                            "magnitude, or null."
                                        ),
                                    },
                                    "flows": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["amount", "direction"],
                                            "properties": {
                                                "amount": {
                                                    "type": "number",
                                                    "description": (
                                                        "Positive magnitude as "
                                                        "printed."
                                                    ),
                                                },
                                                "direction": {
                                                    "type": "string",
                                                    "enum": ["inflow", "outflow"],
                                                    "description": (
                                                        "inflow = entra / reduce "
                                                        "deuda; outflow = sale / "
                                                        "aumenta deuda."
                                                    ),
                                                },
                                                "contingent": {
                                                    "type": "boolean",
                                                    "description": (
                                                        "true only if NOT in the "
                                                        "pay-in-full balance "
                                                        "(current-period interest)."
                                                    ),
                                                },
                                                "label_raw": {
                                                    "type": ["string", "null"]
                                                },
                                            },
                                        },
                                    },
                                    "closing_candidates": {
                                        "type": "array",
                                        "description": (
                                            "Every printed ending balance, each "
                                            "with its role. Do NOT pick one."
                                        ),
                                        "items": {
                                            "type": "object",
                                            "additionalProperties": False,
                                            "required": ["amount", "role"],
                                            "properties": {
                                                "amount": {"type": "number"},
                                                "role": {
                                                    "type": "string",
                                                    "enum": _BALANCE_ROLES,
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": (
                    "How confident the extraction is. Low when the document is "
                    "a scan, unclear, or not a bank statement."
                ),
            },
        },
    },
}


# ── Per-product verification pass (the "parse each product in isolation" step) ─
# After the inventory pass reads the whole PDF, a focused pass re-reads it scoped
# to ONE product and returns that product's primitives PLUS a narrow judgment of
# its CURRENT balance (so a dense bundle can't bleed a loan's balance role across
# products, and the role tags get a second, focused read to cross-check).

_STATEMENT_PRODUCT_SYSTEM_PROMPT = (
    "Sos un extractor de estados de cuenta bancarios costarricenses, enfocado en "
    "UN solo producto. Tu único trabajo es leer el PDF adjunto y llamar la "
    "herramienta extract_statement_product SOLO con los datos del producto que "
    "te indiquen. Copiás los montos TAL CUAL aparecen impresos (positivos); NO "
    "calculás, NO sumás. Etiquetás los saldos con su rol y das tu juicio sobre "
    "cuál es el saldo ACTUAL. Si un dato no está, dejalo en null; NO inventes."
)

_VERIFICATION_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "description": "Tu juicio enfocado sobre el SALDO ACTUAL de ESTE producto al corte.",
    "properties": {
        "current_balance_amount": {
            "type": ["number", "null"],
            "description": (
                "El saldo ACTUAL al corte (lo que se debe / hay HOY), copiado del "
                "documento. Para un préstamo, el PRINCIPAL que falta hoy."
            ),
        },
        "current_balance_role": {
            "type": ["string", "null"],
            "enum": _BALANCE_ROLES + [None],
        },
        "printed_label_raw": {
            "type": ["string", "null"],
            "description": "La etiqueta impresa EXACTA de ese saldo.",
        },
        "is_original_amount": {
            "type": "boolean",
            "description": (
                "true si el saldo actual que encontraste se parece al monto "
                "ORIGINAL / financiado del préstamo (no al saldo de hoy)."
            ),
        },
        "includes_future_interest": {
            "type": "boolean",
            "description": (
                "true si ese saldo INCLUYE intereses futuros (un 'total a pagar')."
            ),
        },
        "account_type_confirmed": {
            "type": ["string", "null"],
            "enum": [
                "checking",
                "savings",
                "credit_card",
                "loan",
                "line_of_credit",
                "investment",
                None,
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

_STATEMENT_PRODUCT_TOOL = {
    "name": "extract_statement_product",
    "description": (
        "Normalize ONE account/product from a Costa Rican bank statement into "
        "semantic primitives, plus a focused judgment of its CURRENT balance. "
        "Always call this tool; never reply in free text. Copy amounts verbatim "
        "as positive magnitudes; only TAG them. Leave absent fields null."
    ),
    "input_schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["account_type", "currency_legs", "confidence"],
        "properties": {
            **_STATEMENT_TOOL["input_schema"]["properties"]["accounts"]["items"][
                "properties"
            ],
            "verification": _VERIFICATION_SCHEMA,
            "confidence": {
                "type": "number",
                "minimum": 0,
                "maximum": 1,
                "description": "How confident this focused extraction is.",
            },
        },
    },
}

_ACCOUNT_TYPE_ES = {
    "checking": "cuenta corriente",
    "savings": "cuenta de ahorro",
    "credit_card": "tarjeta de crédito",
    "loan": "préstamo",
    "line_of_credit": "línea de crédito",
    "investment": "inversión",
}


def _product_descriptor(acct: StmtAccount) -> str:
    """A short human descriptor of one product, to scope the focused pass."""
    bits = [_ACCOUNT_TYPE_ES.get(acct.account_type, acct.account_type)]
    name = acct.product_name or acct.issuer
    if name:
        bits.append(f"«{name}»")
    last4 = None
    for ident in acct.identifiers:
        digits = re.sub(r"\D", "", ident.value or "")
        if len(digits) >= 4:
            last4 = digits[-4:]
            break
    if last4:
        bits.append(f"(•••{last4})")
    currencies = sorted({(leg.currency or "CRC").upper() for leg in acct.currency_legs})
    if currencies:
        bits.append(f"[{', '.join(currencies)}]")
    return " ".join(bits)


def _product_prompt(descriptor: str) -> str:
    return (
        "Adjunté mi estado de cuenta completo. AHORA concentrate SOLO en este "
        f"producto: {descriptor}. Ignorá los demás.\n\n"
        "Llamá extract_statement_product con los datos de ESE producto: "
        "account_type, identifiers, instruments y currency_legs (cada uno con "
        "opening_balance, flows y closing_candidates etiquetados con su rol). "
        "Para un préstamo, etiquetá por SEPARADO el monto original "
        "(original_principal), el saldo de principal ACTUAL al corte "
        "(principal_outstanding) y cualquier 'total a pagar' con intereses "
        "futuros (total_with_interest).\n\n"
        "En 'verification', mirando SOLO este producto, decime cuál es el SALDO "
        "ACTUAL al corte (current_balance_amount + su rol + la etiqueta impresa "
        "exacta) y si ese saldo en realidad es el monto ORIGINAL "
        "(is_original_amount) o un total con intereses futuros "
        "(includes_future_interest). El saldo actual de un préstamo es el "
        "PRINCIPAL que falta HOY, NO el monto original NI el total a pagar. "
        "Copiá los montos tal cual; no calculés."
    )


def _merge_product(draft_acct: StmtAccount, product: StmtProductExtraction) -> StmtAccount:
    """Prefer the focused per-product primitives, but keep the draft's identity /
    instruments when the focused pass dropped them (so resolution/dedup don't
    regress). Carries the verification judgment for the guardrails."""
    return StmtAccount(
        account_type=product.account_type or draft_acct.account_type,
        issuer=product.issuer or draft_acct.issuer,
        product_name=product.product_name or draft_acct.product_name,
        identifiers=product.identifiers or draft_acct.identifiers,
        instruments=product.instruments or draft_acct.instruments,
        currency_legs=product.currency_legs or draft_acct.currency_legs,
        verification=product.verification,
    )


async def _extract_one_product(
    *,
    user: User,
    pdf_bytes: bytes,
    client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
    db: AsyncSession,
    descriptor: str,
) -> Optional[StmtProductExtraction]:
    """One focused per-product pass. Failure is NON-fatal — the caller falls back
    to the inventory-pass draft for that product, so a flaky product never tanks
    the whole reconcile."""
    try:
        return await _extract_document(
            user=user,
            pdf_bytes=pdf_bytes,
            client=client,
            haiku_model=haiku_model,
            sonnet_model=sonnet_model,
            db=db,
            system_prompt=_STATEMENT_PRODUCT_SYSTEM_PROMPT,
            user_prompt=_product_prompt(descriptor),
            tool=_STATEMENT_PRODUCT_TOOL,
            result_model=StmtProductExtraction,
            intent="parse_statement_product",
            is_empty=lambda r: not r.currency_legs,
            max_tokens=_STATEMENT_MAX_TOKENS,
            timeout_s=_STATEMENT_TIMEOUT_S,
        )
    except Exception:
        log.warning(
            "statement_product_extract_failed user_id=%s descriptor=%s",
            getattr(user, "id", None),
            descriptor,
            exc_info=True,
        )
        return None


async def extract_debt_terms(
    *,
    user: User,
    pdf_bytes: bytes,
    client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
    db: AsyncSession,
) -> DebtTermsExtraction:
    """Run PDF loan-term extraction with Haiku-first, Sonnet retry on low
    confidence. Returns a validated `DebtTermsExtraction` (does NOT create a
    debt)."""
    return await _extract_document(
        user=user,
        pdf_bytes=pdf_bytes,
        client=client,
        haiku_model=haiku_model,
        sonnet_model=sonnet_model,
        db=db,
        system_prompt=_DOCUMENT_SYSTEM_PROMPT,
        user_prompt=_DOCUMENT_PROMPT,
        tool=_LOAN_TERMS_TOOL,
        result_model=DebtTermsExtraction,
        intent="parse_debt_document",
    )


async def extract_card_terms(
    *,
    user: User,
    pdf_bytes: bytes,
    client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
    db: AsyncSession,
) -> CardTermsExtraction:
    """Phase 7b B3 — card-term extraction from a statement/contract PDF.
    Same Haiku-first / Sonnet-retry contract as `extract_debt_terms`. Returns
    a validated `CardTermsExtraction` (does NOT create anything)."""
    return await _extract_document(
        user=user,
        pdf_bytes=pdf_bytes,
        client=client,
        haiku_model=haiku_model,
        sonnet_model=sonnet_model,
        db=db,
        system_prompt=_CARD_SYSTEM_PROMPT,
        user_prompt=_CARD_PROMPT,
        tool=_CARD_TERMS_TOOL,
        result_model=CardTermsExtraction,
        intent="parse_card_document",
    )


async def extract_statement(
    *,
    user: User,
    pdf_bytes: bytes,
    client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
    db: AsyncSession,
) -> StatementExtractionV2:
    """Normalize a bank statement PDF into the semantic primitives
    (`StatementExtractionV2`). Two passes for robustness:

    1. **Inventory + draft** — one call over the whole PDF lists the products and
       a first read of their primitives.
    2. **Per-product** — each product is then re-read in ISOLATION (a focused
       call scoped to that one account/loan) which re-extracts its primitives and
       adds a narrow `verification` judgment of its CURRENT balance. This stops a
       dense multi-product bundle from bleeding a loan's balance role across
       products and gives the role tags a second, focused cross-check that the
       deterministic guardrails (`statement_normalize` / `statement_guardrails`)
       validate.

    Sequential (the shared AsyncSession isn't concurrency-safe); Haiku keeps it
    quick. A failed per-product pass falls back to its draft. Same Haiku-first /
    Sonnet-retry contract per call. Does NOT write anything — `build_reconcile_
    plan` turns this into a reconcile plan."""
    draft = await _extract_document(
        user=user,
        pdf_bytes=pdf_bytes,
        client=client,
        haiku_model=haiku_model,
        sonnet_model=sonnet_model,
        db=db,
        system_prompt=_STATEMENT_SYSTEM_PROMPT,
        user_prompt=_STATEMENT_PROMPT,
        tool=_STATEMENT_TOOL,
        result_model=StatementExtractionV2,
        intent="parse_statement",
        # An empty `accounts` means the model couldn't read the statement (often a
        # card/dual-currency layout) — force the retry the same way a low
        # confidence does, so we give a second pass before the user sees
        # "0 productos".
        is_empty=lambda r: not r.accounts,
        # Statements emit a large nested JSON; the default 512 cap truncates them.
        max_tokens=_STATEMENT_MAX_TOKENS,
        # ...and a multi-page PDF + that output blows past the 30s document default.
        timeout_s=_STATEMENT_TIMEOUT_S,
    )
    if not draft.accounts:
        return draft

    merged: list[StmtAccount] = []
    confidences = [draft.confidence]
    for draft_acct in draft.accounts:
        product = await _extract_one_product(
            user=user,
            pdf_bytes=pdf_bytes,
            client=client,
            haiku_model=haiku_model,
            sonnet_model=sonnet_model,
            db=db,
            descriptor=_product_descriptor(draft_acct),
        )
        if product is None:
            merged.append(draft_acct)
            continue
        merged.append(_merge_product(draft_acct, product))
        confidences.append(product.confidence)

    return StatementExtractionV2(
        bank=draft.bank,
        period_start=draft.period_start,
        period_end=draft.period_end,
        due_date=draft.due_date,
        accounts=merged,
        confidence=min(confidences),
    )


async def _extract_document(
    *,
    user: User,
    pdf_bytes: bytes,
    client: LLMClient,
    haiku_model: str,
    sonnet_model: str,
    db: AsyncSession,
    system_prompt: str,
    user_prompt: str,
    tool: dict,
    result_model,
    intent: str,
    is_empty=None,
    max_tokens: int = 512,
    timeout_s: float = _DOCUMENT_TIMEOUT_S,
):
    pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
    content_blocks: list[dict] = [
        {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": _PDF_MEDIA_TYPE,
                "data": pdf_b64,
            },
        },
        {"type": "text", "text": user_prompt},
    ]

    result = await _run_one(
        user=user,
        content_blocks=content_blocks,
        pdf_b64=pdf_b64,
        client=client,
        model=haiku_model,
        db=db,
        is_retry=False,
        system_prompt=system_prompt,
        tool=tool,
        result_model=result_model,
        intent=intent,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
    )

    if result.confidence < _CONFIDENCE_THRESHOLD or (
        is_empty is not None and is_empty(result)
    ):
        result = await _run_one(
            user=user,
            content_blocks=content_blocks,
            pdf_b64=pdf_b64,
            client=client,
            model=sonnet_model,
            db=db,
            is_retry=True,
            system_prompt=system_prompt,
            tool=tool,
            result_model=result_model,
            intent=intent,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )

    return result


async def _run_one(
    *,
    user: User,
    content_blocks: list[dict],
    pdf_b64: str,
    client: LLMClient,
    model: str,
    db: AsyncSession,
    is_retry: bool,
    system_prompt: str = _DOCUMENT_SYSTEM_PROMPT,
    tool: dict = _LOAN_TERMS_TOOL,
    result_model=DebtTermsExtraction,
    intent: str = "parse_debt_document",
    max_tokens: int = 512,
    timeout_s: float = _DOCUMENT_TIMEOUT_S,
):
    t0 = time.perf_counter()
    raw = await client.extract(
        user_message=content_blocks,
        prior_turns=[],
        system_prompt=system_prompt,
        tool=tool,
        model=model,
        timeout_s=timeout_s,
        max_tokens=max_tokens,
    )

    latency_ms = int((time.perf_counter() - t0) * 1000)

    # Anthropic tool-use does NOT hard-enforce a tool's `required` fields, so a
    # model (Haiku especially) occasionally omits `confidence`. Treat a
    # missing/null confidence as low (0.0) instead of 500-ing on validation:
    # on the Haiku pass it forces the Sonnet retry, and a still-missing value
    # surfaces a low-confidence review note downstream rather than a crash.
    tool_input = raw.tool_input
    if (
        isinstance(tool_input, dict)
        and "confidence" in result_model.model_fields
        and tool_input.get("confidence") is None
    ):
        tool_input = {**tool_input, "confidence": 0.0}

    try:
        result = result_model.model_validate(tool_input)
    except ValidationError as e:
        await _log(
            db=db,
            user=user,
            confidence=None,
            extraction={
                "invalid": True,
                "errors": e.errors(include_context=False),
                "raw": raw.tool_input,
                "document": True,
                "is_retry": is_retry,
                "stop_reason": raw.stop_reason,
            },
            latency_ms=latency_ms,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            cache_read_tokens=raw.cache_read_input_tokens,
            cache_creation_tokens=raw.cache_creation_input_tokens,
            model=model,
            intent=intent,
        )
        raise

    payload = result.model_dump(mode="json")
    payload["pdf_b64"] = pdf_b64
    payload["document"] = True
    payload["is_retry"] = is_retry
    # Audit the truncation signal: a `max_tokens` stop on a statement is exactly
    # the failure that hid as a silent empty extraction (see _STATEMENT_MAX_TOKENS).
    payload["stop_reason"] = raw.stop_reason

    await _log(
        db=db,
        user=user,
        confidence=result.confidence,
        extraction=payload,
        latency_ms=latency_ms,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        cache_read_tokens=raw.cache_read_input_tokens,
        cache_creation_tokens=raw.cache_creation_input_tokens,
        model=model,
        intent=intent,
    )
    return result


async def _log(
    *,
    db: AsyncSession,
    user: User,
    confidence: Optional[float],
    extraction: dict,
    latency_ms: int,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    model: str,
    intent: str = "parse_debt_document",
) -> None:
    row = LLMExtraction(
        user_id=user.id,
        message_hash=f"document:{_PDF_MEDIA_TYPE}",
        intent=intent,
        confidence=confidence,
        extraction=extraction,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        model=model,
    )
    db.add(row)
    await db.commit()
