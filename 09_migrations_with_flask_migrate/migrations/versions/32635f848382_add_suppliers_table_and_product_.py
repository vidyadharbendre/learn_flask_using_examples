"""add suppliers table and product.supplier_id

Revision ID: 32635f848382
Revises: 7ad7e54df8a1
Create Date: 2026-08-20 18:22:43.595774

-------------------------------------------------------------------------------
EDITED BY HAND. Autogenerate drafted it; a human made it correct and safe.
-------------------------------------------------------------------------------

What was changed and why:

1. **The foreign key was unnamed** (``create_foreign_key(None, ...)``), so the
   generated ``downgrade`` said ``drop_constraint(None, type_='foreignkey')``.
   Same bug as the previous revision: you cannot drop by a name you never gave.
   Named it ``fk_products_supplier_id``.

2. **Added a data migration.** Creating an empty ``suppliers`` table and a
   ``supplier_id`` column that is NULL for every row is only half a change. This
   revision inserts a placeholder supplier and points existing products at it,
   so the application has something coherent to render immediately after deploy.

3. **The column is nullable, deliberately.** A ``NOT NULL`` foreign key cannot
   be added to a populated table in one step — there is no legal value for the
   existing rows. The safe production sequence is three deploys:

       (a) add the column as NULLABLE          <- this migration
       (b) backfill it, and ship application code that always sets it
       (c) a later migration flips it to NOT NULL, once no NULLs remain

   Trying to do all three at once is the classic way to take a site down.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "32635f848382"
down_revision = "7ad7e54df8a1"
branch_labels = None
depends_on = None

FK_NAME = "fk_products_supplier_id"


def upgrade() -> None:
    """Create ``suppliers``, add the FK column, and backfill it."""
    op.create_table(
        "suppliers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=160), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_suppliers"),
        sa.UniqueConstraint("name", name="uq_suppliers_name"),
    )

    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.add_column(sa.Column("supplier_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_products_supplier_id"), ["supplier_id"], unique=False
        )
        # Named, so downgrade() can drop it.
        batch_op.create_foreign_key(
            FK_NAME, "suppliers", ["supplier_id"], ["id"], ondelete="SET NULL"
        )

    # -------------------------------------------------------------------------
    # DATA MIGRATION
    # -------------------------------------------------------------------------
    # `sa.table` / `sa.column` declare a MINIMAL, INLINE view of the table —
    # just enough columns for this statement. This is the recommended pattern
    # for data migrations, and it is why we do NOT import models.Supplier:
    #
    #   models.py describes the schema as it is TODAY. A migration must describe
    #   the schema as it was at THIS POINT in history. Import the live model and
    #   this file breaks the moment someone adds a column to Supplier, because
    #   the generated INSERT would reference a column that does not exist yet
    #   when replaying the history from scratch.
    suppliers = sa.table(
        "suppliers",
        sa.column("id", sa.Integer),
        sa.column("name", sa.String),
        sa.column("email", sa.String),
    )
    op.bulk_insert(
        suppliers,
        [{"id": 1, "name": "Unassigned", "email": "purchasing@example.com"}],
    )

    # Point every existing product at the placeholder so nothing renders as a
    # blank supplier on the first page load after deployment.
    op.execute("UPDATE products SET supplier_id = 1 WHERE supplier_id IS NULL")


def downgrade() -> None:
    """Drop the column and the table, in dependency order.

    Order matters: the foreign key must go before the table it references,
    or the database refuses the drop.
    """
    with op.batch_alter_table("products", schema=None) as batch_op:
        batch_op.drop_constraint(FK_NAME, type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_products_supplier_id"))
        batch_op.drop_column("supplier_id")

    op.drop_table("suppliers")
