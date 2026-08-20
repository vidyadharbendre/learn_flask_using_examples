"""Day 21 — Authentication (Day 13 patterns)."""

from __future__ import annotations

from urllib.parse import urlparse

from flask import (
    Blueprint, current_app, flash, redirect, render_template, request, session, url_for,
)
from flask.typing import ResponseReturnValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db, limiter
from ..forms import LoginForm, RegisterForm
from ..models import User

auth_bp = Blueprint("auth", __name__, template_folder="../templates/auth")

_DUMMY_HASH = generate_password_hash("timing-equaliser", method="pbkdf2:sha256:1000")


def is_safe_redirect_url(target: str | None) -> bool:
    """Return whether ``target`` is a relative path on this host.

    Args:
        target: Candidate URL, usually from ``?next=``.

    Returns:
        bool: True only for a safe relative path (Day 13 §8).
    """
    if not target:
        return False
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return False
    if target.startswith("//") or "\\" in target:
        return False
    return target.startswith("/")


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour")
def register() -> ResponseReturnValue:
    """Create an account and sign in.

    Returns:
        ResponseReturnValue: The form, or a 303 redirect on success.
    """
    if current_user.is_authenticated:
        return redirect(url_for("surveys.index"), code=303)

    form = RegisterForm()
    if form.validate_on_submit():
        user = User(
            email=(form.email.data or "").strip().lower(),
            display_name=(form.display_name.data or "").strip(),
        )
        user.set_password(form.password.data or "")
        user.rotate_token()

        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            # Deliberately vague: "already registered" confirms an address has
            # an account here (Day 13 §9).
            form.email.errors = list(form.email.errors) + [
                "We could not complete that registration."
            ]
        else:
            session.clear()          # defeat session fixation
            login_user(user)
            flash(f"Welcome, {user.display_name}!", "success")
            return redirect(url_for("surveys.index"), code=303)

    return render_template("auth/register.html", form=form), (422 if form.errors else 200)


@auth_bp.route("/login", methods=["GET", "POST"])
# Rate limiting a login form matters more than password complexity rules: it is
# what makes credential stuffing expensive (Day 19 §8).
@limiter.limit("10 per 15 minutes")
def login() -> ResponseReturnValue:
    """Sign in an existing user.

    Returns:
        ResponseReturnValue: The form, or a 303 redirect on success.
    """
    if current_user.is_authenticated:
        return redirect(url_for("surveys.index"), code=303)

    form = LoginForm()
    if form.validate_on_submit():
        email = (form.email.data or "").strip().lower()
        user = db.session.execute(select(User).where(User.email == email)).scalar_one_or_none()

        if user is None:
            check_password_hash(_DUMMY_HASH, form.password.data or "")  # equalise timing

        if user is None or not user.check_password(form.password.data or "") or not user.is_active:
            current_app.logger.info("Failed login for %r", email)
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html", form=form), 401

        session.clear()
        login_user(user, remember=bool(form.remember.data))
        flash(f"Signed in as {user.display_name}.", "success")

        requested = request.args.get("next")
        next_url = requested if is_safe_redirect_url(requested) else None
        return redirect(next_url or url_for("surveys.index"), code=303)

    return render_template("auth/login.html", form=form), (422 if form.errors else 200)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout() -> ResponseReturnValue:
    """Sign out. POST-only, so it cannot be triggered cross-site.

    Returns:
        ResponseReturnValue: 303 redirect to the login page.
    """
    logout_user()
    session.clear()
    flash("Signed out.", "info")
    return redirect(url_for("auth.login"), code=303)


@auth_bp.route("/token", methods=["GET", "POST"])
@login_required
def api_token() -> ResponseReturnValue:
    """Show, and optionally rotate, the caller's API token.

    Returns:
        ResponseReturnValue: The token page, or a 303 after rotating.

    Note:
        Rotation is the entire revocation story for an opaque token: replace it
        and the old one stops working immediately, with no blocklist and no
        version counter (contrast Day 15 §6).
    """
    if request.method == "POST":
        current_user.rotate_token()
        db.session.commit()
        flash("New API token issued. The old one no longer works.", "success")
        return redirect(url_for("auth.api_token"), code=303)

    return render_template("auth/token.html", token=current_user.api_token)
