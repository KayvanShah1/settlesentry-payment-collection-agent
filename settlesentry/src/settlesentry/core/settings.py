from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def find_project_root(
    markers: tuple[str, ...] = (".git", ".env"),
) -> Path:
    """
    Search upwards from the current file's directory to find the project root.

    This avoids fragile Path.parents[n] assumptions when files are moved.
    """
    current = Path(__file__).resolve().parent

    for parent in [current] + list(current.parents):
        if any((parent / marker).exists() for marker in markers):
            return parent

    return current.parent


PROJECT_ROOT = find_project_root()


class BaseProjectSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def model_dump(self, **kwargs):
        """Custom dump to make absolute paths relative for clean logging."""
        dump = super().model_dump(**kwargs)
        project_root = getattr(self, "project_root", PROJECT_ROOT)
        for key, value in dump.items():
            if isinstance(value, Path) and value.is_absolute():
                try:
                    dump[key] = str(value.relative_to(project_root))
                except ValueError:
                    dump[key] = str(value)
        return dump


class LoggingConfig(BaseProjectSettings):
    """
    Logging configuration.

    Keep DEBUG useful locally, but avoid logging sensitive values.
    Redaction happens inside logger.py.
    """

    level: str = Field(default="INFO")
    file_enabled: bool = Field(default=True)
    console_enabled: bool = Field(default=True)
    max_bytes: int = Field(default=2000 * 1024)
    backup_count: int = Field(default=5)

    model_config = SettingsConfigDict(env_prefix="LOG_")


class APIConfig(BaseProjectSettings):
    """External API configuration for the Prodigal payment verification API."""

    base_url: str = Field(
        default="https://se-payment-verification-api.service.external.usea2.aws.prodigaltech.com/openapi",
        description="Base URL for the Prodigal payment verification API",
    )
    timeout_seconds: int = Field(default=30, ge=1, le=120, description="Timeout for API requests in seconds")
    max_retries: int = Field(default=2, ge=0, le=5, description="Maximum number of retries for API requests")

    model_config = SettingsConfigDict(env_prefix="API_")  # Prefix for env vars


class LLMConfig(BaseProjectSettings):
    """
    Optional OpenRouter configuration.

    Keep disabled by default so the assignment remains deterministic and runnable
    without external LLM setup.
    """

    enabled: bool = Field(default=False)
    api_key: SecretStr | None = Field(default=None)
    base_url: str = Field(default="https://openrouter.ai/api/v1")
    model: str = Field(default="openrouter/free")
    timeout_seconds: int = Field(default=12)
    temperature: float = Field(default=0.0)
    max_tokens: int = Field(default=500)

    model_config = SettingsConfigDict(env_prefix="OPENROUTER_")


class AgentPolicyConfig(BaseProjectSettings):
    """
    Operational policy for the payment collection agent.
    """

    verification_max_attempts: int = Field(default=3, ge=1, le=5)
    payment_max_attempts: int = Field(default=3, ge=1, le=5)

    allow_partial_payments: bool = Field(default=True)
    allow_zero_balance_payment: bool = Field(default=False)

    max_payment_amount: float | None = Field(
        default=None,
        description="Optional production guardrail. None means use account balance as the cap.",
    )

    model_config = SettingsConfigDict(env_prefix="AGENT_POLICY_")


class Settings(BaseProjectSettings):
    """
    Main Settings class. It coordinates the sub-configs and manages
    the physical directory structure.
    """

    project_root: Path = PROJECT_ROOT
    project_name: str = "settlesentry"

    # Paths derived from root
    var_dir: Path = Field(default=project_root / "var")
    log_dir: Path = Field(default=project_root / "var" / "logs")

    # Sub-configs
    api: APIConfig = Field(default_factory=APIConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    agent_policy: AgentPolicyConfig = Field(default_factory=AgentPolicyConfig)

    def model_post_init(self, __context):
        """Ensure directories exist on startup."""
        for directory in [self.var_dir, self.log_dir]:
            directory.mkdir(parents=True, exist_ok=True)


# Instantiate the singleton
settings = Settings()

if __name__ == "__main__":
    from rich.pretty import pretty_repr

    from settlesentry.core.logger import get_logger

    logger = get_logger("ApplicationSettings")
    logger.debug(f"Project Root Detected: {settings.project_root}")
    logger.debug("--- Loaded Settings ---")
    logger.debug(pretty_repr(settings.model_dump()))
