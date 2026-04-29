# Phase 6a Broken Test Inventory

Comando usado para inventario principal:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest -p no:cacheprovider tests/test_llm_extractor.py tests/test_telegram_dispatcher.py
```

Resultado: 27 fallos esperados despues del cambio de schema/routing.

Nota: tambien se intento correr la suite completa con `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -p no:cacheprovider --tb=short`, pero quedo bloqueada en tests con DB de nudges. Se termino el proceso y no se usa ese run para este inventario.

## tests/test_llm_extractor.py

- `test_basic_crc_expense_shape`: fixture `BASIC_EXPENSE_CRC` no incluye el nuevo campo requerido `dispatcher`.
- `test_slang_amount_no_currency_leaves_currency_null`: fixture `SLANG_AMOUNT_NO_CURRENCY` no incluye el nuevo campo requerido `dispatcher`.
- `test_usd_expense_normalizes_currency_uppercase`: fixture `USD_EXPENSE` no incluye el nuevo campo requerido `dispatcher`.
- `test_relative_date_passes_through_unresolved`: fixture `EXPENSE_YESTERDAY` no incluye el nuevo campo requerido `dispatcher`.
- `test_weekly_balance_query_window`: fixture `WEEKLY_BALANCE_QUERY` usa el intent eliminado `query_balance` y no incluye `dispatcher`.
- `test_low_confidence_ambiguous_preserved_for_dispatcher`: fixture `LOW_CONFIDENCE_AMBIGUOUS` no incluye el nuevo campo requerido `dispatcher`.

## tests/test_telegram_dispatcher.py

- `test_confirm_yes_short_circuits`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_confirm_no_short_circuits`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_undo_short_circuits`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_unknown_intent_routes_to_help`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_help_intent_routes_to_help`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_low_confidence_log_expense_clarifies`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_log_expense_missing_amount_clarifies`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_log_expense_single_account_auto_selects`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_log_expense_multiple_accounts_no_hint_clarifies`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_log_expense_multiple_accounts_ambiguous_hint_clarifies`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_log_expense_multiple_accounts_hint_resolves`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_log_expense_null_currency_defaults_to_user_currency`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_log_income_yields_positive_amount`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_log_expense_yields_negative_amount`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_log_expense_yesterday_resolves`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_log_expense_unknown_hint_falls_back_to_today`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_query_balance_resolves_window`: referencia el intent eliminado `Intent.QUERY_BALANCE` y espera el dispatcher viejo de queries.
- `test_query_recent_uses_default_window`: referencia el intent eliminado `Intent.QUERY_RECENT` y espera el dispatcher viejo de queries.
- `test_account_clarification_round_trip_merges_and_proposes`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_amount_clarification_round_trip_parses_cr_format`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
- `test_clarification_merge_rejects_gibberish_amount`: helper `_extraction()` construye `ExtractionResult` sin `dispatcher`.
