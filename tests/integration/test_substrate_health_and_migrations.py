import json
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select

import app.main as main_module
from app.cli import SEED_SETTING_KEY, main, seed_dev
from app.config import Settings
from app.db import create_session_factory, create_sqlite_engine
from app.deps import ensure_utc
from app.main import create_app
from app.models import Camera, CapturedFrame, SystemSetting, User


def test_alembic_migrations_round_trip_and_zero_schema_drift(
    app_settings: Settings,
    alembic_config: Config,
) -> None:
    command.upgrade(alembic_config, "head")
    command.check(alembic_config)

    expected_tables = {
        "system_settings",
        "users",
        "user_sessions",
        "cameras",
        "captured_frames",
        "render_jobs",
    }

    engine = create_sqlite_engine(app_settings.db_path)
    try:
        inspector = inspect(engine)
        present_tables = set(inspector.get_table_names())
        assert expected_tables.issubset(present_tables)
    finally:
        engine.dispose()

    command.downgrade(alembic_config, "base")
    engine_after_downgrade = create_sqlite_engine(app_settings.db_path)
    try:
        inspector = inspect(engine_after_downgrade)
        remaining_tables = set(inspector.get_table_names())
        assert expected_tables.isdisjoint(remaining_tables)
    finally:
        engine_after_downgrade.dispose()

    command.upgrade(alembic_config, "head")
    command.check(alembic_config)


def test_health_endpoint_reports_ok_version_and_wal_mode(
    migrated_settings: Settings,
) -> None:
    with TestClient(create_app(migrated_settings, enable_scheduler=False)) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": migrated_settings.version,
        "database": "ok",
        "journal_mode": "wal",
    }


def test_seed_dev_is_idempotent_and_cli_entry_point_succeeds(
    migrated_settings: Settings,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_pass = migrated_settings.bootstrap_admin_password
    assert admin_pass is not None

    seed_dev(migrated_settings)
    first_output = capsys.readouterr().out
    assert str(migrated_settings.db_path) in first_output
    assert migrated_settings.secret_key not in first_output
    assert admin_pass not in first_output

    engine = create_sqlite_engine(migrated_settings.db_path)
    session_factory = create_session_factory(engine)
    try:
        with session_factory() as session:
            first_row = session.get(SystemSetting, SEED_SETTING_KEY)
            assert first_row is not None
            first_timestamp = ensure_utc(first_row.updated_at)
            first_payload = json.loads(first_row.value_json)
            assert first_payload == {"phase": 1}

            users_first = list(session.scalars(select(User)).all())
            admin_user = session.scalar(select(User).where(User.username == "admin"))
            assert admin_user is not None
            assert admin_user.role == "admin"
            assert admin_user.is_active is True

            cameras_first = list(session.scalars(select(Camera)).all())
            seeded_cam = session.scalar(select(Camera).where(Camera.slug == "front-courtyard"))
            assert seeded_cam is not None
            assert seeded_cam.source_type == "synthetic"
            assert seeded_cam.is_enabled is True

            frames_first = list(
                session.scalars(
                    select(CapturedFrame)
                    .where(CapturedFrame.camera_id == seeded_cam.id)
                    .order_by(CapturedFrame.captured_at_utc.asc())
                ).all()
            )
            assert len(frames_first) >= 2
            first_frame_ids = [frame.id for frame in frames_first]

            # Verify seeded archive spans a DST offset transition and files exist on disk
            cam_zone = ZoneInfo(seeded_cam.timezone)
            observed_offsets = {
                ensure_utc(frame.captured_at_utc).astimezone(cam_zone).utcoffset()
                for frame in frames_first
            }
            assert len(observed_offsets) >= 2
            assert (migrated_settings.photos_dir / frames_first[0].original_rel_path).is_file()
            assert (migrated_settings.previews_dir / frames_first[0].preview_rel_path).is_file()

            setting_keys_first = {s.key for s in session.scalars(select(SystemSetting)).all()}

        # Second run must be 100% idempotent (zero duplicated users, cameras, or frames)
        seed_dev(migrated_settings)
        capsys.readouterr()

        with session_factory() as session:
            second_row = session.get(SystemSetting, SEED_SETTING_KEY)
            assert second_row is not None
            second_payload = json.loads(second_row.value_json)
            assert second_payload == {"phase": 1}
            assert ensure_utc(second_row.updated_at) >= first_timestamp

            users_second = list(session.scalars(select(User)).all())
            cameras_second = list(session.scalars(select(Camera)).all())
            frames_second = list(
                session.scalars(
                    select(CapturedFrame)
                    .where(CapturedFrame.camera_id == seeded_cam.id)
                    .order_by(CapturedFrame.captured_at_utc.asc())
                ).all()
            )
            setting_keys_second = {s.key for s in session.scalars(select(SystemSetting)).all()}

            assert len(users_second) == len(users_first)
            assert len(cameras_second) == len(cameras_first)
            assert [frame.id for frame in frames_second] == first_frame_ids
            assert setting_keys_second == setting_keys_first
    finally:
        engine.dispose()

    monkeypatch.setenv("APP_SECRET_KEY", migrated_settings.secret_key)
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", admin_pass)
    monkeypatch.setenv("TIMELAPSE_DATA_DIR", str(migrated_settings.data_dir))
    assert main(["seed-dev"]) == 0
    cli_out = capsys.readouterr().out
    assert migrated_settings.secret_key not in cli_out
    assert admin_pass not in cli_out


def test_spa_static_serving_and_api_not_found_boundary(
    migrated_settings: Settings,
) -> None:
    dist_dir = migrated_settings.frontend_dist_dir
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    index_html = "<!doctype html><html><body><div id='root'>Time-Lapse</div></body></html>"
    asset_js = "console.log('substrate-ready');"
    favicon_svg = "<svg xmlns='http://www.w3.org/2000/svg'></svg>"

    (dist_dir / "index.html").write_text(index_html, encoding="utf-8")
    (assets_dir / "index.js").write_text(asset_js, encoding="utf-8")
    (dist_dir / "favicon.svg").write_text(favicon_svg, encoding="utf-8")

    with TestClient(create_app(migrated_settings, enable_scheduler=False)) as client:
        root_resp = client.get("/")
        assert root_resp.status_code == 200
        assert root_resp.text == index_html

        route_resp = client.get("/cameras/front-yard")
        assert route_resp.status_code == 200
        assert route_resp.text == index_html

        asset_resp = client.get("/assets/index.js")
        assert asset_resp.status_code == 200
        assert asset_resp.text == asset_js

        favicon_resp = client.get("/favicon.svg")
        assert favicon_resp.status_code == 200
        assert favicon_resp.text == favicon_svg

        missing_api_resp = client.get("/api/unknown")
        assert missing_api_resp.status_code == 404
        assert missing_api_resp.json() == {"detail": "Not Found"}


def test_lazy_module_app_attribute_and_unknown_attribute(
    migrated_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_SECRET_KEY", migrated_settings.secret_key)
    monkeypatch.setenv("TIMELAPSE_DATA_DIR", str(migrated_settings.data_dir))

    lazy_app = main_module.app
    assert isinstance(lazy_app, FastAPI)
    lazy_app.state.engine.dispose()

    with pytest.raises(AttributeError):
        _ = main_module.nonexistent_attribute
