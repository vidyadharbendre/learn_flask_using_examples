"""
Day 13 — Models: users, password hashes, and roles.
====================================================

The single most important rule in this file
--------------------------------------------
**You never store a password.** You store a *verifier* — a slow, salted hash
that can confirm a guess but cannot be reversed. When your database leaks (and
you should design as though it will), the attacker gets hashes, not accounts.

Concretely::

    password = "hunter2"                         # ❌ catastrophic
    password = md5("hunter2")                    # ❌ cracked at billions/second
    password = sha256("hunter2")                 # ❌ same problem: too fast
    password_hash = generate_password_hash(...)  # ✅ scrypt, salted, slow

Fast hashes are *designed* to be fast, which is exactly wrong here. A password
hash must be **deliberately slow** so that a leaked database cannot be brute
forced. Werkzeug defaults to **scrypt**, which is memory-hard and resists GPU
attacks; bcrypt and Argon2 are the other reasonable choices.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from werkzeug.security import check_password_hash, generate_password_hash

from flask_login import UserMixin

from .extensions import db


class User(UserMixin, db.Model):
    """A portal member.

    ``UserMixin`` supplies the four properties Flask-Login requires —
    ``is_authenticated``, ``is_active``, ``is_anonymous`` and ``get_id()`` —
    with sensible defaults, so you only override what differs. Here
    ``is_active`` is overridden to respect the ``active`` column, which is what
    makes account suspension work.

    Attributes:
        id: Surrogate primary key.
        email: Unique, lower-cased login identifier.
        display_name: Name shown in the UI.
        password_hash: The scrypt verifier. **Never** the password.
        role: ``"member"`` or ``"admin"``.
        active: Whether the account may sign in.
        created_at: Registration timestamp.
        last_login_at: Updated on each successful sign-in.
        notes: Private notes belonging to this user.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)

    # Length matters. scrypt hashes from Werkzeug are ~160 characters; a
    # VARCHAR(60) sized for bcrypt would TRUNCATE them, and every login would
    # fail with no obvious cause. Give the column generous room.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[str] = mapped_column(String(20), nullable=False, default="member")
    active: Mapped[bool] = mapped_column(nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    notes: Mapped[list["Note"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan",
        order_by="Note.created_at.desc()",
    )

    # -------------------------------------------------------------------------
    # Password handling
    # -------------------------------------------------------------------------
    def set_password(self, password: str) -> None:
        """Hash and store a new password.

        Args:
            password: The plaintext password. It is used and discarded; it is
                never stored, logged, or kept on the instance.

        Note:
            ``generate_password_hash`` salts automatically, so two users with
            the same password get different hashes. That is what defeats
            rainbow tables and stops an attacker learning that two accounts
            share a password.
        """
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        """Verify a password guess against the stored hash.

        Args:
            password: The plaintext guess.

        Returns:
            bool: True when it matches.

        Note:
            ``check_password_hash`` re-hashes the guess with the stored salt and
            compares in **constant time**. Never write
            ``hash(guess) == stored`` — ``==`` returns at the first differing
            byte, and that timing difference is measurable (Day 04).
        """
        return check_password_hash(self.password_hash, password)

    # -------------------------------------------------------------------------
    # Flask-Login integration
    # -------------------------------------------------------------------------
    @property
    def is_active(self) -> bool:  # type: ignore[override]
        """Whether this account may sign in.

        Returns:
            bool: The ``active`` column.

        Note:
            Flask-Login checks this during ``login_user()`` and refuses to sign
            in an inactive account. Overriding it here means "suspend user" is a
            single column update rather than a special case scattered through
            your views.
        """
        return self.active

    @property
    def is_admin(self) -> bool:
        """Whether this user holds the admin role.

        Returns:
            bool: True for administrators.
        """
        return self.role == "admin"

    def touch_login(self) -> None:
        """Record a successful sign-in."""
        self.last_login_at = datetime.now(timezone.utc)

    def __repr__(self) -> str:
        """Return an unambiguous representation.

        Returns:
            str: e.g. ``<User 1 'ana@example.com'>``. Note it contains **no**
            password material — a ``__repr__`` ends up in logs and tracebacks.
        """
        return f"<User {self.id} {self.email!r}>"


class Note(db.Model):
    """A private note belonging to one user.

    Exists so the portal has something worth protecting: the authorisation
    lesson only lands when there is data that must not leak between accounts.

    Attributes:
        id: Surrogate primary key.
        title: Short heading.
        body: Note content.
        user_id: Owning user.
        owner: The related user.
        created_at: Creation timestamp.
    """

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner: Mapped["User"] = relationship(back_populates="notes")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialise for display.

        Returns:
            dict[str, Any]: JSON-safe representation.
        """
        return {"id": self.id, "title": self.title, "body": self.body}
