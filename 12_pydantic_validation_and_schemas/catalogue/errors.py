"""
Day 12 — One error shape, for every failure.
============================================

Why this module exists
----------------------
The fastest way to make an API miserable to consume is to return errors in
several different shapes::

    {"error": "not found"}                       # from one endpoint
    {"message": "Bad Request", "code": 400}      # from another
    <!doctype html><title>404 Not Found</title>  # from Flask's default

A client then needs three parsers and still cannot reliably show a message. Pick
**one envelope** and return it for every failure, from every endpoint, forever.

The envelope used here
----------------------
.. code-block:: json

    {
      "error": {
        "status": 422,
        "code": "validation_error",
        "message": "The request body failed validation.",
        "details": {"title": "This field is required."}
      }
    }

- ``status`` mirrors the HTTP status — convenient when a client has already
  discarded the response object.
- ``code`` is a **stable, machine-readable string**. Clients branch on this.
  Never make them match on ``message``: prose gets reworded and translated.
- ``message`` is for humans, and safe to display.
- ``details`` is optional, and structured — field-level errors go here.
"""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from werkzeug.wrappers import Response


class APIError(Exception):
    """An application error that should become a JSON response.

    Raising a typed exception beats returning ``(jsonify(...), 400)`` from deep
    inside a helper: the helper does not need to know it is in a web request,
    and the shape is applied in exactly one place.

    Attributes:
        status: HTTP status code.
        code: Stable machine-readable identifier.
        message: Human-readable explanation.
        details: Optional structured extra information.

    Example:
        >>> raise APIError(409, "isbn_conflict", "That ISBN already exists.")
        Traceback (most recent call last):
        APIError: ...
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            status: HTTP status code to return.
            code: Stable machine-readable identifier, e.g. ``"not_found"``.
            message: Human-readable explanation.
            details: Optional structured detail, e.g. per-field messages.
        """
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}

    def to_response(self) -> tuple[Response, int]:
        """Render this error in the standard envelope.

        Returns:
            tuple[Response, int]: JSON body and status code.
        """
        payload: dict[str, Any] = {
            "error": {
                "status": self.status,
                "code": self.code,
                "message": self.message,
            }
        }
        if self.details:
            payload["error"]["details"] = self.details
        return jsonify(payload), self.status


# Map Werkzeug's built-in exceptions onto our stable `code` strings, so that a
# 404 raised by Flask's router looks identical to one we raise ourselves.
_CODE_BY_STATUS: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
}


def register_error_handlers(app: Flask) -> None:
    """Install handlers so every failure returns the same envelope.

    Args:
        app: The application to register handlers on.

    Note:
        Three handlers cover everything:

        1. :class:`APIError` — errors we raise deliberately.
        2. :class:`~werkzeug.exceptions.HTTPException` — 404 from the router,
           405 from method mismatch, 413 from the body-size limit, and so on.
        3. :class:`Exception` — the catch-all, so an unexpected crash still
           produces JSON instead of an HTML debug page.
    """

    @app.errorhandler(APIError)
    def handle_api_error(error: APIError) -> tuple[Response, int]:
        """Render a deliberately raised :class:`APIError`.

        Args:
            error: The raised error.

        Returns:
            tuple[Response, int]: JSON body and status code.
        """
        return error.to_response()

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException) -> tuple[Response, int]:
        """Convert any Werkzeug HTTP exception into the standard envelope.

        Args:
            error: The raised HTTP exception.

        Returns:
            tuple[Response, int]: JSON body and status code.
        """
        status = error.code or 500
        return APIError(
            status=status,
            code=_CODE_BY_STATUS.get(status, "http_error"),
            message=error.description or error.name,
        ).to_response()

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception) -> tuple[Response, int]:
        """Catch anything unhandled and still answer with JSON.

        Args:
            error: The unhandled exception.

        Returns:
            tuple[Response, int]: A generic 500 in the standard envelope.

        Warning:
            **Never** put ``str(error)`` in the response. Exception text leaks
            file paths, SQL fragments, and internal hostnames. Log the detail
            (Day 18) and tell the client only that something failed.
        """
        app.logger.exception("Unhandled exception: %s", error)
        return APIError(
            500, "internal_error", "An unexpected error occurred."
        ).to_response()
