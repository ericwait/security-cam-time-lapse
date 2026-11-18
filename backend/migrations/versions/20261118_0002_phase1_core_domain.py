"""Phase 1 core domain tables for users, sessions, cameras, frames, and render jobs.

Revision ID: 20261118_0002
Revises: 20261118_0001
Create Date: 2026-11-18 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261118_0002"
down_revision: str | None = "20261118_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create Phase 1 domain tables and indexes."""
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("auth_type", sa.String(length=16), nullable=False),
        sa.Column("auth_username", sa.String(length=255), nullable=True),
        sa.Column("encrypted_secret", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_capture_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_capture_status", sa.String(length=32), nullable=True),
        sa.Column("last_capture_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cameras")),
    )
    op.create_index(op.f("ix_cameras_slug"), "cameras", ["slug"], unique=True)

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
    )
    op.create_index(
        op.f("ix_user_sessions_expires_at"),
        "user_sessions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_sessions_token_hash"),
        "user_sessions",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_user_sessions_user_id"),
        "user_sessions",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "captured_frames",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("captured_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("camera_timezone", sa.String(length=64), nullable=False),
        sa.Column("local_date", sa.String(length=10), nullable=False),
        sa.Column("original_rel_path", sa.String(length=512), nullable=False),
        sa.Column("preview_rel_path", sa.String(length=512), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_captured_frames_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_captured_frames")),
        sa.UniqueConstraint(
            "camera_id",
            "original_rel_path",
            name="uq_captured_frames_camera_orig_path",
        ),
    )
    op.create_index(
        "ix_captured_frames_camera_local_date",
        "captured_frames",
        ["camera_id", "local_date"],
        unique=False,
    )
    op.create_index(
        "ix_captured_frames_camera_utc",
        "captured_frames",
        ["camera_id", "captured_at_utc"],
        unique=False,
    )

    op.create_table(
        "render_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("camera_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("query_params_json", sa.Text(), nullable=False),
        sa.Column("fps", sa.Integer(), nullable=False),
        sa.Column("resolution_preset", sa.String(length=16), nullable=False),
        sa.Column("watermark_timestamp", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("frame_count", sa.Integer(), nullable=False),
        sa.Column("processed_frames", sa.Integer(), nullable=False),
        sa.Column("output_rel_path", sa.String(length=512), nullable=True),
        sa.Column("output_size_bytes", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.id"],
            name=op.f("fk_render_jobs_camera_id_cameras"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name=op.f("fk_render_jobs_created_by_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_render_jobs")),
    )
    op.create_index(
        op.f("ix_render_jobs_camera_id"),
        "render_jobs",
        ["camera_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_render_jobs_created_by_user_id"),
        "render_jobs",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_render_jobs_expires_at"),
        "render_jobs",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop Phase 1 domain tables and indexes in reverse dependency order."""
    op.drop_index(op.f("ix_render_jobs_expires_at"), table_name="render_jobs")
    op.drop_index(op.f("ix_render_jobs_created_by_user_id"), table_name="render_jobs")
    op.drop_index(op.f("ix_render_jobs_camera_id"), table_name="render_jobs")
    op.drop_table("render_jobs")

    op.drop_index("ix_captured_frames_camera_utc", table_name="captured_frames")
    op.drop_index("ix_captured_frames_camera_local_date", table_name="captured_frames")
    op.drop_table("captured_frames")

    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_token_hash"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_expires_at"), table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_index(op.f("ix_cameras_slug"), table_name="cameras")
    op.drop_table("cameras")

    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
