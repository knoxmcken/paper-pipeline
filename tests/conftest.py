import pytest


@pytest.fixture(autouse=True)
def isolated_project_registry(tmp_path, monkeypatch):
    """Keep every test away from the real ~/.config/paperpipe registry and $PAPERPIPE_DATA."""
    monkeypatch.setenv("PAPERPIPE_PROJECTS", str(tmp_path / "registry" / "projects.json"))
    monkeypatch.delenv("PAPERPIPE_DATA", raising=False)
