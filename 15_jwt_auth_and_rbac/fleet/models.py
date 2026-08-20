"""
Day 15 — Models: users with roles, vehicles, and a token blocklist.
===================================================================

The blocklist is the interesting one. A JWT is **self-contained**: the server
verifies it by checking a signature, without looking anything up. That is what
makes JWTs fast and horizontally scalable — and it is also why you cannot simply
"delete" one. Until it expires, a stolen token keeps working.

The blocklist buys back revocation, at the cost of the very statelessness that
made JWTs attractive. That trade-off is the heart of today.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db


class Role(str, enum.Enum):
    """What a user is allowed to do.

    Ordered from least to most privileged so :meth:`at_least` can compare them.
    """

    VIEWER = "viewer"
    DRIVER = "driver"
    MANAGER = "manager"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        """Privilege level, higher is more.

        Returns:
            int: ``0`` for viewer through ``3`` for admin.
        """
        return {"viewer": 0, "driver": 1, "manager": 2, "admin": 3}[self.value]

    def at_least(self, other: "Role") -> bool:
        """Whether this role meets or exceeds ``other``.

        Args:
            other: The minimum required role.

        Returns:
            bool: True when permitted.

        Note:
            A **hierarchy** is only appropriate when privileges genuinely nest —
            an admin really can do everything a driver can. When they do not
            (an auditor may read finance data that an engineer cannot, and vice
            versa), model **permissions** as a set instead. Forcing unrelated
            duties onto one axis is how you end up granting admin to somebody
            who only needed one report.
        """
        return self.rank >= other.rank


class User(db.Model):
    """An account.

    Attributes:
        id: Surrogate primary key.
        email: Unique login identifier.
        display_name: Name shown in responses.
        password_hash: scrypt verifier (Day 13).
        role: The user's role.
        active: Whether the account may authenticate.
        token_version: Bumped to invalidate every existing token for this user.
        created_at: Registration timestamp.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        db.Enum(Role, native_enum=False, length=20), nullable=False, default=Role.VIEWER
    )
    active: Mapped[bool] = mapped_column(nullable=False, default=True)

    # -------------------------------------------------------------------------
    # token_version: "sign out everywhere", in one integer
    # -------------------------------------------------------------------------
    # Every token this user is issued carries the current value as a claim. Bump
    # the column and every previously-issued token fails the check on its next
    # use — no per-token bookkeeping required.
    #
    # This is the cheap, coarse revocation tool: perfect for "log out all
    # devices" and for password changes. The blocklist below is the precise,
    # expensive one, for revoking a single token.
    token_version: Mapped[int] = mapped_column(nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="assigned_to")

    def set_password(self, password: str) -> None:
        """Hash and store a password.

        Args:
            password: The plaintext password; used and discarded.
        """
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Verify a password guess in constant time.

        Args:
            password: The plaintext guess.

        Returns:
            bool: True when it matches.
        """
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: Identity and role — never the hash.
        """
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role.value,
            "active": self.active,
        }


class Vehicle(db.Model):
    """A fleet vehicle.

    Attributes:
        id: Surrogate primary key.
        registration: Unique plate number.
        model: Make and model.
        status: Free-text operational status.
        odometer_km: Distance travelled.
        assigned_to_id: Optional assigned driver.
        assigned_to: The related user.
    """

    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True)
    registration: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="available")
    odometer_km: Mapped[int] = mapped_column(nullable=False, default=0)

    assigned_to_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_to: Mapped["User | None"] = relationship(back_populates="vehicles")

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: JSON-safe representation.
        """
        return {
            "id": self.id,
            "registration": self.registration,
            "model": self.model,
            "status": self.status,
            "odometer_km": self.odometer_km,
            "assigned_to": (
                {"id": self.assigned_to.id, "name": self.assigned_to.display_name}
                if self.assigned_to else None
            ),
        }


class RevokedToken(db.Model):
    """A token that has been explicitly revoked before its expiry.

    Attributes:
        id: Surrogate primary key.
        jti: The token's unique identifier (``jti`` claim).
        token_type: ``"access"`` or ``"refresh"``.
        user_id: Who the token belonged to.
        revoked_at: When it was revoked.
        expires_at: The token's own expiry, used to prune this table.

    Note:
        **Prune this table.** A revoked token is harmless once it has expired
        anyway, so rows older than the token lifetime can be deleted. Without a
        cleanup job the blocklist grows forever and the lookup you added to every
        request gets slower forever — see the ``prune-tokens`` CLI command.
    """

    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Indexed and unique: this is looked up on EVERY authenticated request, so
    # it must be a single indexed hit, not a scan.
    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    token_type: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[int] = mapped_column(nullable=False, index=True)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    @staticmethod
    def utcnow() -> datetime:
        """Return the current aware UTC time.

        Returns:
            datetime: Timezone-aware now.
        """
        return datetime.now(timezone.utc)
