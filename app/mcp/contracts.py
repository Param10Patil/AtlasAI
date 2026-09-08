"""Focused inputs/outputs for the runbook and incident-history MCP tools."""

from pydantic import BaseModel, ConfigDict, Field

from app.remediation.contracts import SafeAction


class SearchRunbooksInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)


class GetIncidentHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str | None = Field(default=None, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=3, ge=1, le=5)


class ExecuteSafeActionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    action: SafeAction
    target: str = Field(min_length=1, max_length=120, pattern=r'^[a-zA-Z0-9._:/-]+$')


class VerifyHealthInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    target: str = Field(min_length=1, max_length=120, pattern=r'^[a-zA-Z0-9._:/-]+$')


class MCPResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=800)
