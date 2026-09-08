'''Centralized, side-effect-free configuration.

Both the repository-prefixed names and the short names from the product brief
are accepted. Prefixed values win, which keeps Compose and Cloud Run explicit.
'''

from enum import Enum
import os

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ExecutionMode(str, Enum):
    IN_PROCESS = 'in_process'
    HTTP = 'http'
    CLOUD = 'cloud'


def _env(name: str, default: str | None = None) -> str | None:
    return os.getenv(f'OPSPILOT_{name}', os.getenv(name, default))


class Settings(BaseModel):
    '''Validated settings shared by local, Compose, Kubernetes, and cloud.'''

    model_config = ConfigDict(extra='forbid')

    app_env: str = Field(default_factory=lambda: _env('APP_ENV', 'development') or 'development')
    execution_mode: ExecutionMode = Field(
        default_factory=lambda: _env('EXECUTION_MODE', 'in_process') or 'in_process'
    )
    log_level: str = Field(default_factory=lambda: _env('LOG_LEVEL', 'INFO') or 'INFO')
    database_url: str = Field(
        default_factory=lambda: _env(
            'DATABASE_URL',
            'memory://opspilot',
        )
        or 'memory://opspilot'
    )
    llm_base_url: str | None = Field(default_factory=lambda: _env('LLM_BASE_URL'))
    llm_api_key: SecretStr | None = Field(default=None, repr=False)
    llm_model: str = Field(
        default_factory=lambda: _env('LLM_MODEL', 'provider-default') or 'provider-default'
    )
    embedding_model: str = Field(
        default_factory=lambda: _env('EMBEDDING_MODEL', 'hash-v1') or 'hash-v1'
    )
    mcp_server_url: str | None = Field(default_factory=lambda: _env('MCP_SERVER_URL'))
    mlflow_tracking_uri: str | None = Field(default_factory=lambda: _env('MLFLOW_TRACKING_URI'))
    lora_adapter_path: str | None = Field(default_factory=lambda: _env('LORA_ADAPTER_PATH'))
    active_analysis_slots: int = Field(
        default_factory=lambda: int(_env('ACTIVE_ANALYSIS_SLOTS', '1') or '1'),
        ge=1,
        le=1,
    )
    queue_capacity: int = Field(
        default_factory=lambda: int(_env('QUEUE_CAPACITY', '1') or '1'),
        ge=0,
        le=3,
    )
    port: int = Field(default_factory=lambda: int(_env('PORT', '8080') or '8080'), ge=1, le=65535)
    provider_timeout_seconds: float = Field(
        default_factory=lambda: float(_env('PROVIDER_TIMEOUT_SECONDS', '20') or '20'),
        gt=0,
        le=120,
    )

    @classmethod
    def from_env(cls) -> 'Settings':
        values = cls().model_dump()
        api_key = _env('LLM_API_KEY')
        values['llm_api_key'] = SecretStr(api_key) if api_key else None
        return cls.model_validate(values)

    @field_validator('log_level')
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        value = value.upper().strip()
        allowed = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
        if value not in allowed:
            raise ValueError('LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL')
        return value

    def configuration_errors(self) -> list[str]:
        '''Return safe, actionable errors for settings required by the mode.'''

        errors: list[str] = []
        if self.execution_mode is ExecutionMode.CLOUD and self.database_url.startswith('memory://'):
            errors.append('DATABASE_URL must point to external PostgreSQL in cloud mode')
        if self.execution_mode is ExecutionMode.CLOUD and not self.llm_api_key:
            errors.append('LLM_API_KEY is required in cloud mode')
        return errors
