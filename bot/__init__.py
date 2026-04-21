"""Telegram bot for the personal finance agent (Phase 5b).

Pipeline (from the Phase 5b spec):

    Telegram update
      → resolve user (telegram_user_id → users.id)
      → load short conversation context from Redis (last 1–2 turns)
      → LLM extractor → ExtractionResult
      → deterministic Dispatcher → Action | Clarify | Reject
      → if Action: stage PendingAction in Redis, send confirm message
      → if Clarify: ask, await reply
      → if Reject: explain why

No branch of this pipeline calls the LLM to "decide what to do". The LLM's
only job is turning prose into structured data.
"""
