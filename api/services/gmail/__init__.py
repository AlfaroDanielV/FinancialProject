"""Gmail OAuth + ingestion services (Phase 6b).

Module map:
    oauth.py           — Authorization Code flow + signed state JWT.
    whitelist.py       — Per-user sender whitelist CRUD.
    sample_analyzer.py — Optional Haiku-based sample classifier
                         (used by /agregar_muestra).
    scanner.py         — Gmail API client + per-user inbox scan.
    reconciler.py      — 5-outcome matcher: candidate → existing /
                         created / shadow / duplicate / skipped.
    backfill.py        — Async wrapper kicked from on_activate_callback;
                         calls scanner + notifier.
    notifier.py        — Telegram delivery: per-transaction / batched /
                         shadow accumulator + daily summary.

Daily worker at workers/gmail_daily.py composes these modules; the
notifier is the only one that talks to the bot.
"""
