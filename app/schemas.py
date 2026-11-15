from typing import Literal

from pydantic import BaseModel, Field


class Character(BaseModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)


class Mention(BaseModel):
    id: str
    sentence_id: int
    text: str
    character_id: str | None = None
    candidates: list[str] = Field(default_factory=list)
    reason: str = ''


class Evidence(BaseModel):
    sentence_id: int
    quote: str


RelationKind = Literal['conversation', 'conflict', 'support', 'family', 'transfer', 'other']


class Relation(BaseModel):
    id: str
    source_id: str
    target_id: str
    kind: RelationKind
    label: str = Field(min_length=1, max_length=120)
    evidence: list[Evidence] = Field(min_length=1)


class Extraction(BaseModel):
    characters: list[Character]
    mentions: list[Mention]
    relations: list[Relation]


class Resolution(BaseModel):
    mention_id: str
    character_id: str | None
    reason: str = ''


class Review(BaseModel):
    resolutions: list[Resolution] = Field(default_factory=list)
    discard_relation_ids: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AgentEvent(BaseModel):
    step: str
    detail: str
    kind: Literal['model', 'tool', 'validation', 'info'] = 'info'


class AnalysisResult(BaseModel):
    sentences: list[str]
    characters: list[Character]
    mentions: list[Mention]
    relations: list[Relation]
    agent_events: list[AgentEvent]
    warnings: list[str]
    metadata: dict[str, int | float | str]
