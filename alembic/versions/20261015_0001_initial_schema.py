"""Baseline schema for new and existing deployments.

Existing tables are left intact; missing tables are created. This permits a first
migration run against databases initialized by older releases without dropping data.
Revision ID: 20261015_0001
Revises:
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from pgvector.sqlalchemy import Vector

revision = "20261015_0001"
down_revision = None
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    if not _has_table("customers"):
        op.create_table(
            "customers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    if not _has_table("knowledge_articles"):
        op.create_table(
            "knowledge_articles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("title", sa.String(), nullable=True),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("embedding", Vector(1536), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    if not _has_table("orders"):
        op.create_table(
            "orders",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id")),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    if not _has_table("support_tickets"):
        op.create_table(
            "support_tickets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id")),
            sa.Column("issue", sa.Text(), nullable=True),
            sa.Column("priority", sa.String(), nullable=True),
            sa.Column("status", sa.String(), server_default="OPEN"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    if not _has_table("notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id")),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), server_default="SENT"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    if not _has_table("tasks"):
        op.create_table(
            "tasks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_request", sa.Text(), nullable=True),
            sa.Column("intent", sa.String(), nullable=True),
            sa.Column("parameters", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), server_default="PENDING"),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("retry_count", sa.Integer(), server_default="0"),
            sa.Column("requires_human", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_table("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id")),
            sa.Column("action", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        )
    if not _has_table("idempotency_records"):
        op.create_table(
            "idempotency_records",
            sa.Column("key", sa.String(128), primary_key=True),
            sa.Column("request_hash", sa.String(64), nullable=False),
            sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        )
    if not _has_table("task_owners"):
        op.create_table(
            "task_owners",
            sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), primary_key=True),
            sa.Column("owner_id", sa.String(128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        )

    # Add missing indexes without duplicating indexes from pre-migration schemas.
    index_specs = {
        "customers": [("ix_customers_name", ["name"], False), ("ix_customers_email", ["email"], True)],
        "knowledge_articles": [("ix_knowledge_articles_id", ["id"], False)],
        "orders": [("ix_orders_id", ["id"], False)],
        "support_tickets": [("ix_support_tickets_id", ["id"], False)],
        "notifications": [("ix_notifications_id", ["id"], False)],
        "tasks": [("ix_tasks_id", ["id"], False)],
        "audit_logs": [("ix_audit_logs_id", ["id"], False)],
        "task_owners": [("ix_task_owners_owner_id", ["owner_id"], False)],
    }
    for table, specs in index_specs.items():
        existing = {idx["name"] for idx in inspect(bind).get_indexes(table)}
        for name, columns, unique in specs:
            if name not in existing:
                op.create_index(name, table, columns, unique=unique)


def downgrade() -> None:
    # This is a compatibility baseline and may have skipped pre-existing tables.
    # Dropping all of them on downgrade could destroy production data; use forward migrations.
    pass
