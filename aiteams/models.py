"""Pydantic request bodies for the HTTP API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RegisterBody(BaseModel):
    path: str


class ConversationBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class MessageBody(BaseModel):
    conversation: str
    text: str = Field(min_length=1)


class AnswerBody(BaseModel):
    answers: dict[str, str]


class PauseBody(BaseModel):
    paused: bool


class InternalMessageBody(BaseModel):
    conversation: str
    text: str = Field(min_length=1)
    type: str = "Message"
    mentions: list[str] = []


class PermissionBody(BaseModel):
    tool_name: str
    input: dict = {}
    tool_use_id: str | None = None


class NewTaskBody(BaseModel):
    text: str = Field(min_length=1)
    starter: str = "product"
    title: str | None = None


class FeaturePatchBody(BaseModel):
    phase: str | None = None
    owner: str | None = None
    title: str | None = None
    archived: bool | None = None
    lead: str | None = None  # F-22: lead | lead-1 | lead-2; null unassigns (Master only)


class ApprovalDecideBody(BaseModel):
    decision: str
    note: str | None = None
    set_phase: str | None = None


class CommitDecideBody(BaseModel):
    decision: str
    message: str | None = None
    note: str | None = None
    force: bool = False
    group: bool = False  # F-11: commit the request's whole commit group, all or nothing


class ResolveBody(BaseModel):
    message_id: int


class InternalFeatureBody(BaseModel):
    title: str = Field(min_length=1)
    phase: str = "intake"
    owner: str | None = None


class InternalFeaturePatchBody(BaseModel):
    phase: str | None = None
    owner: str | None = None
    title: str | None = None
    lead: str | None = None  # F-22: Manager only


class AskMasterBody(BaseModel):
    questions: Any = None  # shape validated in workflow.validate_card (400 with a sentence)
    feature: str | None = None
    intro: str = ""
    purpose: str = "ask"


class InternalApprovalBody(BaseModel):
    feature: str | None = None
    title: str = Field(min_length=1)
    summary: str = ""


class InternalCommitBody(BaseModel):
    feature: str | None = None
    files: list[str]
    message: str = Field(min_length=1)
    requires: list[int] = []  # F-11: open requests this one can't land without


class InternalWithdrawBody(BaseModel):
    kind: str
    id: int
    reason: str = ""  # an empty one is a 400 with a sentence (workflow.withdraw_request), not a 422


class InternalReviewBody(BaseModel):
    decision: str
    note: str | None = None


class ReactionBody(BaseModel):
    message_id: int
    emoji: str


class LockBody(BaseModel):
    path: str = Field(min_length=1)
    force: bool = False


class FreshStartBody(BaseModel):
    handoff: bool = True


class FeatureFreshStartBody(BaseModel):
    mode: str = "keep_docs"


class BrainstormBody(BaseModel):
    topic: str = ""  # empty -> 400 with a sentence in workflow.start_brainstorm


class InternalBrainstormBody(BaseModel):
    feature: str
    topic: str = ""


class HandoffBody(BaseModel):
    text: str = Field(min_length=1)


class StandupSaveBody(BaseModel):
    text: str = Field(min_length=1)


class InterruptBody(BaseModel):
    role: str
    reason: str = ""


class CheckpointBody(BaseModel):
    role: str
    reason: str = ""
    handoff: bool = True
