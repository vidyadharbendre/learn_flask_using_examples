"""
Day 18 — Logging that is actually useful at 3am.
================================================

``print()`` is not logging
--------------------------
``print`` has no level, no timestamp, no source, no structure, and cannot be
turned down in production or up during an incident. It also goes to stdout
regardless of whether anyone is reading.

What a log line must answer
---------------------------
=============  =============================================================
**When**       an ISO 8601 timestamp, in UTC
**How bad**    a level, so you can filter
**Where**      logger name, module, line
**What**       the message
**Which one**  a **request id**, so one user's journey can be reconstructed
=============  =============================================================

The last is the one people skip, and it is the one that matters most. Under
concurrency, interleaved lines from twenty simultaneous requests are unreadable
without a correlation id.

Levels, decided in advance
--------------------------
============  ==============================================================
``DEBUG``     detail for development; usually off in production
``INFO``      normal, notable events: request served, job completed
``WARNING``   something unexpected but handled: retry, slow query, fallback
``ERROR``     this request failed; a human should look
``CRITICAL``  the process cannot continue
============  ==============================================================

The commonest mistake is logging everything at ``INFO``, which makes the level
useless as a filter and buries the two lines that mattered.
"""

from __future__ import annotations

import json
import logging
import logging.config
import sys
import time
import uuid
from typing import Any

from flask import Flask, Response, g, has_request_context, request

from .settings import Settings

# Keys that must never be written to a log, whatever they contain. Log
# aggregators are searchable, retained for months, and read by people who do not
# need your users' passwords.
SENSITIVE_KEYS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "authorization", "auth", "cookie", "session", "csrf_token",
    "credit_card", "card_number", "cvv", "ssn", "aadhaar",
})


class RequestContextFilter(logging.Filter):
    """Attach request metadata to every record.

    A ``logging.Filter`` is the idiomatic way to *enrich* records — despite the
    name, returning ``True`` keeps the record, and the hook is free to add
    attributes on the way through.

    This runs for **every** log line, including ones emitted deep inside library
    code that knows nothing about Flask. That is precisely the point: you get a
    request id on SQLAlchemy's warnings too.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Add request fields to ``record``.

        Args:
            record: The log record being emitted.

        Returns:
            bool: Always ``True`` — nothing is filtered out.

        Note:
            ``has_request_context()`` is essential. Log lines are also emitted
            at start-up, from CLI commands, and from background threads, where
            touching ``request`` raises ``RuntimeError: Working outside of
            request context``. A logging call must never be able to crash the
            thing it is reporting on.
        """
        if has_request_context():
            record.request_id = getattr(g, "request_id", "-")
            record.method = request.method
            record.path = request.path
            record.remote_addr = request.remote_addr or "-"
        else:
            record.request_id = "-"
            record.method = "-"
            record.path = "-"
            record.remote_addr = "-"
        return True


class JsonFormatter(logging.Formatter):
    """Render records as one JSON object per line.

    Why JSON in production: log aggregators (CloudWatch, Datadog, Loki, ELK)
    parse structured lines into **queryable fields**. ``request_id:"abc"`` is a
    filter; the same value inside a prose sentence is a substring search across
    terabytes.

    Why text in development: JSON is unreadable in a terminal.

    Hence ``APP_LOG_FORMAT`` — the same code, formatted for whoever is reading.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Render one record as JSON.

        Args:
            record: The log record.

        Returns:
            str: A single-line JSON object.
        """
        payload: dict[str, Any] = {
            # UTC, always. A distributed system whose logs are in local time
            # cannot be correlated across regions.
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "method": getattr(record, "method", "-"),
            "path": getattr(record, "path", "-"),
        }

        if record.exc_info:
            # The traceback belongs in the log, where only you can read it —
            # never in the HTTP response, where it leaks paths and internals.
            payload["exception"] = self.formatException(record.exc_info)

        # Anything passed as `extra={...}` on the logging call.
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value

        # default=str stops an unserialisable value (a datetime, a Decimal, a
        # model object) from raising inside the logger — a logging call must
        # never be the thing that breaks the request.
        return json.dumps(payload, default=str)


def scrub(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive values before logging a dictionary.

    Args:
        data: Arbitrary key/value data, e.g. form fields.

    Returns:
        dict[str, Any]: The same shape with sensitive values replaced.

    Example:
        >>> scrub({"email": "a@b.com", "password": "hunter2"})
        {'email': 'a@b.com', 'password': '***REDACTED***'}

    Note:
        Logging ``request.form`` wholesale is how plaintext passwords end up in
        a retained, searchable log — a genuine breach that no amount of
        password hashing protects against, because the password never reached
        the hashing code before it was written to disk.

        The check is a **substring** match, so ``user_password`` and
        ``api_key_v2`` are caught too.
    """
    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        lowered = key.lower()
        if any(marker in lowered for marker in SENSITIVE_KEYS):
            cleaned[key] = "***REDACTED***"
        elif isinstance(value, dict):
            cleaned[key] = scrub(value)
        else:
            cleaned[key] = value
    return cleaned


def configure_logging(settings: Settings) -> None:
    """Configure logging for the whole process.

    Args:
        settings: Validated application settings.

    Note:
        ``dictConfig`` configures the **root** logger, so Flask, Werkzeug,
        SQLAlchemy and your own modules all share one destination and format.
        Configuring ``app.logger`` alone leaves every library logging somewhere
        else, which is why people end up with two different log formats
        interleaved.

        ``disable_existing_loggers: False`` matters: the default (``True``)
        silently mutes loggers created before this call — including ones created
        at import time by libraries.
    """
    formatter = (
        {"()": JsonFormatter} if settings.log_format == "json"
        else {
            "format": (
                "%(asctime)s %(levelname)-8s [%(request_id)s] "
                "%(name)s: %(message)s"
            ),
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    )

    handlers: dict[str, Any] = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "filters": ["request_context"],
            # stdout, not stderr: in a container, stdout is the log stream.
            "stream": sys.stdout,
        }
    }

    if settings.log_file:
        settings.log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            # Rotating, always. A log file with no rotation eventually fills the
            # disk, and a full disk takes the whole application down.
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(settings.log_file),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
            "formatter": "default",
            "filters": ["request_context"],
            "encoding": "utf-8",
        }

    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"request_context": {"()": RequestContextFilter}},
        "formatters": {"default": formatter},
        "handlers": handlers,
        "root": {"level": settings.log_level, "handlers": list(handlers)},
        "loggers": {
            # Werkzeug's per-request access log duplicates our own request log,
            # so turn it down to warnings.
            "werkzeug": {"level": "WARNING", "propagate": True},
        },
    })


def register_request_logging(app: Flask, settings: Settings) -> None:
    """Assign a request id and log the start and end of every request.

    Args:
        app: The application.
        settings: Validated settings.
    """
    logger = logging.getLogger("app.request")

    @app.before_request
    def start_request() -> None:
        """Assign a correlation id and record the start time.

        Note:
            If the caller supplied an id (a load balancer, an upstream service,
            a browser fetch), **reuse it**. That is what lets you follow one
            user action across several services — the single most useful thing a
            request id does.

            ``g`` is per-request storage, cleared automatically at the end. It
            is the right home for this; a module-level variable would be shared
            between concurrent requests and give you the wrong id.
        """
        incoming = request.headers.get(settings.request_id_header)
        g.request_id = incoming or uuid.uuid4().hex[:12]
        g.request_started = time.perf_counter()

    @app.after_request
    def finish_request(response: Response) -> Response:
        """Log the outcome and echo the request id back.

        Args:
            response: The outgoing response.

        Returns:
            Response: The same response, with the correlation header added.

        Note:
            Returning the id in a header means a user reporting a problem can
            quote it, and you can find their exact request in seconds. It costs
            nothing and it is the difference between "sometime yesterday, the
            page broke" and one log query.

            The level is chosen from the outcome: a 500 is an ``ERROR``, a slow
            request is a ``WARNING``. Logging everything at ``INFO`` makes the
            level useless as a filter.
        """
        duration_ms = (time.perf_counter() - getattr(g, "request_started", time.perf_counter())) * 1000
        response.headers[settings.request_id_header] = getattr(g, "request_id", "-")

        if response.status_code >= 500:
            level = logging.ERROR
        elif response.status_code >= 400 or duration_ms > settings.slow_request_ms:
            level = logging.WARNING
        else:
            level = logging.INFO

        logger.log(
            level,
            "%s %s -> %s in %.1fms",
            request.method, request.path, response.status_code, duration_ms,
            # `extra` adds structured fields to the JSON output without
            # cluttering the human-readable message.
            extra={"extra_fields": {
                "status": response.status_code,
                "duration_ms": round(duration_ms, 1),
                "slow": duration_ms > settings.slow_request_ms,
            }},
        )
        return response
