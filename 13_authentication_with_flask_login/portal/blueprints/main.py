"""
Day 13 — The ``main`` blueprint: the protected area.

Demonstrates the difference between **authentication** ("who are you?") and
**authorisation** ("may you do this?"). ``@login_required`` answers the first.
Only an ownership check answers the second — and forgetting it is the single
most common access-control bug in web applications.
"""

from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from flask import (
    Blueprint,
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    url_for,
)
from sqlalchemy import select
from werkzeug.wrappers import Response as WerkzeugResponse

from flask_login import current_user, login_required

from ..extensions import db
from ..forms import NoteForm
from ..models import Note, User

main_bp = Blueprint("main", __name__, template_folder="../templates/main")


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Require an authenticated **admin** user.

    Args:
        view: The view function to protect.

    Returns:
        Callable: The wrapped view.

    Note:
        Order matters when stacking decorators::

            @main_bp.route("/admin")
            @login_required          # runs FIRST — redirects anonymous users
            @admin_required          # then checks the role
            def admin_panel(): ...

        Decorators apply bottom-up, so the one listed *first* wraps outermost
        and runs first. Put ``@login_required`` above ``@admin_required`` or an
        anonymous visitor reaches the role check, where ``current_user.is_admin``
        raises ``AttributeError`` on ``AnonymousUserMixin`` — a 500 instead of a
        polite redirect to the login page.

        And ``@wraps`` is mandatory, or every protected view registers under the
        endpoint name ``"wrapper"`` (Day 12 §12).
    """

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not current_user.is_authenticated or not current_user.is_admin:
            # 403 Forbidden, not 404 and not a redirect: the user IS
            # authenticated, they simply may not do this. 401 would be wrong —
            # that means "you have not identified yourself".
            abort(403, description="Administrators only.")
        return view(*args, **kwargs)

    return wrapper


@main_bp.route("/")
def home() -> str:
    """Public landing page.

    Returns:
        str: Rendered ``main/home.html``.

    Note:
        ``current_user`` works on public pages too — it is an
        ``AnonymousUserMixin`` when nobody is signed in, so
        ``current_user.is_authenticated`` is simply ``False`` rather than an
        error. That is what lets one template serve both audiences.
    """
    return render_template("main/home.html")


@main_bp.route("/dashboard", methods=["GET", "POST"])
@login_required
def dashboard() -> str | WerkzeugResponse:
    """The signed-in user's private notes.

    Returns:
        str | WerkzeugResponse: The rendered dashboard, or a 303 redirect after
        creating a note.

    Note:
        The query filters by ``current_user.id``. Fetching *all* notes and
        filtering in the template would leak every user's data to anyone who
        views source or hits the JSON endpoint.

        **Scope the query, not the display.**
    """
    form = NoteForm()
    if form.validate_on_submit():
        note = Note(
            title=form.title.data or "",
            body=form.body.data or "",
            user_id=current_user.id,
        )
        db.session.add(note)
        db.session.commit()
        flash("Note saved.", "success")
        return redirect(url_for("main.dashboard"), code=303)

    notes = db.session.execute(
        select(Note).where(Note.user_id == current_user.id)
        .order_by(Note.created_at.desc())
    ).scalars().all()

    return render_template("main/dashboard.html", notes=notes, form=form), (
        422 if form.errors else 200
    )


@main_bp.route("/notes/<int:note_id>")
@login_required
def note_detail(note_id: int) -> str:
    """Show one note — **if it belongs to you**.

    Args:
        note_id: Primary key from the URL.

    Returns:
        str: Rendered ``main/note.html``.

    Raises:
        werkzeug.exceptions.NotFound: when the note does not exist **or**
            belongs to someone else.

    Note:
        This is the lesson of the day. ``@login_required`` proves *who you are*;
        it says nothing about *what is yours*. Without the ownership check,
        signing in as anybody and walking ``/notes/1``, ``/notes/2``, … reads
        every user's private data. That flaw has a name — **IDOR**, Insecure
        Direct Object Reference — and it is consistently among the most common
        serious vulnerabilities found in real applications.

        Note it returns **404**, not 403. Answering "403 Forbidden" would
        confirm that note 7 exists and belongs to someone else. 404 reveals
        nothing at all.
    """
    note = db.session.get(Note, note_id)
    if note is None or note.user_id != current_user.id:
        abort(404, description="No such note.")
    return render_template("main/note.html", note=note)


@main_bp.route("/notes/<int:note_id>/delete", methods=["POST"])
@login_required
def delete_note(note_id: int) -> WerkzeugResponse:
    """Delete one of your own notes.

    Args:
        note_id: Primary key from the URL.

    Returns:
        WerkzeugResponse: 303 redirect to the dashboard.
    """
    note = db.session.get(Note, note_id)
    if note is None or note.user_id != current_user.id:
        flash("That note no longer exists.", "warning")
    else:
        db.session.delete(note)
        db.session.commit()
        flash("Note deleted.", "info")
    return redirect(url_for("main.dashboard"), code=303)


@main_bp.route("/api/me")
@login_required
def whoami() -> Response:
    """Return the signed-in user as JSON.

    Returns:
        Response: Identity and role — and deliberately **no** password hash.

    Note:
        Never serialise ``password_hash``. Even a hash is material an attacker
        can take away and crack offline at their leisure. Build responses from
        an explicit allow-list of fields, never ``vars(user)`` or a blanket
        ``__dict__`` dump.
    """
    return jsonify(
        id=current_user.id,
        email=current_user.email,
        display_name=current_user.display_name,
        role=current_user.role,
        is_admin=current_user.is_admin,
    )


@main_bp.route("/admin")
@login_required
@admin_required
def admin_panel() -> str:
    """List every user — administrators only.

    Returns:
        str: Rendered ``main/admin.html``.
    """
    users = db.session.execute(select(User).order_by(User.created_at)).scalars().all()
    return render_template("main/admin.html", users=users)
