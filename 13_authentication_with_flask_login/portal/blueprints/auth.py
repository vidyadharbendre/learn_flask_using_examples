"""
Day 13 — The ``auth`` blueprint: register, sign in, sign out.
=============================================================

Authentication is where beginner code most reliably becomes a security
incident. Every view below carries a defence, and each one is labelled.
"""

from __future__ import annotations

from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.wrappers import Response

from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..forms import LoginForm, PasswordChangeForm, RegisterForm
from ..models import User

auth_bp = Blueprint("auth", __name__, template_folder="../templates/auth")

# A hash of a throwaway password, computed once at import time. See
# `_verify_credentials` for why this exists — it is the fix for username
# enumeration through response timing.
_DUMMY_HASH = generate_password_hash("not-a-real-password-just-for-timing")


def is_safe_redirect_url(target: str | None) -> bool:
    """Return whether ``target`` is a safe place to redirect after login.

    Args:
        target: The candidate URL, usually from ``?next=``.

    Returns:
        bool: True only for a relative path on this host.

    Note:
        **This function prevents an open-redirect vulnerability**, and its
        absence is one of the most common real bugs in Flask login code:

            ``/login?next=https://evil.example/fake-login``

        The user signs in on your genuine site, and you then send them to a
        pixel-perfect clone that asks them to "confirm" their password. The
        phishing link is legitimate — it points at *your* domain — so it passes
        every filter and every wary user.

        The rule: only ever redirect to a **relative path on your own host**,
        never to a client-supplied absolute URL.
    """
    if not target:
        return False

    # Reject anything with a scheme or host, and protocol-relative "//evil.com"
    # (which browsers treat as absolute — an easy one to miss).
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return False
    if target.startswith("//") or "\\" in target:
        return False
    return target.startswith("/")


def _verify_credentials(email: str, password: str) -> User | None:
    """Look up a user and check the password in constant-ish time.

    Args:
        email: Submitted email address.
        password: Submitted plaintext password.

    Returns:
        User | None: The user when the credentials are valid, else ``None``.

    Note:
        The ``else`` branch hashes a dummy password on purpose. Without it, a
        request for a **non-existent** account returns much faster than one for
        a real account with a wrong password — because only the second performs
        an expensive scrypt hash.

        That timing difference is measurable over a few hundred requests, and it
        turns your login form into a **user-enumeration oracle**: an attacker
        learns which email addresses are registered, which is valuable on its
        own and makes credential-stuffing far cheaper.

        Doing the same work either way removes the signal.
    """
    user = db.session.execute(
        select(User).where(User.email == email)
    ).scalar_one_or_none()

    if user is None:
        # Burn the same CPU we would have burned for a real user.
        check_password_hash(_DUMMY_HASH, password)
        return None

    if not user.check_password(password):
        return None
    return user


@auth_bp.route("/register", methods=["GET", "POST"])
def register() -> str | Response:
    """Create a new account.

    Returns:
        str | Response: The rendered form, or a 303 redirect after signing the
        new user in.

    Note:
        Already-authenticated users are bounced away. Letting a signed-in user
        submit a registration form leads to confusing half-states and is a
        common source of session bugs.
    """
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"), code=303)

    form = RegisterForm()
    if form.validate_on_submit():
        user = User(
            email=(form.email.data or "").strip().lower(),
            display_name=(form.display_name.data or "").strip(),
        )
        user.set_password(form.password.data or "")

        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            # Deliberately vague. "That email is already registered" confirms
            # to a stranger that an address has an account here — the same
            # enumeration leak as above, through a different door.
            #
            # The user-friendly production answer is to accept the registration
            # silently and EMAIL the address: a genuine owner gets either a
            # welcome or a "you already have an account" message, and an
            # attacker learns nothing from the web response.
            form.email.errors = list(form.email.errors) + [
                "We could not complete that registration."
            ]
        else:
            _sign_in(user, remember=False)
            flash(f"Welcome, {user.display_name}!", "success")
            return redirect(url_for("main.dashboard"), code=303)

    return render_template("auth/register.html", form=form), (
        422 if form.errors else 200
    )


@auth_bp.route("/login", methods=["GET", "POST"])
def login() -> str | Response:
    """Sign an existing user in.

    Returns:
        str | Response: The rendered form, or a 303 redirect on success.
    """
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"), code=303)

    form = LoginForm()
    if form.validate_on_submit():
        user = _verify_credentials(
            (form.email.data or "").strip().lower(), form.password.data or ""
        )

        if user is None or not user.is_active:
            # ONE message for every failure: unknown email, wrong password,
            # suspended account. Distinguishing them is precisely the
            # enumeration leak we are avoiding. Log the detail server-side
            # (Day 18) where only you can read it.
            current_app.logger.info("Failed login attempt for %r", form.email.data)
            flash("Invalid email or password.", "error")
            return render_template("auth/login.html", form=form), 401

        _sign_in(user, remember=bool(form.remember.data))
        user.touch_login()
        db.session.commit()
        flash(f"Signed in as {user.display_name}.", "success")

        # ---- the redirect, done safely --------------------------------------
        next_url = request.args.get("next")
        if not is_safe_redirect_url(next_url):
            next_url = url_for("main.dashboard")
        return redirect(next_url, code=303)

    return render_template("auth/login.html", form=form), (422 if form.errors else 200)


def _sign_in(user: User, *, remember: bool) -> None:
    """Establish a session for ``user``, rotating the session id first.

    Args:
        user: The authenticated user.
        remember: Whether to issue a long-lived "remember me" cookie.

    Note:
        **Session fixation.** An attacker who can set a victim's session cookie
        before they log in — via an XSS bug, a shared machine, or a
        ``?sessionid=`` style link — would otherwise still hold a valid cookie
        *after* the victim authenticates, and would be logged in as them.

        The defence is to discard the pre-login session and start a fresh one,
        so any identifier the attacker planted becomes worthless. Flask's
        ``session.clear()`` regenerates the signed cookie on the next response,
        which achieves this for the cookie-based session.

        Do this on **every** privilege change: login, and also any step-up such
        as entering an admin area or completing MFA.
    """
    session.clear()
    login_user(user, remember=remember)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout() -> Response:
    """Sign the current user out.

    Returns:
        Response: 303 redirect to the login page.

    Note:
        **POST, not GET.** A ``GET /logout`` link can be triggered by any other
        site with ``<img src="https://yoursite/logout">`` — a nuisance CSRF that
        logs your users out at random. It is also fired by prefetching browsers
        and crawlers.

        ``session.clear()`` after ``logout_user()`` removes everything else
        too — cart, flashes, CSRF token — so nothing survives the sign-out.
    """
    logout_user()
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"), code=303)


@auth_bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password() -> str | Response:
    """Change the signed-in user's password.

    Returns:
        str | Response: The rendered form, or a 303 redirect on success.

    Note:
        The **current** password is required even though the user is already
        signed in. This protects against an unattended logged-in browser, and
        it is why every serious site asks. Never skip it.
    """
    form = PasswordChangeForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data or ""):
            form.current_password.errors = list(form.current_password.errors) + [
                "That is not your current password."
            ]
        else:
            current_user.set_password(form.new_password.data or "")
            db.session.commit()

            # Re-establish the session with the new credentials. A real
            # application should also invalidate every OTHER session belonging
            # to this user — "change password" is what a victim does after a
            # compromise, and it must actually evict the attacker. With
            # cookie sessions that requires a per-user token in the model (see
            # the exercises); with server-side sessions you delete the rows.
            _sign_in(current_user, remember=False)
            flash("Password updated.", "success")
            return redirect(url_for("main.dashboard"), code=303)

    return render_template("auth/change_password.html", form=form), (
        422 if form.errors else 200
    )
