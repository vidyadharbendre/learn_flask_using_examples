"""
Day 16 — Accepting files from strangers, safely.
================================================

An upload endpoint is the point where an untrusted party writes bytes to your
disk and chooses their name. Almost every rule below exists because someone once
skipped it.

The five questions every upload must answer
-------------------------------------------
1. **How big?**       → a limit enforced *before* the body is buffered
2. **What is it?**    → sniff the content; never believe the client
3. **Where does it go?** → a name *you* chose, in a directory outside the web root
4. **Who may read it?** → serve through a view, not by handing out a path
5. **What if it is hostile?** → SVG, decompression bombs, executable content

Filenames are the classic attack surface::

    "../../../../etc/passwd"          path traversal
    "../../app/routes.py"             overwrite your own code
    "invoice.pdf.exe"                 double extension
    "CON", "NUL", "PRN"               reserved Windows device names
    "\\x00.jpg"                       null-byte truncation
    "logo.svg"                        contains <script> — stored XSS

The defence that beats all of them is simple: **never build a path from
user-supplied text.** Generate a random name and keep theirs only as a label.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Final

from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

# -----------------------------------------------------------------------------
# What we accept
# -----------------------------------------------------------------------------
# An ALLOW-LIST, mapping the detected format to a canonical extension. A
# deny-list ("everything except .exe") is unwinnable: you cannot enumerate every
# dangerous type, and new ones appear.
#
# NOTE what is missing: **SVG**. An SVG is XML that may contain <script>, so
# serving a user-supplied one from your domain is stored XSS. If you must accept
# SVG, sanitise it (e.g. with a library like bleach on the XML), serve it from a
# separate origin, and never inline it.
ALLOWED_IMAGE_FORMATS: Final[dict[str, str]] = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "GIF": ".gif",
    "WEBP": ".webp",
}

ALLOWED_DOCUMENT_TYPES: Final[dict[bytes, tuple[str, str]]] = {
    # magic bytes -> (content type, extension)
    b"%PDF-": ("application/pdf", ".pdf"),
}

MAX_UPLOAD_BYTES: Final[int] = 5 * 1024 * 1024      # 5 MB
MAX_IMAGE_PIXELS: Final[int] = 40_000_000           # ~40 megapixels
THUMB_SIZE: Final[tuple[int, int]] = (320, 320)

# Pillow's own guard against decompression bombs. A 10 KB PNG can legitimately
# declare dimensions of 50000x50000, which would allocate gigabytes when
# decoded — a denial of service costing the attacker almost nothing. Pillow
# warns above this and refuses at twice the value.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


class UploadRejected(Exception):
    """Raised when a file must not be accepted.

    Attributes:
        reason: A message safe to show the user.
    """

    def __init__(self, reason: str) -> None:
        """Initialise the error.

        Args:
            reason: Human-readable explanation.
        """
        super().__init__(reason)
        self.reason = reason


@dataclass
class StoredFile:
    """The result of accepting an upload.

    Attributes:
        original_name: Sanitised display name.
        stored_name: The random name on disk.
        content_type: The detected type.
        size_bytes: Size on disk.
        width / height: Image dimensions, if applicable.
        thumb_name: Thumbnail filename, if generated.
    """

    original_name: str
    stored_name: str
    content_type: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    thumb_name: str | None = None


def safe_display_name(raw: str | None) -> str:
    """Sanitise a client-supplied filename **for display only**.

    Args:
        raw: The filename as sent by the browser.

    Returns:
        str: A safe label, never empty.

    Note:
        ``secure_filename`` strips directory components, normalises unicode, and
        removes characters that are dangerous on any filesystem::

            secure_filename("../../etc/passwd")  -> "etc_passwd"
            secure_filename("my photo.jpg")      -> "my_photo.jpg"
            secure_filename("../../../☃.png")    -> "png"

        **But it is not sufficient on its own.** Two important gaps:

        1. It can return an **empty string** (a name of only unsafe characters),
           and ``open(directory / "")`` does not do what you want.
        2. It does not prevent one user **overwriting another's** file of the
           same name.

        That is why this function is used only for the label, and
        :func:`generate_stored_name` decides the actual path.
    """
    cleaned = secure_filename(raw or "")
    if not cleaned:
        # Never let a hostile name collapse to nothing.
        cleaned = f"upload-{secrets.token_hex(4)}"
    return cleaned[:255]


def generate_stored_name(extension: str) -> str:
    """Return a random, collision-free filename.

    Args:
        extension: The canonical extension, including the dot.

    Returns:
        str: e.g. ``"a3f1c8d0e4b74e9fbb6a1f7c1d2e3f40.jpg"``.

    Note:
        This single decision defeats path traversal, overwrites, double
        extensions, reserved device names and null-byte tricks **at once** —
        because none of the user's text reaches the filesystem.

        The extension comes from what we *detected*, not from what the user
        typed, so ``invoice.pdf.exe`` containing a PNG is stored as ``.png``.
    """
    return f"{uuid.uuid4().hex}{extension}"


def detect_type(stream: IO[bytes]) -> tuple[str, str, int | None, int | None]:
    """Determine a file's real type by inspecting its **contents**.

    Args:
        stream: The uploaded file's stream, positioned anywhere.

    Returns:
        tuple[str, str, int | None, int | None]: content type, canonical
        extension, width and height (the last two ``None`` for non-images).

    Raises:
        UploadRejected: when the content is not an allowed type.

    Note:
        **Never trust ``file.content_type`` or the file extension.** Both are
        supplied by the client and both are trivially forged::

            curl -F "file=@shell.php;type=image/png" …

        Type is a property of the *bytes*. Here images are identified by asking
        Pillow to parse them, and PDFs by their magic-byte prefix.

        ``Image.verify()`` checks structural integrity without decoding the
        whole image, which is exactly what you want before committing memory to
        a file a stranger sent you. It also consumes the file object, hence the
        reopen below.
    """
    stream.seek(0)
    header = stream.read(8)
    stream.seek(0)

    # ---- documents, by magic bytes -----------------------------------------
    for magic, (content_type, extension) in ALLOWED_DOCUMENT_TYPES.items():
        if header.startswith(magic):
            return content_type, extension, None, None

    # ---- images, by actually parsing them ----------------------------------
    try:
        with Image.open(stream) as image:
            image_format = image.format or ""
            width, height = image.size
            # verify() detects truncation and corruption. It must be the LAST
            # operation on this Image object — the file is unusable afterwards.
            image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise UploadRejected(
            "That file is not a supported image or PDF, or it is corrupt."
        ) from error
    finally:
        stream.seek(0)

    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise UploadRejected(
            f"{image_format or 'That format'} is not accepted. "
            f"Allowed: {', '.join(sorted(ALLOWED_IMAGE_FORMATS))} or PDF."
        )

    if width * height > MAX_IMAGE_PIXELS:
        # A decompression bomb: tiny on disk, enormous when decoded.
        raise UploadRejected("That image's dimensions are unreasonably large.")

    return f"image/{image_format.lower()}", ALLOWED_IMAGE_FORMATS[image_format], width, height


def store_upload(
    file: FileStorage, *, originals_dir: Path, thumbs_dir: Path
) -> StoredFile:
    """Validate and persist an uploaded file.

    Args:
        file: The ``FileStorage`` from ``request.files``.
        originals_dir: Directory for original files.
        thumbs_dir: Directory for generated thumbnails.

    Returns:
        StoredFile: Metadata describing what was stored.

    Raises:
        UploadRejected: when the file is empty, too large, or not an allowed
            type.

    Note:
        Order matters. Cheap checks first, expensive ones last: an empty-file
        check costs nothing, a size check costs a seek, and parsing the image
        costs real CPU. Do not decode a 5 MB file before noticing it is empty.
    """
    if not file or not file.filename:
        raise UploadRejected("No file was selected.")

    # ---- 1. size, measured from the STREAM ----------------------------------
    # Never trust a Content-Length header or a client-declared size. Seek to the
    # end and ask. (MAX_CONTENT_LENGTH in the app config is the outer guard that
    # rejects an oversized body before it is ever buffered; this is the inner,
    # per-file check.)
    file.stream.seek(0, 2)
    size = file.stream.tell()
    file.stream.seek(0)

    if size == 0:
        raise UploadRejected("That file is empty.")
    if size > MAX_UPLOAD_BYTES:
        raise UploadRejected(
            f"That file is {size / 1024 / 1024:.1f} MB; the limit is "
            f"{MAX_UPLOAD_BYTES / 1024 / 1024:.0f} MB."
        )

    # ---- 2. what IS it? -----------------------------------------------------
    content_type, extension, width, height = detect_type(file.stream)

    # ---- 3. where does it go? ----------------------------------------------
    stored_name = generate_stored_name(extension)
    destination = originals_dir / stored_name

    # Belt and braces: confirm the resolved path is genuinely inside the target
    # directory. It cannot escape given a uuid4 name, but this assertion is
    # cheap and would catch a future refactor that reintroduces user input.
    originals_dir.mkdir(parents=True, exist_ok=True)
    if originals_dir.resolve() not in destination.resolve().parents:
        raise UploadRejected("Refusing to write outside the upload directory.")

    file.stream.seek(0)
    file.save(destination)

    # ---- 4. derived artefacts ----------------------------------------------
    thumb_name: str | None = None
    if content_type.startswith("image/"):
        thumb_name = _make_thumbnail(destination, thumbs_dir, stored_name)

    return StoredFile(
        original_name=safe_display_name(file.filename),
        stored_name=stored_name,
        content_type=content_type,
        size_bytes=destination.stat().st_size,
        width=width,
        height=height,
        thumb_name=thumb_name,
    )


def _make_thumbnail(source: Path, thumbs_dir: Path, stored_name: str) -> str | None:
    """Generate a thumbnail beside the original.

    Args:
        source: Path to the stored original.
        thumbs_dir: Directory for thumbnails.
        stored_name: The original's stored filename.

    Returns:
        str | None: The thumbnail filename, or ``None`` if generation failed.

    Note:
        ``Image.thumbnail()`` resizes **in place, preserving aspect ratio**, and
        never enlarges a smaller image. It is the right call for previews;
        ``resize()`` would distort anything not matching the target ratio.

        Thumbnails are written as JPEG regardless of input format, which
        normalises them and — importantly — **strips metadata**, including EXIF
        GPS coordinates. Serving a user's original holiday photo with its
        location intact is a real privacy leak.

        A failure here is logged and swallowed: a missing preview is a cosmetic
        problem, and it must not lose an upload the user already made.
    """
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    thumb_name = f"{Path(stored_name).stem}.jpg"

    try:
        with Image.open(source) as opened:
            # `opened` is an ImageFile; convert() returns a plain Image, so the
            # working value gets its own name rather than being reassigned —
            # mypy rejects narrowing ImageFile to Image, and the separate name
            # is clearer anyway.
            image: Image.Image = opened

            # Flatten transparency onto white; JPEG has no alpha channel, and
            # saving an RGBA image as JPEG raises OSError.
            if image.mode in {"RGBA", "LA", "P"}:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")

            image.thumbnail(THUMB_SIZE)
            image.save(thumbs_dir / thumb_name, "JPEG", quality=82, optimize=True)
    except (OSError, ValueError):
        return None

    return thumb_name


def delete_files(stored_name: str, thumb_name: str | None, *,
                 originals_dir: Path, thumbs_dir: Path) -> None:
    """Remove an upload's files from disk.

    Args:
        stored_name: The original's filename.
        thumb_name: The thumbnail's filename, if any.
        originals_dir: Directory holding originals.
        thumbs_dir: Directory holding thumbnails.

    Note:
        Deleting the database row without deleting the file leaves an orphan
        that nothing references and nothing will ever clean up. Over years, that
        is how a disk fills with files nobody can identify.

        ``missing_ok=True`` makes this idempotent — deleting twice is not an
        error, which matters because the row and the file are not in a single
        transaction. If the process dies between the two, a retry must succeed.
    """
    (originals_dir / stored_name).unlink(missing_ok=True)
    if thumb_name:
        (thumbs_dir / thumb_name).unlink(missing_ok=True)
