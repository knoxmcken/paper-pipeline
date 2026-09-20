import json

from paperpipe import cli


def test_parser_wires_every_subcommand():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "-q", "llm agents", "-n", "5", "--data-dir", "/tmp/x"])
    assert args.command == "run"
    assert args.func is cli.cmd_run
    assert args.query == ["llm agents"] and args.max == 5 and args.data_dir == "/tmp/x"


def test_query_is_repeatable_and_order_preserving():
    parser = cli.build_parser()
    args = parser.parse_args(["fetch", "-q", "a", "-q", "b", "-q", "c"])
    assert args.query == ["a", "b", "c"]
    for name in ("search", "fetch", "extract", "index", "export", "stats", "show"):
        parsed = parser.parse_args([name] + (["-q", "x"] if name in ("search", "fetch") else []))
        assert parsed.command == name


def test_run_stage_names_cover_the_documented_pipeline():
    assert cli.STAGES == ("fetch", "download", "extract", "index", "export")


def test_stats_and_show_against_a_temp_db(tmp_path):
    args = cli.build_parser().parse_args(["--data-dir", str(tmp_path), "stats"])
    assert cli.cmd_stats(args) == 0
    args = cli.build_parser().parse_args(["--data-dir", str(tmp_path), "show"])
    assert cli.cmd_show(args) == 0
    assert (tmp_path / "papers.db").exists()
