from pathlib import Path

CONFIG = (Path(__file__).resolve().parent.parent / "render.yaml").read_text()


def test_start_command_binds_to_render_port_without_reload():
    assert "uvicorn app.main:app --host 0.0.0.0 --port $PORT" in CONFIG
    assert "--reload" not in CONFIG


def test_health_check_and_python_version():
    assert "healthCheckPath: /healthz" in CONFIG
    assert "value: 3.11.8" in CONFIG


def test_build_installs_runtime_requirements_only():
    assert "pip install -r requirements.txt" in CONFIG
    assert "requirements-dev" not in CONFIG


def test_no_secrets_in_config():
    for word in ("secret", "password", "token", "key:  "):
        assert word not in CONFIG.lower().replace("- key:", "")
