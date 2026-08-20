"""
Day 08 — Extensions: the cure for circular imports.
===================================================

The problem this file exists to solve
-------------------------------------
The obvious layout fails::

    # app.py
    from models import Product          # app.py needs the models
    db = SQLAlchemy(app)

    # models.py
    from app import db                  # models.py needs db  -> CIRCULAR IMPORT

Python cannot resolve that: importing ``app`` starts executing ``app.py``, which
imports ``models``, which imports ``app`` again — still half-initialised — and
you get ``ImportError: cannot import name 'db' from partially initialized
module``. Every Flask developer meets this exactly once.

The fix
-------
Create the extension objects in a module that imports **nothing** from your
application, then have both sides import from here::

    extensions.py   db = SQLAlchemy()        <- no app, no models
    models.py       from extensions import db
    app.py          from extensions import db; db.init_app(app)

This is the **deferred initialisation** or ``init_app`` pattern, and it works
for every Flask extension: Migrate, LoginManager, Cache, Limiter, Mail. Day 10
generalises it into a full application factory.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase

from flask_sqlalchemy import SQLAlchemy


class Base(DeclarativeBase):
    """Declarative base for every model in this application.

    Subclassing :class:`~sqlalchemy.orm.DeclarativeBase` explicitly is the
    SQLAlchemy 2.0 way. It gives you one place to add behaviour shared by all
    models (naming conventions, common columns, serialisation helpers) instead
    of repeating it per class.
    """


# Created WITHOUT an app. It is inert until `db.init_app(app)` runs, which is
# precisely what lets models.py import it at module level without a cycle.
db = SQLAlchemy(model_class=Base)
