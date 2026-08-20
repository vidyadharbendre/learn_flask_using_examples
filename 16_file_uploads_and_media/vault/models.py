"""
Day 16 — Models: uploaded file metadata.
========================================

Note what is stored and what is not: the **row** holds metadata, the **disk**
holds bytes. Storing file contents in a database column is possible and almost
always wrong — it bloats backups, defeats HTTP caching, and pushes every byte
through your application process.

Two filenames are recorded, and the distinction matters:

``original_name``
    What the user called it. Shown in the UI, offered on download. **Never**
    used to build a path.
``stored_name``
    A random name we chose. This is what exists on disk, so a hostile filename
    cannot influence where anything is written.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .extensions import db


class Upload(db.Model):
    """One uploaded file.

    Attributes:
        id: Surrogate primary key.
        original_name: The user's filename, sanitised for display only.
        stored_name: The random name actually used on disk.
        content_type: The type **we detected**, not the one the client claimed.
        size_bytes: Size on disk.
        width / height: Image dimensions, when applicable.
        thumb_name: Generated thumbnail filename, when applicable.
        uploaded_at: Timestamp.
    """

    __tablename__ = "uploads"

    id: Mapped[int] = mapped_column(primary_key=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False, default=0)
    width: Mapped[int | None] = mapped_column(nullable=True)
    height: Mapped[int | None] = mapped_column(nullable=True)
    thumb_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def is_image(self) -> bool:
        """Whether this upload is an image.

        Returns:
            bool: True for image content types.
        """
        return self.content_type.startswith("image/")

    @property
    def human_size(self) -> str:
        """Size in human-readable units.

        Returns:
            str: e.g. ``"1.4 MB"``.
        """
        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the API.

        Returns:
            dict[str, Any]: JSON-safe metadata. ``stored_name`` is included for
            teaching purposes; a production API would usually expose only the
            id and a download URL.
        """
        return {
            "id": self.id,
            "original_name": self.original_name,
            "stored_name": self.stored_name,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "dimensions": (
                {"width": self.width, "height": self.height} if self.width else None
            ),
            "uploaded_at": self.uploaded_at.isoformat(),
        }
