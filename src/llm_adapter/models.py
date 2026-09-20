from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChromeStatus(BaseModel):
    state: Literal["stopped", "starting", "connected", "error"]
    connected: bool
    message: str | None = None


class TabInfo(BaseModel):
    id: str
    title: str
    url: str
    is_gemini: bool
    is_selected: bool


class SelectTabRequest(BaseModel):
    tab_id: str


class SendMessageRequest(BaseModel):
    question: str = Field(min_length=1, max_length=100_000)

    @model_validator(mode="after")
    def reject_blank_question(self) -> "SendMessageRequest":
        if not self.question.strip():
            raise ValueError("Question must not be blank.")
        return self


TurnStatus = Literal["pending", "generating", "completed", "partial", "failed"]


class ConversationTurn(BaseModel):
    turn_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    question: str = Field(min_length=1)
    response_text: str = ""
    response_markdown: str = ""
    sent_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    status: TurnStatus = "pending"
    error: str | None = None

    @model_validator(mode="after")
    def validate_completion_time(self) -> "ConversationTurn":
        is_finished = self.status in {"completed", "partial", "failed"}
        if is_finished != (self.completed_at is not None):
            raise ValueError("Finished turns must have completed_at, and active turns must not.")
        return self


class ConversationSession(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    title: str = Field(min_length=1)
    conversation_url: str
    tab_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    turns: list[ConversationTurn] = Field(default_factory=list)
