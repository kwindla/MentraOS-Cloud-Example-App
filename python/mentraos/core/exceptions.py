"""Custom exceptions for MentraOS SDK."""


class MentraOSError(Exception):
    """Base exception for all MentraOS SDK errors."""
    pass


class ConnectionError(MentraOSError):
    """Raised when WebSocket connection fails."""
    pass


class AuthenticationError(MentraOSError):
    """Raised when authentication fails."""
    pass


class SessionError(MentraOSError):
    """Raised when session-related operations fail."""
    pass


class MessageError(MentraOSError):
    """Raised when message handling fails."""
    pass


class ConfigurationError(MentraOSError):
    """Raised when configuration is invalid."""
    pass