"""
AIDE -- Network error detection and fallback utilities
Distinguishes between network failures (fallback) and auth failures (no fallback).
"""
from loguru import logger


class NetworkError(Exception):
    """Raised when a network-level error occurs (connection, timeout, DNS)."""
    pass


class AuthenticationError(Exception):
    """Raised when API authentication fails (invalid key, 401, 403)."""
    pass


class RateLimitError(Exception):
    """Raised when rate limit is hit (429, quota exceeded)."""
    pass


def classify_http_error(status_code: int, error_msg: str = "") -> type[Exception]:
    """
    Classify HTTP errors to determine if fallback should occur.
    
    NetworkError triggers → fallback to Ollama
    AuthenticationError → don't retry, just fail
    RateLimitError → queue for later
    """
    if status_code in (401, 403):
        return AuthenticationError
    if status_code == 429:
        return RateLimitError
    if status_code >= 500:  # Server errors often mean network/service issues
        return NetworkError
    if status_code >= 400:
        return AuthenticationError  # Other 4xx are client errors
    return NetworkError


def classify_exception(error: Exception) -> type[Exception]:
    """
    Classify exceptions to determine if fallback should occur.
    """
    error_str = str(error).lower()
    
    # Network-level errors → fallback
    network_indicators = [
        "connection refused",
        "connection reset",
        "connection aborted",
        "timeout",
        "timed out",
        "dns",
        "name resolution",
        "no route to host",
        "network unreachable",
        "connection error",
        "ssl",
        "certificate",
        "handshake",
        "remote end closed",
        "broken pipe",
        "operation timed out",
    ]
    if any(indicator in error_str for indicator in network_indicators):
        return NetworkError
    
    # Auth errors → don't fallback
    auth_indicators = [
        "401", "403", "unauthorized", "forbidden", "invalid api key",
        "authentication failed", "access denied", "invalid token",
    ]
    if any(indicator in error_str for indicator in auth_indicators):
        return AuthenticationError
    
    # Rate limiting → queue for retry
    if "429" in error_str or "rate limit" in error_str or "quota" in error_str:
        return RateLimitError
    
    # httpx-specific exceptions (check safely to avoid AttributeError)
    error_type_name = type(error).__name__
    httpx_network_errors = [
        "ConnectError",
        "TimeoutException",
        "ProxyError",
        "SSLError",
        "LocalProtocolError",
        "RemoteProtocolError",
        "PoolTimeout",
    ]
    if error_type_name in httpx_network_errors:
        return NetworkError
    
    # Default to network error to be safe (fallback available)
    return NetworkError


def is_network_error(error: Exception) -> bool:
    """Check if an error is network-related (should trigger fallback)."""
    return classify_exception(error) == NetworkError


def is_auth_error(error: Exception) -> bool:
    """Check if an error is authentication-related (don't fallback)."""
    return classify_exception(error) == AuthenticationError


def is_rate_limit_error(error: Exception) -> bool:
    """Check if an error is rate limiting (queue for retry)."""
    return classify_exception(error) == RateLimitError
