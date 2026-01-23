class NebiusAccError(Exception):
    """Base error for nebius-acc-management."""


class NebiusSdkError(NebiusAccError):
    """Raised when Nebius SDK operations fail."""

    def __init__(self, message: str, details: str | None = None) -> None:
        super().__init__(message)
        self.details = details or ""


class ConfigError(NebiusAccError):
    """Raised when configuration or input validation fails."""
