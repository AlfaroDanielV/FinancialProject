"""Phase 6d B6 — debt onboarding contract.

The native app uses the `/schedule` alias from the decisions doc, while the
legacy API keeps `/amortization` for existing callers.

Phase 6f B16: the amortization parity guard cross-checks the lifted
`mobile/src/lib/amortization.ts` (the live client lib after the SPA retired)
against the backend formula.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from api.database import get_db
from api.dependencies import current_user
from api.main import app
from api.services.amortization import compute_french_payment, generate_schedule


ROOT = Path(__file__).resolve().parent.parent
MOBILE_DIR = ROOT / "mobile"
_MOBILE_TSC = MOBILE_DIR / "node_modules/.bin/tsc"


BANK_FIXTURES = [
    {
        "case_id": "bac_public_calculator_capital_interest_only",
        "principal": 1_000_000.0,
        "annual_rate": 0.12,
        "term_months": 12,
        "due_day": 15,
        "expected_payment": 88_848.79,
        "expected_total_interest": 66_185.45,
    },
    {
        "case_id": "bac_mortgage_cuota_nivelada_crc",
        "principal": 50_000_000.0,
        "annual_rate": 0.0825,
        "term_months": 240,
        "due_day": 15,
        "expected_payment": 426_032.83,
        "expected_total_interest": 52_247_876.88,
    },
    {
        "case_id": "promerica_prendario_cuota_style_crc",
        "principal": 8_000_000.0,
        "annual_rate": 0.095,
        "term_months": 60,
        "due_day": 28,
        "expected_payment": 168_014.89,
        "expected_total_interest": 2_080_893.40,
    },
]


def _override(session, user_id):
    class _StubUser:
        def __init__(self) -> None:
            self.id = user_id
            self.status = "active"

    async def _yield_session():
        yield session

    app.dependency_overrides[current_user] = lambda: _StubUser()
    app.dependency_overrides[get_db] = _yield_session


def _clear():
    app.dependency_overrides.pop(current_user, None)
    app.dependency_overrides.pop(get_db, None)


def _payload(debt_type: str = "mortgage"):
    return {
        "name": "Hipoteca B6",
        "debt_type": debt_type,
        "lender": "BAC",
        "original_amount": 1_000_000,
        "current_balance": 1_000_000,
        "interest_rate": 0.12,
        "minimum_payment": 88_848.79,
        "payment_due_day": 15,
        "term_months": 12,
        "start_date": "2026-05-11",
        "currency": "CRC",
        "rate_type": "fixed",
        "prepayment_penalty_pct": 0.03,
        "payments_made": 0,
        "includes_insurance": False,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("debt_type", ["mortgage", "personal_loan", "credit_card_balance"])
async def test_create_debt_accepts_b6_required_types(db_with_user, debt_type):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/debts",
                json=_payload(debt_type=debt_type),
            )
    finally:
        _clear()

    assert resp.status_code == 201, resp.text
    body = resp.json()
    expected_type = "credit_card" if debt_type == "credit_card_balance" else debt_type
    assert body["debt_type"] == expected_type
    assert Decimal(str(body["current_balance"])) == Decimal("1000000.0")


@pytest.mark.asyncio
async def test_schedule_alias_returns_amortization_for_created_debt(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            created = await ac.post("/api/v1/debts", json=_payload())
            assert created.status_code == 201, created.text

            debt_id = created.json()["id"]
            schedule = await ac.get(f"/api/v1/debts/{debt_id}/schedule")
    finally:
        _clear()

    assert schedule.status_code == 200, schedule.text
    body = schedule.json()
    assert body["debt_name"] == "Hipoteca B6"
    assert body["monthly_payment"] == 88848.79
    assert body["total_months"] == 12
    assert len(body["schedule"]) == 12
    assert body["schedule"][0]["payment_date"] == "2026-06-15"
    assert body["schedule"][0]["interest_portion"] == 10000.0


@pytest.mark.asyncio
async def test_schedule_alias_matches_legacy_amortization_endpoint(db_with_user):
    session, user_id = db_with_user
    _override(session, user_id)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            created = await ac.post("/api/v1/debts", json=_payload())
            assert created.status_code == 201, created.text
            debt_id = created.json()["id"]

            schedule = await ac.get(f"/api/v1/debts/{debt_id}/schedule")
            amortization = await ac.get(f"/api/v1/debts/{debt_id}/amortization")
    finally:
        _clear()

    assert schedule.status_code == 200, schedule.text
    assert amortization.status_code == 200, amortization.text
    assert schedule.json() == amortization.json()


def test_backend_schedule_matches_bank_calculator_reference_fixtures():
    for fixture in BANK_FIXTURES:
        monthly_payment = compute_french_payment(
            fixture["principal"],
            fixture["annual_rate"] / 12,
            fixture["term_months"],
        )
        assert round(monthly_payment, 2) == fixture["expected_payment"], fixture["case_id"]

        schedule = generate_schedule(
            balance=fixture["principal"],
            annual_rate=fixture["annual_rate"],
            monthly_payment=fixture["expected_payment"],
            due_day=fixture["due_day"],
            start_date=date(2026, 5, 11),
        )
        assert schedule.total_months == fixture["term_months"], fixture["case_id"]
        assert schedule.total_interest == fixture["expected_total_interest"], fixture["case_id"]
        assert schedule.rows[-1].remaining_balance == 0, fixture["case_id"]


@pytest.mark.skipif(
    not _MOBILE_TSC.exists(),
    reason="mobile/node_modules not installed (run npm ci in mobile/)",
)
def test_mobile_payment_preview_matches_backend_formula():
    cases = [
        {
            "balance": fixture["principal"],
            "annualRate": fixture["annual_rate"],
            "termMonths": fixture["term_months"],
            "includesInsurance": False,
            "insuranceMonthly": 0,
        }
        for fixture in BANK_FIXTURES
    ]

    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [
                str(_MOBILE_TSC),
                "src/lib/amortization.ts",
                "--target",
                "ES2022",
                "--module",
                "ES2022",
                "--moduleResolution",
                "bundler",
                "--outDir",
                tmp,
                "--skipLibCheck",
            ],
            cwd=MOBILE_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
        compiled = Path(tmp) / "amortization.js"
        compiled_mjs = Path(tmp) / "amortization.mjs"
        compiled.rename(compiled_mjs)
        node_code = """
const modulePath = process.argv[1];
const cases = JSON.parse(process.argv[2]);
const mod = await import(modulePath);
const values = cases.map((input) =>
  Number(mod.computeMonthlyPaymentPreview(input).toFixed(2))
);
console.log(JSON.stringify(values));
"""
        node_result = subprocess.run(
            ["node", "--input-type=module", "-e", node_code, compiled_mjs.as_uri(), json.dumps(cases)],
            cwd=MOBILE_DIR,
            check=True,
            capture_output=True,
            text=True,
        )

    client_values = json.loads(node_result.stdout)
    backend_values = [
        round(
            compute_french_payment(
                fixture["principal"],
                fixture["annual_rate"] / 12,
                fixture["term_months"],
            ),
            2,
        )
        for fixture in BANK_FIXTURES
    ]
    assert client_values == backend_values
