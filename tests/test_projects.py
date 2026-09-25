import json
from pathlib import Path

import pytest

from paperpipe import cli, projects


def test_add_list_use_remove_round_trip(tmp_path):
    entry = projects.add("lit-review", tmp_path / "lit", "survey for chapter 2")
    assert entry["path"] == str((tmp_path / "lit").resolve())
    assert projects.current() is None
    projects.use("lit-review")
    assert projects.current() == "lit-review"
    assert projects.path_of("lit-review") == (tmp_path / "lit").resolve()

    projects.remove("lit-review")
    assert projects.load() == {"current": None, "projects": {}}


@pytest.mark.parametrize("name", ["", "has space", "../escape", "-leading", "x" * 65])
def test_invalid_names_are_refused(tmp_path, name):
    with pytest.raises(projects.ProjectError):
        projects.add(name, tmp_path)


def test_duplicate_and_unknown_names(tmp_path):
    projects.add("a", tmp_path / "a")
    with pytest.raises(projects.ProjectError, match="already exists"):
        projects.add("a", tmp_path / "b")
    for call in (projects.use, projects.remove, projects.path_of):
        with pytest.raises(projects.ProjectError, match="unknown project"):
            call("nope")


def test_corrupt_registry_is_reported_not_overwritten(tmp_path):
    path = projects.registry_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    with pytest.raises(projects.ProjectError, match="not valid JSON"):
        projects.load()
    assert path.read_text() == "{not json"


def _data_dir(*argv):
    return cli._data_dir(cli.build_parser().parse_args([*argv, "stats"]))


def test_data_dir_resolution_order(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _data_dir() == Path("data")
    projects.add("one", tmp_path / "one")
    projects.add("two", tmp_path / "two")
    projects.use("one")
    assert _data_dir() == (tmp_path / "one").resolve()
    monkeypatch.setenv("PAPERPIPE_DATA", str(tmp_path / "env"))
    assert _data_dir() == tmp_path / "env"
    assert _data_dir("--project", "two") == (tmp_path / "two").resolve()
    assert _data_dir("--project", "two", "--data-dir", "explicit") == Path("explicit")
    # the flags also work after the subcommand
    args = cli.build_parser().parse_args(["stats", "--project", "two"])
    assert cli._data_dir(args) == (tmp_path / "two").resolve()


def test_unknown_project_flag_exits_with_a_message():
    with pytest.raises(SystemExit, match="unknown project 'ghost'"):
        _data_dir("--project", "ghost")


def test_projects_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    parser = cli.build_parser()
    run = lambda *argv: cli.cmd_projects(parser.parse_args(["projects", *argv]))  # noqa: E731

    assert run("add", "thesis", "--description", "PhD", "--use") == 0
    assert (tmp_path / "projects" / "thesis" / "pdfs").is_dir()  # data dir created
    assert run("add", "side", str(tmp_path / "elsewhere")) == 0
    assert run("open", "side") == 0  # alias of `use`
    assert run("list") == 0
    out = capsys.readouterr().out
    assert "* side" in out and "  thesis" in out and "PhD" in out

    assert run("add", "thesis") == 2
    assert run("remove", "side") == 0
    assert (tmp_path / "elsewhere").is_dir()  # unregistering never deletes data
    assert json.loads(projects.registry_path().read_text())["current"] is None
