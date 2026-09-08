"""Small, explicit settings model.

The runtime will load environment values in a later construction pass.  This
module intentionally contains validation and defaults only; it has no import-
time network or database side effects.
"""

from enum import Enum

from pydantic import BaseModel, Field, SecretStr


class ExecutionMode(str, Enum):
    IN_PROCESS = "in_process"
    HTTP = "http"
    CLOUD = "cloud"


class Settings(BaseModel):
    """Validated values shared by local, Compose, Kubernetes, and cloud modes."""

    app_env: str = "development"
    execution_mode: ExecutionMode = ExecutionMode.IN_PROCESS
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://opspilot:opspilot@localhost:5432/opspilot"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = Field(default=None, repr=False)
    llm_model: str = "provider-default"
    embedding_model: str = "provider-default"
    active_analysis_slots: int = Field(default=1, ge=1, le=1)
    queue_capacity: int = Field(default=1, ge=0, le=3)

