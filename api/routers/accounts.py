import uuid
from typing import Iterable

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..dependencies import current_user
from ..models.account import Account
from ..models.credit_card_terms import CreditCardTerms
from ..models.user import User
from ..schemas.account import (
    VALID_ACCOUNT_TYPES,
    AccountCreate,
    AccountDeleteImpactResponse,
    AccountHardDeleteResponse,
    AccountResponse,
    AccountUpdate,
    AnchorRequest,
    AnchorResponse,
)
from ..schemas.card_terms import (
    CardAnalysisResponse,
    CardTermsCreate,
    CardTermsExtraction,
    CardTermsResponse,
)
from ..services.accounts import (
    compute_account_balances,
    compute_delete_impact,
    hard_delete_account,
)
from ..services.anchors import (
    apply_anchor,
    latest_anchor_sources,
    needs_balance_confirmation,
)
from ..services.llm_extractor import extract_card_terms

from bot.app import get_llm_client

router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])

_PDF_MIME_TYPE = "application/pdf"
_MAX_PDF_BYTES = 4 * 1024 * 1024  # 4 MB pre-base64, same cap as debts


async def _accounts_with_balances(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    accounts: Iterable[Account],
) -> list[AccountResponse]:
    accounts_list = list(accounts)
    ids = [acc.id for acc in accounts_list]
    balances = await compute_account_balances(
        db, user_id=user_id, account_ids=ids
    )
    sources = await latest_anchor_sources(db, account_ids=ids)
    responses: list[AccountResponse] = []
    for acc in accounts_list:
        balance = balances.get(acc.id)
        response = AccountResponse.model_validate(acc)
        update: dict = {
            "needs_balance_confirmation": needs_balance_confirmation(
                sources.get(acc.id)
            ),
        }
        if balance is not None:
            update["current_balance"] = balance.current
            update["month_start_balance"] = balance.month_start
        responses.append(response.model_copy(update=update))
    return responses


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    if payload.account_type not in VALID_ACCOUNT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"account_type must be one of: {', '.join(sorted(VALID_ACCOUNT_TYPES))}",
        )

    account = Account(
        user_id=user.id,
        name=payload.name,
        account_type=payload.account_type,
        currency=payload.currency,
        initial_balance=payload.initial_balance,
    )
    db.add(account)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Ya tenés una cuenta activa con ese nombre.",
        )
    await db.refresh(account)
    # NOTE: a brand-new account is intentionally left ANCHORLESS — it
    # reconstructs from `initial_balance` (so same-day captures count normally)
    # and is not nudged (entering the balance at creation IS the confirmation).
    # The anchor benefit (partial-import immunity) is available on demand via
    # POST /{id}/anchor. (Pending operator decision on create-time anchoring.)
    [response] = await _accounts_with_balances(
        db, user_id=user.id, accounts=[account]
    )
    return response


@router.get("", response_model=list[AccountResponse])
async def list_accounts(
    include_inactive: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    stmt = select(Account).where(Account.user_id == user.id)
    if not include_inactive:
        stmt = stmt.where(
            Account.is_active == True,  # noqa: E712
            Account.archived.is_(False),
        )
    stmt = stmt.order_by(Account.created_at.desc())

    result = await db.execute(stmt)
    accounts = list(result.scalars().all())
    return await _accounts_with_balances(db, user_id=user.id, accounts=accounts)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.user_id == user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    [response] = await _accounts_with_balances(
        db, user_id=user.id, accounts=[account]
    )
    return response


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.user_id == user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")

    update_data = payload.model_dump(exclude_unset=True)
    if "account_type" in update_data:
        if update_data["account_type"] not in VALID_ACCOUNT_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    "account_type must be one of: "
                    f"{', '.join(sorted(VALID_ACCOUNT_TYPES))}"
                ),
            )

    for field, value in update_data.items():
        setattr(account, field, value)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Ya tenés una cuenta activa con ese nombre.",
        )
    await db.refresh(account)
    [response] = await _accounts_with_balances(
        db, user_id=user.id, accounts=[account]
    )
    return response


@router.post("/{account_id}/anchor", response_model=AnchorResponse)
async def set_account_anchor(
    account_id: uuid.UUID,
    payload: AnchorRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Set/correct an account's REAL balance (bank reconciliation).

    Appends a 'reanchor' anchor and records the labeled, informational ajuste
    for the delta (excluded from the balance + income/expense; visible in
    Movimientos). Serves both the "confirmá tu saldo" nudge (migrated accounts)
    and AccountDetail's "corregí mi saldo". Balances are projected forward, so
    the displayed balance becomes the stated value in one step.
    """
    result = await db.execute(
        select(Account).where(
            Account.id == account_id, Account.user_id == user.id
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    if account.archived:
        raise HTTPException(
            status_code=400,
            detail="No se puede reanclar una cuenta archivada.",
        )

    res = await apply_anchor(
        db,
        user=user,
        account=account,
        value=payload.value,
        source="reanchor",
        note=payload.note,
        write_ajuste=True,
    )
    await db.commit()
    return AnchorResponse(
        account_id=account.id,
        value=res.value,
        effective_date=res.effective_date,
        delta=res.delta,
        ajuste_transaction_id=res.ajuste_transaction_id,
        current_balance=res.value,
    )


@router.get(
    "/{account_id}/delete-impact", response_model=AccountDeleteImpactResponse
)
async def account_delete_impact(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 7b B2 — read-only preview of what a hard delete would remove /
    detach, shown in the app's confirm sheet (auditability rule)."""
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.user_id == user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    impact = await compute_delete_impact(
        db, user_id=user.id, account_id=account.id
    )
    return AccountDeleteImpactResponse(**impact.__dict__)


@router.delete("/{account_id}")
async def delete_account(
    account_id: uuid.UUID,
    hard: bool = Query(
        default=False,
        description=(
            "When true, PERMANENTLY delete the account and ALL its "
            "transactions (requires confirm=<exact account name>). Linked "
            "debts/bills/goals are detached, never deleted. Default false = "
            "soft archive."
        ),
    ),
    confirm: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.user_id == user.id,
        )
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")

    if hard:
        # Typed-name confirmation: deleting transactions is irreversible.
        if confirm != account.name:
            raise HTTPException(
                status_code=400,
                detail=(
                    "El nombre no coincide. Escribí el nombre exacto de la "
                    "cuenta para confirmar."
                ),
            )
        snapshot = AccountResponse.model_validate(account)
        impact = await hard_delete_account(db, user_id=user.id, account=account)
        await db.commit()
        return AccountHardDeleteResponse(
            account=snapshot,
            impact=AccountDeleteImpactResponse(**impact.__dict__),
        )

    account.is_active = False
    account.archived = True
    await db.commit()
    await db.refresh(account)
    [response] = await _accounts_with_balances(
        db, user_id=user.id, accounts=[account]
    )
    return response


# ── Phase 7b B3: credit-card terms (account parameters, NOT a Debt) ───────────


async def _get_credit_account(
    db: AsyncSession, *, user_id: uuid.UUID, account_id: uuid.UUID
) -> Account:
    account = (
        await db.execute(
            select(Account).where(
                Account.id == account_id, Account.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    if account.account_type != "credit":
        raise HTTPException(
            status_code=400, detail="Esta cuenta no es de crédito."
        )
    return account


async def _get_terms(
    db: AsyncSession, *, user_id: uuid.UUID, account_id: uuid.UUID
) -> CreditCardTerms | None:
    return (
        await db.execute(
            select(CreditCardTerms).where(
                CreditCardTerms.account_id == account_id,
                CreditCardTerms.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


@router.post("/parse-card-document", response_model=CardTermsExtraction)
async def parse_card_document(
    file: UploadFile = File(..., description="Card contract or statement (PDF)"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
) -> CardTermsExtraction:
    """Phase 7b B3 — read card terms from an uploaded contract (or statement)
    PDF to PRE-FILL the native form. Does NOT create anything (mirror of
    /debts/parse-document)."""
    media_type = file.content_type or ""
    if media_type != _PDF_MIME_TYPE:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported document type '{media_type}'. Only PDF is "
                "accepted."
            ),
        )

    pdf_bytes = await file.read()
    if len(pdf_bytes) > _MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds {_MAX_PDF_BYTES // (1024 * 1024)} MB limit.",
        )

    return await extract_card_terms(
        user=user,
        pdf_bytes=pdf_bytes,
        client=get_llm_client(),
        haiku_model=settings.llm_extraction_model,
        sonnet_model=settings.llm_query_model,
        db=db,
    )


@router.get("/{account_id}/card-terms", response_model=CardTermsResponse)
async def get_card_terms(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    await _get_credit_account(db, user_id=user.id, account_id=account_id)
    terms = await _get_terms(db, user_id=user.id, account_id=account_id)
    if terms is None:
        raise HTTPException(
            status_code=404,
            detail="Esta tarjeta todavía no tiene términos registrados.",
        )
    return terms


@router.put("/{account_id}/card-terms", response_model=CardTermsResponse)
async def upsert_card_terms(
    account_id: uuid.UUID,
    payload: CardTermsCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Create or replace the card's terms (1:1 with the account). The PUT is
    the only write path — the PDF extraction only pre-fills the form."""
    await _get_credit_account(db, user_id=user.id, account_id=account_id)
    if payload.envelope_id is not None:
        from ..services.envelopes import is_valid_envelope_target

        if not await is_valid_envelope_target(
            db, user_id=user.id, envelope_id=payload.envelope_id
        ):
            raise HTTPException(status_code=400, detail="Sobre inválido.")
    terms = await _get_terms(db, user_id=user.id, account_id=account_id)
    if terms is None:
        terms = CreditCardTerms(
            user_id=user.id,
            account_id=account_id,
            **payload.model_dump(),
        )
        db.add(terms)
    else:
        # exclude_unset: a PUT that omits envelope_id must NOT silently
        # detach an attached envelope — only an explicit null clears it.
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(terms, field, value)
    await db.commit()
    await db.refresh(terms)
    return terms


@router.delete("/{account_id}/card-terms", status_code=204)
async def delete_card_terms(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    await _get_credit_account(db, user_id=user.id, account_id=account_id)
    terms = await _get_terms(db, user_id=user.id, account_id=account_id)
    if terms is None:
        raise HTTPException(
            status_code=404,
            detail="Esta tarjeta todavía no tiene términos registrados.",
        )
    await db.delete(terms)
    await db.commit()


@router.get("/{account_id}/card-analysis", response_model=CardAnalysisResponse)
async def card_analysis(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(current_user),
):
    """Phase 7b B4 — deterministic minimum-payment analysis over the LIVE
    balance (app/domain/credit engine). The LLM never calculates this."""
    from ..services.credit_cards import build_card_analysis, list_active_cards_with_terms

    await _get_credit_account(db, user_id=user.id, account_id=account_id)
    cards = await list_active_cards_with_terms(db, user_id=user.id)
    card = next((c for c in cards if c.account.id == account_id), None)
    if card is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Esta tarjeta no tiene términos registrados. Agregá la tasa y "
                "el pago mínimo primero."
            ),
        )
    return build_card_analysis(card=card)
