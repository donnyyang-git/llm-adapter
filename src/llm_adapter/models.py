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
ArtifactKind = Literal["gemini-ui", "code-block"]
ArtifactPreviewType = Literal["html", "code", "image", "none"]


class ResponseArtifact(BaseModel):
    artifact_id: str = Field(pattern=r"^artifact-[1-9][0-9]*$")
    title: str = Field(min_length=1)
    summary: str = ""
    kind: ArtifactKind = "code-block"
    language: str = ""
    code: str = ""
    preview_type: ArtifactPreviewType = "none"
    image_path: str = ""


class ConversationTurn(BaseModel):
    turn_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
    question: str = Field(min_length=1)
    response_text: str = ""
    response_markdown: str = ""
    # [修改] 2026-09-20 19:20 原因: 一則 Gemini 回覆可能包含多個獨立程式片段或 artifact。 說明: 以結構化陣列保存每個 artifact，供前端逐一渲染與切換。
    response_artifacts: list[ResponseArtifact] = Field(default_factory=list)
    # [修改] 2026-09-20 18:55 原因: Gemini 的 artifact 程式碼不能再從混合摘要文字硬拆，否則會遺失換行與後段 JS。 說明: 將完整 artifact code 獨立持久化，前端 code panel 直接使用這份原始內容。
    response_artifact_code: str = ""
    # [修改] 2026-09-20 18:05 原因: 前端需要把 Gemini 的視覺化回覆以附圖方式保留下來。 說明: 為每個 turn 增加可持久化的截圖路徑，供 API 與 UI 直接顯示。
    response_image_path: str = ""
    sent_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None
    status: TurnStatus = "pending"
    error: str | None = None

    @model_validator(mode="after")
    def validate_completion_time(self) -> "ConversationTurn":
        # [修改] 2026-09-20 19:20 原因: 舊 session 仍可能只保存單一 artifact code / image path。 說明: 載入時自動補成 artifact 陣列，並同步回 legacy 欄位，維持新舊資料相容。
        if not self.response_artifacts and (
            self.response_artifact_code or self.response_image_path
        ):
            preview_type: ArtifactPreviewType = "none"
            normalized_code = self.response_artifact_code.lstrip().lower()
            if normalized_code.startswith("<!doctype html>") or normalized_code.startswith(
                "<html"
            ):
                preview_type = "html"
            elif self.response_artifact_code:
                preview_type = "code"
            elif self.response_image_path:
                preview_type = "image"
            self.response_artifacts = [
                ResponseArtifact(
                    artifact_id="artifact-1",
                    title="Generated artifact",
                    kind="gemini-ui" if self.response_artifact_code else "code-block",
                    language="html" if preview_type == "html" else "",
                    code=self.response_artifact_code,
                    preview_type=preview_type,
                    image_path=self.response_image_path,
                )
            ]

        if self.response_artifacts:
            primary_artifact = self.response_artifacts[0]
            if not self.response_artifact_code and primary_artifact.code:
                self.response_artifact_code = primary_artifact.code
            if not self.response_image_path and primary_artifact.image_path:
                self.response_image_path = primary_artifact.image_path

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
