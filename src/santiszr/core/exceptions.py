class AppError(Exception):
    """Base exception for application-level errors."""


class ConfigurationError(AppError):
    """Raised when runtime configuration is invalid."""


class ExternalDependencyError(AppError):
    """Raised when an expected external dependency is unavailable."""


class WorkflowError(AppError):
    """Raised when workflow orchestration fails."""
