"""Pydantic request bodies for the HTTP API."""
from __future__ import annotations

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
