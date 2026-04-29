import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class QueryToolUsage(BaseModel):
    name: str
    args_summary: dict[str, Any]
    duration_ms: int
    error: Optional[str] = None


class LLMQueryDispatchResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    message_hash: str
    total_iterations: int
    total_input_tokens: int
    total_output_tokens: int
    tools_used: list[QueryToolUsage]
    final_response_chars: Optional[int]
    error: Optional[str]
    duration_ms: Optional[int]
    created_at: datetime

    model_config = {"from_attributes": True}
