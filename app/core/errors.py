"""Centralised error handling for the API.

Exception handlers registered on the application:

* a handler for :class:`AppError` (our own, explicit application errors) that
  renders the error payload declared in the OpenAPI schema,
* a handler for request validation errors that returns the same envelope —
  **with the submitted values removed**, so passwords and tokens never travel
  back to the client or into logs, and
* a catch-all handler so unexpected failures return a consistent JSON body
  instead of a bare HTML traceback.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected, application-level errors."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "app_error"

    def __init__(
        self,
        message: str = "Unexpected application error",
        *,
        headers: dict[str, str] | None = None,
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.headers = headers


class NotFoundError(AppError):
    """Raised when a requested resource does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class UnauthorizedError(AppError):
    """Raised when credentials are missing, invalid, expired or revoked."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"

    def __init__(self, message: str = "Not authenticated.", **details: Any) -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"}, **details)


class ForbiddenError(AppError):
    """Raised when an authenticated user is not allowed to perform an action."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class BadRequestError(AppError):
    """Raised when a request is well-formed but the action cannot be performed."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "bad_request"


class ConflictError(AppError):
    """Raised when a resource already exists (for example a duplicate email)."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class PayloadTooLargeError(AppError):
    """Raised when an uploaded file exceeds the configured size limit."""

    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
    code = "payload_too_large"


class UnsupportedMediaTypeError(AppError):
    """Raised when an upload's extension, declared type or content is not allowed."""

    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_media_type"


class ServiceUnavailableError(AppError):
    """Raised when a required dependency cannot answer safely."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"


class RateLimitError(AppError):
    """Raised when a client exceeds an allowed request rate."""

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"

    def __init__(
        self,
        message: str = "Too many requests. Please try again later.",
        *,
        retry_after: int | None = None,
        **details: Any,
    ) -> None:
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        super().__init__(message, headers=headers, **details)


def _error_payload(code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    """Build the standard error envelope returned by the API."""
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details:
        payload["error"]["details"] = details
    return payload


def _sanitise_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop ``input``/``ctx`` from validation errors.

    Pydantic echoes the offending value in ``input``; for authentication
    payloads that would mean returning the password in the response body.
    """
    safe = []
    for error in errors:
        safe.append(
            {
                "type": error.get("type"),
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg"),
            }
        )
    return safe


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to ``app``."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(exc.code, exc.message, exc.details or None),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_payload(
                "validation_error",
                "Request validation failed.",
                details=_sanitise_validation_errors(exc.errors()),
            ),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception while processing request: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_payload("internal_error", "An unexpected error occurred."),
        )
