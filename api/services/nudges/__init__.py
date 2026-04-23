"""Phase 5d — engagement nudges.

Layout mirrors api/services/llm_extractor/:
    policy.py           — anti-saturation constants + quiet-hours helpers
    evaluators/         — three concrete evaluators + registry
    orchestrator.py     — runs evaluators, dedups, filters silenced (bloque 6)
    delivery.py         — rate limit + quiet hours + LLM phrasing + channel send (bloque 7)
    phrasing.py         — prompt builders per nudge_type (bloque 7)

Rule: the LLM phrases, the evaluators + policy decide. Anything calling the
LLM to choose "whether to nudge" is a bug.
"""
