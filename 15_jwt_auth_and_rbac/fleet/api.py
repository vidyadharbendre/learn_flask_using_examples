"""Day 15 — API endpoints, protected by tokens and roles."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from flask_jwt_extended import get_jwt, jwt_required

from .auth import current_user, issue_tokens, revoke_token, role_required
from .extensions import db
from .models import Role, User, Vehicle

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _json() -> dict[str, Any]:
    """Return the JSON body as a dict.

    Returns:
        dict[str, Any]: The decoded body, or ``{}`` when absent or malformed.
    """
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


# -----------------------------------------------------------------------------
# Authentication
# -----------------------------------------------------------------------------
@api_bp.post("/auth/login")
def login() -> ResponseReturnValue:
    """Exchange credentials for a token pair.

    Returns:
        ResponseReturnValue: ``200`` with tokens, or ``401`` on bad credentials.

    Note:
        One message for every failure — unknown email, wrong password, inactive
        account — exactly as on Day 13, and for the same reason: distinguishing
        them turns this endpoint into a user-enumeration oracle.
    """
    body = _json()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))

    user = db.session.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not user.check_password(password) or not user.active:
        return jsonify(error={
            "code": "invalid_credentials", "message": "Invalid email or password.",
        }), 401

    return jsonify(issue_tokens(user)), 200


@api_bp.post("/auth/refresh")
@jwt_required(refresh=True)
def refresh() -> ResponseReturnValue:
    """Exchange a refresh token for a new access token.

    Returns:
        ResponseReturnValue: ``200`` with a new pair, or ``401``.

    Note:
        ``@jwt_required(refresh=True)`` accepts **only** a refresh token here.
        Without that flag an access token would also be accepted, and the
        distinction between the two would be decorative.

        This implements **refresh-token rotation**: the presented refresh token
        is revoked and a new one issued. If a stolen refresh token is used, the
        legitimate user's next refresh fails — which is a detectable signal that
        something is wrong, instead of silent long-term compromise.
    """
    user = current_user()
    if user is None:
        return jsonify(error={"code": "user_not_found", "message": "No such user."}), 401

    revoke_token(get_jwt())          # rotation: the old refresh token dies here
    return jsonify(issue_tokens(user)), 200


@api_bp.post("/auth/logout")
@jwt_required(verify_type=False)
def logout() -> ResponseReturnValue:
    """Revoke the presented token.

    Returns:
        ResponseReturnValue: ``200``.

    Note:
        ``verify_type=False`` accepts either token type, so a client can revoke
        whichever it holds.

        **Logging out of a JWT system is not like logging out of a session.**
        There is nothing on the server to delete, so "logout" means adding this
        token's ``jti`` to the blocklist. Any *other* tokens the user holds
        remain valid — which is what ``/auth/logout-all`` exists for.
    """
    revoke_token(get_jwt())
    return jsonify(message="Token revoked."), 200


@api_bp.post("/auth/logout-all")
@jwt_required()
def logout_all() -> ResponseReturnValue:
    """Invalidate every token belonging to the current user.

    Returns:
        ResponseReturnValue: ``200``.

    Note:
        One integer increment invalidates every token ever issued to this user,
        because each token carries the version it was minted with. No blocklist
        rows, no scanning — this is the tool for "sign out everywhere",
        password changes, and suspected compromise.
    """
    user = current_user()
    if user is None:
        return jsonify(error={"code": "user_not_found", "message": "No such user."}), 401

    user.token_version += 1
    db.session.commit()
    return jsonify(message="All sessions ended. Sign in again.",
                   token_version=user.token_version), 200


@api_bp.get("/auth/me")
@jwt_required()
def me() -> ResponseReturnValue:
    """Return the caller's identity and the claims in their token.

    Returns:
        ResponseReturnValue: ``200`` with the user and the raw claims.

    Note:
        The ``claims`` block is included so you can see exactly what the token
        carries — and confirm for yourself that it holds no secrets.
    """
    user = current_user()
    if user is None:
        return jsonify(error={"code": "user_not_found", "message": "No such user."}), 401

    claims = get_jwt()
    return jsonify(user=user.to_dict(), claims={
        "sub": claims.get("sub"), "role": claims.get("role"),
        "type": claims.get("type"), "jti": claims.get("jti"),
        "iat": claims.get("iat"), "exp": claims.get("exp"),
        "fresh": claims.get("fresh"), "tv": claims.get("tv"),
    }), 200


# -----------------------------------------------------------------------------
# Vehicles — one endpoint per privilege level
# -----------------------------------------------------------------------------
@api_bp.get("/vehicles")
@role_required(Role.VIEWER)
def list_vehicles() -> ResponseReturnValue:
    """List vehicles. Any authenticated role may read.

    Returns:
        ResponseReturnValue: ``200`` with the fleet.
    """
    vehicles = db.session.execute(
        select(Vehicle).order_by(Vehicle.registration)
    ).scalars().all()
    return jsonify(data=[vehicle.to_dict() for vehicle in vehicles]), 200


@api_bp.patch("/vehicles/<int:vehicle_id>/odometer")
@role_required(Role.DRIVER)
def update_odometer(vehicle_id: int) -> ResponseReturnValue:
    """Record a new odometer reading. Drivers and above.

    Args:
        vehicle_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: ``200`` with the updated vehicle.

    Note:
        The reading may only ever increase. A monotonic value is a business
        rule, and the server is the only place it can be enforced.
    """
    vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None:
        return jsonify(error={"code": "not_found", "message": "No such vehicle."}), 404

    reading = _json().get("odometer_km")
    if not isinstance(reading, int) or isinstance(reading, bool):
        return jsonify(error={
            "code": "validation_error", "message": "odometer_km must be an integer.",
        }), 422
    if reading < vehicle.odometer_km:
        return jsonify(error={
            "code": "validation_error",
            "message": f"Odometer cannot decrease (currently {vehicle.odometer_km}).",
        }), 422

    vehicle.odometer_km = reading
    db.session.commit()
    return jsonify(vehicle.to_dict()), 200


@api_bp.post("/vehicles")
@role_required(Role.MANAGER)
def create_vehicle() -> ResponseReturnValue:
    """Add a vehicle. Managers and above.

    Returns:
        ResponseReturnValue: ``201`` with the created vehicle.
    """
    body = _json()
    registration = str(body.get("registration", "")).strip().upper()
    model = str(body.get("model", "")).strip()

    if not registration or not model:
        return jsonify(error={
            "code": "validation_error",
            "message": "registration and model are required.",
        }), 422

    vehicle = Vehicle(registration=registration, model=model)
    db.session.add(vehicle)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify(error={
            "code": "conflict", "message": f"{registration} is already registered.",
        }), 409

    return jsonify(vehicle.to_dict()), 201


@api_bp.delete("/vehicles/<int:vehicle_id>")
@role_required(Role.ADMIN)
def delete_vehicle(vehicle_id: int) -> ResponseReturnValue:
    """Remove a vehicle. Administrators only.

    Args:
        vehicle_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: ``204`` on success.
    """
    vehicle = db.session.get(Vehicle, vehicle_id)
    if vehicle is None:
        return jsonify(error={"code": "not_found", "message": "No such vehicle."}), 404

    db.session.delete(vehicle)
    db.session.commit()
    return "", 204


@api_bp.patch("/users/<int:user_id>/role")
@role_required(Role.ADMIN)
def set_role(user_id: int) -> ResponseReturnValue:
    """Change a user's role. Administrators only.

    Args:
        user_id: Primary key from the URL.

    Returns:
        ResponseReturnValue: ``200`` with the updated user.

    Note:
        Changing a role bumps ``token_version``. Without that, a demoted user
        would keep their old privileges — embedded in the token as a claim —
        until it expired. **A privilege change must invalidate outstanding
        tokens**, or the demotion is advisory for the next fifteen minutes.
    """
    user = db.session.get(User, user_id)
    if user is None:
        return jsonify(error={"code": "not_found", "message": "No such user."}), 404

    raw = str(_json().get("role", ""))
    try:
        new_role = Role(raw)
    except ValueError:
        return jsonify(error={
            "code": "validation_error",
            "message": f"role must be one of {[r.value for r in Role]}.",
        }), 422

    caller = current_user()
    if caller is not None and caller.id == user.id and new_role != Role.ADMIN:
        # Stop an admin locking everyone out by demoting themselves. Small
        # guard rails like this prevent genuinely painful incidents.
        return jsonify(error={
            "code": "forbidden", "message": "You cannot remove your own admin role.",
        }), 403

    user.role = new_role
    user.token_version += 1     # existing tokens carry the OLD role — kill them
    db.session.commit()
    return jsonify(user.to_dict()), 200


@api_bp.get("/admin/tokens")
@role_required(Role.ADMIN)
def list_revoked() -> ResponseReturnValue:
    """List revoked tokens. Administrators only.

    Returns:
        ResponseReturnValue: ``200`` with blocklist rows.
    """
    from .models import RevokedToken

    rows = db.session.execute(
        select(RevokedToken).order_by(RevokedToken.revoked_at.desc()).limit(50)
    ).scalars().all()
    return jsonify(data=[{
        "jti": row.jti, "type": row.token_type, "user_id": row.user_id,
        "revoked_at": row.revoked_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
    } for row in rows]), 200
