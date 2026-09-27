"""Follow-up: speed up active ticket lists and comment lookups."""

from alembic import op

revision = "0002_ticket_indexes"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_tickets_tenant_active_created", "tickets", ["tenant_id", "deleted_at", "created_at"]
    )
    op.create_index("ix_comments_tenant_ticket", "comments", ["tenant_id", "ticket_id"])


def downgrade():
    op.drop_index("ix_comments_tenant_ticket", table_name="comments")
    op.drop_index("ix_tickets_tenant_active_created", table_name="tickets")
