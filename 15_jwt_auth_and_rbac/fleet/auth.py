"""
Day 15 — Token authentication and role-based access control.
============================================================

Sessions vs tokens
------------------
Day 13's session cookie is the right answer for a browser talking to your own
server. It is the **wrong** answer for:

- a mobile app (no cookie jar you control, no CSRF story);
- a third-party API client (cookies are a browser concept);
- service-to-service calls (no browser at all);
- a front-end on a different origin (cross-site cookies are increasingly blocked).

===================  ==============================  ============================
                     Session cookie                  JWT
===================  ==============================  ============================
Server state         session store or signed cookie  **none** — self-contained
Revocation           delete the session              hard (see the blocklist)
Sent automatically   yes (that is why CSRF exists)   no — client attaches it
Good for             your own browser front end      APIs, mobile, services
===================  ==============================  ============================

**A JWT is signed, not encrypted.** Exactly like Day 06's session cookie, anyone
holding it can read every claim inside. Put an id and a role in it; never a
password, an API key, or personal data you would not print in a log.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from flask import jsonify
from flask.typing import ResponseReturnValue
from sqlalchemy import select

from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    get_jwt,
    get_jwt_identity,
    verify_jwt_in_request,
)

from .extensions import db, jwt
from .models import RevokedToken, Role, User


def issue_tokens(user: User) -> dict[str, Any]:
    """Create an access/refresh pair for ``user``.

    Args:
        user: The authenticated user.

    Returns:
        dict[str, Any]: ``access_token``, ``refresh_token``, ``token_type`` and
        the user's public profile.

    Note:
        **Why two tokens?**

        The access token is short-lived (15 minutes here) and is sent with every
        request, so it is the one most likely to be captured — in a log, a proxy,
        a crash report. A short life bounds the damage.

        The refresh token is long-lived (30 days) but travels **only** to
        ``/auth/refresh``, so it is exposed far less often. It buys a new access
        token without asking the user to log in again.

        Getting this backwards — one long-lived access token — means a single
        leak grants an attacker months of access.

        ``additional_claims`` embeds the role and the user's ``token_version``,
        so authorisation checks need no database query at all. The trade-off is
        **staleness**: demote a user and their current access token still says
        "manager" until it expires. Fifteen minutes is the size of that window,
        which is precisely why the access token is short.
    """
    claims = {
        "role": user.role.value,
        "name": user.display_name,
        "tv": user.token_version,
    }
    return {
        "access_token": create_access_token(identity=str(user.id), additional_claims=claims),
        "refresh_token": create_refresh_token(identity=str(user.id), additional_claims=claims),
        "token_type": "Bearer",
        "user": user.to_dict(),
    }


def register_jwt_callbacks() -> None:
    """Install the callbacks Flask-JWT-Extended uses to validate a token.

    Note:
        These run on **every** authenticated request. Keep them cheap: this is
        the tax you pay for revocation, and it is where a JWT quietly stops
        being stateless.
    """

    @jwt.token_in_blocklist_loader
    def token_is_revoked(jwt_header: dict[str, Any], jwt_payload: dict[str, Any]) -> bool:
        """Report whether a token has been revoked.

        Two independent mechanisms, for two different needs:

        1. **Blocklist** — a single token was revoked (one logout, one leaked
           token). Precise, and costs an indexed lookup per request.
        2. **``token_version``** — every token for that user is invalid at once
           (password change, "sign out everywhere", account compromise). Coarse,
           and costs a primary-key lookup.

        Args:
            jwt_header: The decoded JWT header.
            jwt_payload: The decoded claims.

        Returns:
            bool: True when the token must be rejected.
        """
        jti = jwt_payload.get("jti")
        if jti and db.session.execute(
            select(RevokedToken.id).where(RevokedToken.jti == jti)
        ).first():
            return True

        user = db.session.get(User, int(jwt_payload.get("sub", 0)))
        if user is None or not user.active:
            return True

        # A token issued before the version was bumped is stale.
        return jwt_payload.get("tv") != user.token_version

    # -------------------------------------------------------------------------
    # Error handlers: say WHY the token failed
    # -------------------------------------------------------------------------
    # Flask-JWT-Extended's defaults are terse. Distinguishing "expired" from
    # "malformed" from "missing" is the difference between a client that can
    # refresh automatically and a developer guessing for an afternoon.
    #
    # Note the status codes: 401 means "you have not proved who you are"
    # (missing, expired, invalid token); 403 means "you are known, but not
    # permitted" — which is what the role decorators below return.
    @jwt.expired_token_loader
    def expired(jwt_header: dict[str, Any], jwt_payload: dict[str, Any]) -> ResponseReturnValue:
        """Answer an expired token.

        Args:
            jwt_header: The decoded header.
            jwt_payload: The decoded claims.

        Returns:
            ResponseReturnValue: A 401 telling the client to refresh.
        """
        return jsonify(error={
            "code": "token_expired",
            "message": f"The {jwt_payload.get('type', 'access')} token has expired.",
            "hint": "POST /api/v1/auth/refresh with your refresh token.",
        }), 401

    @jwt.invalid_token_loader
    def invalid(reason: str) -> ResponseReturnValue:
        """Answer a malformed or badly-signed token.

        Args:
            reason: The library's explanation.

        Returns:
            ResponseReturnValue: A 401.
        """
        return jsonify(error={"code": "token_invalid", "message": reason}), 401

    @jwt.unauthorized_loader
    def missing(reason: str) -> ResponseReturnValue:
        """Answer a request with no token at all.

        Args:
            reason: The library's explanation.

        Returns:
            ResponseReturnValue: A 401 with the expected header format.
        """
        return jsonify(error={
            "code": "authorization_required",
            "message": reason,
            "hint": "Send: Authorization: Bearer <access_token>",
        }), 401

    @jwt.revoked_token_loader
    def revoked(jwt_header: dict[str, Any], jwt_payload: dict[str, Any]) -> ResponseReturnValue:
        """Answer a revoked token.

        Args:
            jwt_header: The decoded header.
            jwt_payload: The decoded claims.

        Returns:
            ResponseReturnValue: A 401.
        """
        return jsonify(error={
            "code": "token_revoked",
            "message": "This token has been revoked. Sign in again.",
        }), 401

    @jwt.needs_fresh_token_loader
    def needs_fresh(jwt_header: dict[str, Any], jwt_payload: dict[str, Any]) -> ResponseReturnValue:
        """Answer when a sensitive endpoint requires a fresh token.

        Args:
            jwt_header: The decoded header.
            jwt_payload: The decoded claims.

        Returns:
            ResponseReturnValue: A 401 asking the user to re-authenticate.

        Note:
            A **fresh** token is one obtained by actually typing a password,
            rather than by refreshing. Requiring freshness for dangerous actions
            — changing a password, deleting an account — means a stolen refresh
            token cannot be escalated into account takeover.
        """
        return jsonify(error={
            "code": "fresh_token_required",
            "message": "This action requires you to sign in again.",
        }), 401


def revoke_token(jwt_payload: dict[str, Any]) -> None:
    """Add a token to the blocklist.

    Args:
        jwt_payload: The decoded claims of the token to revoke.

    Note:
        ``expires_at`` is stored so the row can be pruned later. A revoked token
        is harmless once expired, and an unbounded blocklist slowly poisons the
        per-request lookup.
    """
    db.session.add(RevokedToken(
        jti=jwt_payload["jti"],
        token_type=jwt_payload.get("type", "access"),
        user_id=int(jwt_payload.get("sub", 0)),
        expires_at=datetime.fromtimestamp(jwt_payload["exp"], tz=timezone.utc),
    ))
    db.session.commit()


def current_user() -> User | None:
    """Load the user named by the current token.

    Returns:
        User | None: The user, or ``None`` when the token has no valid subject.

    Note:
        Only call this when you genuinely need the **row**. Most authorisation
        checks can be answered from the claims (``get_jwt()["role"]``) with no
        query at all, which is the main performance argument for JWTs.
    """
    identity = get_jwt_identity()
    return db.session.get(User, int(identity)) if identity else None


def role_required(minimum: Role) -> Callable[..., Any]:
    """Require a role at or above ``minimum``.

    Args:
        minimum: The least privileged role permitted.

    Returns:
        Callable: A decorator for a view function.

    Example:
        >>> @app.post("/vehicles")            # doctest: +SKIP
        ... @role_required(Role.MANAGER)
        ... def create_vehicle(): ...

    Note:
        The role is read from the **token claim**, so no database query is
        needed. That is fast, and it is stale for up to the access token's
        lifetime — the trade-off described in :func:`issue_tokens`.

        For an action that must never run on stale authority, read the role from
        the database instead, or require a fresh token.

        ``verify_jwt_in_request()`` is called explicitly rather than stacking
        ``@jwt_required()``, so this single decorator does both jobs and the
        order of decorators cannot be got wrong (Day 13 §11).
    """

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(view)  # without this, every view registers as "wrapper"
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            verify_jwt_in_request()

            raw_role = get_jwt().get("role", Role.VIEWER.value)
            try:
                role = Role(raw_role)
            except ValueError:
                role = Role.VIEWER

            if not role.at_least(minimum):
                # 403, not 401: the caller IS authenticated — they simply may
                # not do this. Returning 401 would tell a well-behaved client
                # to go and get a new token, which would not help at all.
                return jsonify(error={
                    "code": "insufficient_role",
                    "message": f"This action requires the {minimum.value} role or higher.",
                    "your_role": role.value,
                }), 403

            return view(*args, **kwargs)

        return wrapper

    return decorator
