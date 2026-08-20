"""
Day 16 — File Uploads and Media.
================================

Real-world scenario
-------------------
A document vault: users upload images and PDFs, get thumbnails, and download
them again. It is an ordinary feature, and it is the point at which an untrusted
party writes bytes to your disk and chooses their name.

What you will learn
-------------------
1. ``enctype="multipart/form-data"`` and ``request.files``.
2. ``MAX_CONTENT_LENGTH`` — rejecting a huge body **before** it is buffered.
3. ``secure_filename`` — what it does, and why it is **not enough**.
4. **Content sniffing**: never trust the declared type or the extension.
5. Storing files **outside** ``static/`` and serving them through a view.
6. **Decompression bombs**, and Pillow's guard.
7. Thumbnails with Pillow, including EXIF stripping.
8. Why SVG is deliberately not accepted.

How to run
----------
From the repository root::

    source .venv/bin/activate
    export FLASK_APP=16_file_uploads_and_media/wsgi.py
    flask run --port 5016 --debug

The rule that matters most
--------------------------
**Never build a filesystem path from user-supplied text.** Generate a random
name, keep theirs only as a label, and store the file where the web server
cannot serve it directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import click
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask.cli import with_appcontext
from flask.typing import ResponseReturnValue
from sqlalchemy import select

from .extensions import csrf, db
from .storage import (
    MAX_UPLOAD_BYTES,
    UploadRejected,
    delete_files,
    store_upload,
)


def create_app(config_name: str = "development") -> Flask:
    """Build the document-vault application.

    Args:
        config_name: ``"development"`` or ``"testing"``.

    Returns:
        Flask: A configured application.
    """
    app = Flask(__name__, instance_relative_config=True)
    instance_dir = Path(app.instance_path)
    instance_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Upload directories live OUTSIDE static/
    # -------------------------------------------------------------------------
    # If uploads sat in static/, the web server would serve them directly — with
    # whatever content type it inferred, to anybody who guessed a URL, with no
    # authorisation check possible. An uploaded .html or .svg would then execute
    # in YOUR origin: stored XSS with access to your users' cookies.
    #
    # Keeping them outside means every read goes through a view function, where
    # you control the headers and can check permissions.
    upload_root = Path(__file__).resolve().parent.parent / "uploads"
    originals_dir = upload_root / "originals"
    thumbs_dir = upload_root / "thumbs"
    for directory in (originals_dir, thumbs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-not-for-production"),
        SQLALCHEMY_DATABASE_URI=(
            "sqlite:///:memory:" if config_name == "testing"
            else f"sqlite:///{instance_dir / 'vault.db'}"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        TESTING=config_name == "testing",
        WTF_CSRF_ENABLED=config_name != "testing",

        # The OUTER guard. Werkzeug aborts with 413 once the request body
        # exceeds this, WITHOUT buffering the rest — so a 4 GB upload cannot
        # exhaust your memory or disk. Set it slightly above your per-file limit
        # to leave room for multipart overhead and other form fields.
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES + (512 * 1024),

        ORIGINALS_DIR=originals_dir,
        THUMBS_DIR=thumbs_dir,
    )

    db.init_app(app)
    csrf.init_app(app)

    from .models import Upload  # noqa: F401

    with app.app_context():
        db.create_all()

    _register_routes(app)
    _register_commands(app)

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        """Expose shared values to every template.

        Returns:
            dict[str, Any]: Template globals.
        """
        return {"app_name": "Vault", "max_mb": MAX_UPLOAD_BYTES // 1024 // 1024}

    return app


def _register_routes(app: Flask) -> None:
    """Register the vault's views.

    Args:
        app: The application being built.
    """
    from .models import Upload

    @app.route("/", methods=["GET", "POST"])
    def index() -> ResponseReturnValue:
        """List uploads and accept new ones.

        Returns:
            ResponseReturnValue: The rendered page, or a 303 redirect after an
            upload.

        Note:
            ``request.files`` is populated only when the form declares
            ``enctype="multipart/form-data"``. Omit that attribute and the
            browser sends filenames as ordinary text fields — ``request.files``
            is empty and ``request.form`` contains the *name*, not the file.
            That is the single most common upload bug.
        """
        if request.method == "POST":
            # .get() rather than ["file"]: a missing key would abort with 400
            # before you can give a helpful message.
            uploaded = request.files.get("file")

            try:
                stored = store_upload(
                    uploaded,  # type: ignore[arg-type]
                    originals_dir=app.config["ORIGINALS_DIR"],
                    thumbs_dir=app.config["THUMBS_DIR"],
                )
            except UploadRejected as rejection:
                flash(rejection.reason, "error")
                # 422: the request was well-formed, the content was not
                # acceptable.
                return render_template("index.html", uploads=_all_uploads()), 422

            db.session.add(Upload(
                original_name=stored.original_name,
                stored_name=stored.stored_name,
                content_type=stored.content_type,
                size_bytes=stored.size_bytes,
                width=stored.width,
                height=stored.height,
                thumb_name=stored.thumb_name,
            ))
            db.session.commit()
            flash(f"Uploaded {stored.original_name} ({stored.content_type}).", "success")
            return redirect(url_for("index"), code=303)

        return render_template("index.html", uploads=_all_uploads())

    def _all_uploads() -> list[Upload]:
        """Return every upload, newest first.

        Returns:
            list[Upload]: Stored uploads.
        """
        return list(db.session.execute(
            select(Upload).order_by(Upload.uploaded_at.desc())
        ).scalars().all())

    @app.get("/files/<int:upload_id>")
    def download(upload_id: int) -> ResponseReturnValue:
        """Serve an original file through a view.

        Args:
            upload_id: Primary key from the URL.

        Returns:
            ResponseReturnValue: The file as a download.

        Note:
            Three deliberate choices:

            1. **Look up by database id, not by filename.** The client never
               names a path, so path traversal is structurally impossible.
            2. ``send_from_directory`` resolves the path and refuses anything
               outside the directory — a second line of defence.
            3. ``as_attachment=True`` sends ``Content-Disposition: attachment``,
               so the browser **saves** the file rather than rendering it. That
               single header neutralises an uploaded HTML or SVG file, because
               it is never executed in your origin.

            ``download_name`` gives the user back *their* filename without that
            name ever touching the filesystem.
        """
        upload = db.session.get(Upload, upload_id)
        if upload is None:
            abort(404, description="No such file.")

        response = send_from_directory(
            app.config["ORIGINALS_DIR"],
            upload.stored_name,
            as_attachment=True,
            download_name=upload.original_name,
        )
        # Stop a browser from second-guessing the type we declared.
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.get("/thumbs/<int:upload_id>")
    def thumbnail(upload_id: int) -> ResponseReturnValue:
        """Serve a generated thumbnail inline.

        Args:
            upload_id: Primary key from the URL.

        Returns:
            ResponseReturnValue: The JPEG thumbnail.

        Note:
            Served **inline** (not as an attachment) because it must render in
            an ``<img>``. That is safe here only because *we* generated this
            file: it is a re-encoded JPEG produced by Pillow, not whatever the
            user uploaded. Never serve an untrusted file inline.
        """
        upload = db.session.get(Upload, upload_id)
        if upload is None or not upload.thumb_name:
            abort(404, description="No thumbnail.")

        response = send_from_directory(app.config["THUMBS_DIR"], upload.thumb_name)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "private, max-age=3600"
        return response

    @app.post("/files/<int:upload_id>/delete")
    def delete(upload_id: int) -> ResponseReturnValue:
        """Delete an upload and its files.

        Args:
            upload_id: Primary key from the URL.

        Returns:
            ResponseReturnValue: 303 redirect to the list.

        Note:
            Files are removed **before** the row is committed. The row is the
            index of what exists; an orphaned file is invisible waste, whereas a
            row pointing at a missing file produces a broken page. Neither is
            ideal — the two stores cannot share a transaction — so prefer the
            failure mode you can detect and clean up.
        """
        upload = db.session.get(Upload, upload_id)
        if upload is None:
            flash("That file no longer exists.", "warning")
            return redirect(url_for("index"), code=303)

        delete_files(
            upload.stored_name, upload.thumb_name,
            originals_dir=app.config["ORIGINALS_DIR"],
            thumbs_dir=app.config["THUMBS_DIR"],
        )
        db.session.delete(upload)
        db.session.commit()
        flash("File deleted.", "info")
        return redirect(url_for("index"), code=303)

    @app.get("/api/files")
    def api_files() -> ResponseReturnValue:
        """List uploads as JSON.

        Returns:
            ResponseReturnValue: ``200`` with the upload metadata.
        """
        return jsonify(data=[upload.to_dict() for upload in _all_uploads()])

    @app.errorhandler(413)
    def too_large(error: Exception) -> ResponseReturnValue:
        """Answer a body that exceeded ``MAX_CONTENT_LENGTH``.

        Args:
            error: The ``RequestEntityTooLarge`` exception.

        Returns:
            ResponseReturnValue: A 413, as JSON or HTML depending on the client.

        Note:
            This fires *before* your view runs — Werkzeug stops reading the body.
            A friendly message matters, because the browser's default for a
            rejected upload is a blank error page.
        """
        message = f"That file is too large. The limit is {MAX_UPLOAD_BYTES // 1024 // 1024} MB."
        if request.path.startswith("/api/"):
            return jsonify(error={"code": "payload_too_large", "message": message}), 413
        return render_template("error.html", code=413, message=message), 413

    @app.errorhandler(404)
    def not_found(error: Exception) -> ResponseReturnValue:
        """Render a 404.

        Args:
            error: The exception.

        Returns:
            ResponseReturnValue: A 404 page.
        """
        return render_template("error.html", code=404,
                               message=getattr(error, "description", "Not found.")), 404


def _register_commands(app: Flask) -> None:
    """Attach CLI commands.

    Args:
        app: The application being built.
    """

    @click.command("find-orphans")
    @with_appcontext
    def find_orphans() -> None:
        """Report files on disk with no database row, and vice versa.

        Two stores that cannot share a transaction will drift. A reconciliation
        command is how you find out before the disk fills.
        """
        from .models import Upload

        known = {
            row.stored_name for row in db.session.execute(select(Upload)).scalars()
        }
        on_disk = {p.name for p in app.config["ORIGINALS_DIR"].iterdir() if p.is_file()}

        orphan_files = on_disk - known
        missing_files = known - on_disk

        click.echo(f"  rows: {len(known)}   files: {len(on_disk)}")
        click.echo(f"  files with no row : {len(orphan_files)} {sorted(orphan_files)[:5]}")
        click.echo(f"  rows with no file : {len(missing_files)} {sorted(missing_files)[:5]}")

    app.cli.add_command(find_orphans)


__all__ = ["create_app"]
