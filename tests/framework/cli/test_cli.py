from collections import namedtuple
from itertools import cycle
from os.path import join
from pathlib import Path
from unittest.mock import patch

import anyconfig
import click
from click.testing import CliRunner
from pytest import fixture, mark, raises

from kedro import __version__ as version
from kedro.framework.cli import get_project_context, load_entry_points
from kedro.framework.cli.catalog import catalog_cli
from kedro.framework.cli.cli import KedroCLI, _init_plugins, cli
from kedro.framework.cli.jupyter import jupyter_cli
from kedro.framework.cli.micropkg import micropkg_cli
from kedro.framework.cli.pipeline import pipeline_cli
from kedro.framework.cli.project import project_group
from kedro.framework.cli.registry import registry_cli
from kedro.framework.cli.starters import create_cli
from kedro.framework.cli.utils import (
    CommandCollection,
    KedroCliError,
    _clean_pycache,
    _update_value_nested_dict,
    forward_command,
    get_pkg_version,
)
from kedro.framework.session import KedroSession
from kedro.runner import ParallelRunner, SequentialRunner


@click.group(name="stub_cli")
def stub_cli():
    """Stub CLI group description."""
    print("group callback")


@stub_cli.command(name="stub_command")
def stub_command():
    print("command callback")


@forward_command(stub_cli, name="forwarded_command")
def forwarded_command(args, **kwargs):  # pylint: disable=unused-argument
    print("fred", args)


@forward_command(stub_cli, name="forwarded_help", forward_help=True)
def forwarded_help(args, **kwargs):  # pylint: disable=unused-argument
    print("fred", args)


@forward_command(stub_cli)
def unnamed(args, **kwargs):  # pylint: disable=unused-argument
    print("fred", args)


@fixture
def requirements_file(tmp_path):
    body = "\n".join(["SQLAlchemy>=1.2.0, <2.0", "pandas==0.23.0", "toposort"]) + "\n"
    reqs_file = tmp_path / "requirements.txt"
    reqs_file.write_text(body)
    yield reqs_file


@fixture
def fake_session(mocker):
    mock_session_create = mocker.patch.object(KedroSession, "create")
    return mock_session_create.return_value.__enter__.return_value


# pylint:disable=too-few-public-methods
class DummyContext:
    def __init__(self):
        self.config_loader = "config_loader"

    catalog = "catalog"
    pipeline = "pipeline"
    project_name = "dummy_name"
    project_path = "dummy_path"


@fixture
def mocked_load_context(mocker):
    return mocker.patch(
        "kedro.framework.cli.cli.load_context", return_value=DummyContext()
    )


class TestCliCommands:
    def test_cli(self):
        """Run `kedro` without arguments."""
        result = CliRunner().invoke(cli, [])

        assert result.exit_code == 0
        assert "kedro" in result.output

    def test_print_version(self):
        """Check that `kedro --version` and `kedro -V` outputs contain
        the current package version."""
        result = CliRunner().invoke(cli, ["--version"])

        assert result.exit_code == 0
        assert version in result.output

        result_abr = CliRunner().invoke(cli, ["-V"])
        assert result_abr.exit_code == 0
        assert version in result_abr.output

    def test_info_contains_plugin_versions(self, entry_point, mocker):
        get_distribution = mocker.patch("pkg_resources.get_distribution")
        get_distribution().version = "1.0.2"
        entry_point.module_name = "bob.fred"

        result = CliRunner().invoke(cli, ["info"])
        assert result.exit_code == 0
        assert (
            "bob: 1.0.2 (entry points:cli_hooks,global,hooks,init,line_magic,project)"
            in result.output
        )

        entry_point.load.assert_not_called()

    @mark.usefixtures("entry_points")
    def test_info_no_plugins(self):
        result = CliRunner().invoke(cli, ["info"])
        assert result.exit_code == 0
        assert "No plugins installed" in result.output

    def test_help(self):
        """Check that `kedro --help` returns a valid help message."""
        result = CliRunner().invoke(cli, ["--help"])

        assert result.exit_code == 0
        assert "kedro" in result.output

        result = CliRunner().invoke(cli, ["-h"])
        assert result.exit_code == 0
        assert "-h, --help     Show this message and exit." in result.output

    @patch("webbrowser.open")
    def test_docs(self, patched_browser):
        """Check that `kedro docs` opens a correct file in the browser."""
        result = CliRunner().invoke(cli, ["docs"])

        assert result.exit_code == 0
        for each in ("Opening file", join("html", "index.html")):
            assert each in result.output

        assert patched_browser.call_count == 1
        args, _ = patched_browser.call_args
        for each in ("file://", join("kedro", "framework", "html", "index.html")):
            assert each in args[0]


class TestCommandCollection:
    def test_found(self):
        """Test calling existing command."""
        cmd_collection = CommandCollection(("Commands", [cli, stub_cli]))
        result = CliRunner().invoke(cmd_collection, ["stub_command"])
        assert result.exit_code == 0
        assert "group callback" not in result.output
        assert "command callback" in result.output

    def test_found_reverse(self):
        """Test calling existing command."""
        cmd_collection = CommandCollection(("Commands", [stub_cli, cli]))
        result = CliRunner().invoke(cmd_collection, ["stub_command"])
        assert result.exit_code == 0
        assert "group callback" in result.output
        assert "command callback" in result.output

    def test_not_found(self):
        """Test calling nonexistent command."""
        cmd_collection = CommandCollection(("Commands", [cli, stub_cli]))
        result = CliRunner().invoke(cmd_collection, ["not_found"])
        assert result.exit_code == 2
        assert "No such command" in result.output
        assert "Did you mean one of these" not in result.output

    def test_not_found_closest_match(self, mocker):
        """Check that calling a nonexistent command with a close match returns the close match"""
        patched_difflib = mocker.patch(
            "kedro.framework.cli.utils.difflib.get_close_matches",
            return_value=["suggestion_1", "suggestion_2"],
        )

        cmd_collection = CommandCollection(("Commands", [cli, stub_cli]))
        result = CliRunner().invoke(cmd_collection, ["not_found"])

        patched_difflib.assert_called_once_with(
            "not_found", mocker.ANY, mocker.ANY, mocker.ANY
        )

        assert result.exit_code == 2
        assert "No such command" in result.output
        assert "Did you mean one of these?" in result.output
        assert "suggestion_1" in result.output
        assert "suggestion_2" in result.output

    def test_not_found_closet_match_singular(self, mocker):
        """Check that calling a nonexistent command with a close match has the proper wording"""
        patched_difflib = mocker.patch(
            "kedro.framework.cli.utils.difflib.get_close_matches",
            return_value=["suggestion_1"],
        )

        cmd_collection = CommandCollection(("Commands", [cli, stub_cli]))
        result = CliRunner().invoke(cmd_collection, ["not_found"])

        patched_difflib.assert_called_once_with(
            "not_found", mocker.ANY, mocker.ANY, mocker.ANY
        )

        assert result.exit_code == 2
        assert "No such command" in result.output
        assert "Did you mean this?" in result.output
        assert "suggestion_1" in result.output

    def test_help(self):
        """Check that help output includes stub_cli group description."""
        cmd_collection = CommandCollection(("Commands", [cli, stub_cli]))
        result = CliRunner().invoke(cmd_collection, [])
        assert result.exit_code == 0
        assert "Stub CLI group description" in result.output
        assert "Kedro is a CLI" in result.output


class TestForwardCommand:
    def test_regular(self):
        """Test forwarded command invocation."""
        result = CliRunner().invoke(stub_cli, ["forwarded_command", "bob"])
        assert result.exit_code == 0, result.output
        assert "bob" in result.output
        assert "fred" in result.output
        assert "--help" not in result.output
        assert "forwarded_command" not in result.output

    def test_unnamed(self):
        """Test forwarded command invocation."""
        result = CliRunner().invoke(stub_cli, ["unnamed", "bob"])
        assert result.exit_code == 0, result.output
        assert "bob" in result.output
        assert "fred" in result.output
        assert "--help" not in result.output
        assert "forwarded_command" not in result.output

    def test_help(self):
        """Test help output for the command with help flags not forwarded."""
        result = CliRunner().invoke(stub_cli, ["forwarded_command", "bob", "--help"])
        assert result.exit_code == 0, result.output
        assert "bob" not in result.output
        assert "fred" not in result.output
        assert "--help" in result.output
        assert "forwarded_command" in result.output

    def test_forwarded_help(self):
        """Test help output for the command with forwarded help flags."""
        result = CliRunner().invoke(stub_cli, ["forwarded_help", "bob", "--help"])
        assert result.exit_code == 0, result.output
        assert "bob" in result.output
        assert "fred" in result.output
        assert "--help" in result.output
        assert "forwarded_help" not in result.output


class TestCliUtils:
    def test_get_pkg_version(self, requirements_file):
        """Test get_pkg_version(), which extracts package version
        from the provided requirements file."""
        sa_version = "SQLAlchemy>=1.2.0, <2.0"
        assert get_pkg_version(requirements_file, "SQLAlchemy") == sa_version
        assert get_pkg_version(requirements_file, "pandas") == "pandas==0.23.0"
        assert get_pkg_version(requirements_file, "toposort") == "toposort"
        with raises(KedroCliError):
            get_pkg_version(requirements_file, "nonexistent")
        with raises(KedroCliError):
            non_existent_file = f"{str(requirements_file)}-nonexistent"
            get_pkg_version(non_existent_file, "pandas")

    def test_clean_pycache(self, tmp_path, mocker):
        """Test `clean_pycache` utility function"""
        source = Path(tmp_path)
        pycache2 = Path(source / "nested1" / "nested2" / "__pycache__").resolve()
        pycache2.mkdir(parents=True)
        pycache1 = Path(source / "nested1" / "__pycache__").resolve()
        pycache1.mkdir()
        pycache = Path(source / "__pycache__").resolve()
        pycache.mkdir()

        mocked_rmtree = mocker.patch("shutil.rmtree")
        _clean_pycache(source)

        expected_calls = [
            mocker.call(pycache, ignore_errors=True),
            mocker.call(pycache1, ignore_errors=True),
            mocker.call(pycache2, ignore_errors=True),
        ]
        assert mocked_rmtree.mock_calls == expected_calls

    def test_update_value_nested_dict(self):
        """Test `_update_value_nested_dict` utility function."""

        nested_dict = {"foo": {"hello": "world", "bar": 1}}
        value_for_nested_dict = 2
        walking_path_for_nested_dict = ["foo", "bar"]

        expected = {"foo": {"hello": "world", "bar": 2}}
        actual = _update_value_nested_dict(
            nested_dict, value_for_nested_dict, walking_path_for_nested_dict
        )
        assert actual == expected


@mark.usefixtures("mocked_load_context")
class TestGetProjectContext:
    def test_get_context_without_project_path(self, mocked_load_context):
        dummy_context = get_project_context("context")
        mocked_load_context.assert_called_once_with(Path.cwd())
        assert isinstance(dummy_context, DummyContext)

    def test_get_context_with_project_path(self, tmpdir, mocked_load_context):
        dummy_project_path = tmpdir.mkdir("dummy_project")
        dummy_context = get_project_context("context", project_path=dummy_project_path)
        mocked_load_context.assert_called_once_with(dummy_project_path)
        assert isinstance(dummy_context, DummyContext)

    def test_verbose(self):
        assert not get_project_context("verbose")


class TestEntryPoints:
    def test_project_groups(self, entry_points, entry_point):
        entry_point.load.return_value = "groups"
        groups = load_entry_points("project")
        assert groups == ["groups"]
        entry_points.assert_called_once_with(group="kedro.project_commands")

    def test_project_error_is_caught(self, entry_points, entry_point):
        entry_point.load.side_effect = Exception()
        with raises(KedroCliError, match="Loading project commands"):
            load_entry_points("project")

        entry_points.assert_called_once_with(group="kedro.project_commands")

    def test_global_groups(self, entry_points, entry_point):
        entry_point.load.return_value = "groups"
        groups = load_entry_points("global")
        assert groups == ["groups"]
        entry_points.assert_called_once_with(group="kedro.global_commands")

    def test_global_error_is_caught(self, entry_points, entry_point):
        entry_point.load.side_effect = Exception()
        with raises(KedroCliError, match="Loading global commands from"):
            load_entry_points("global")
        entry_points.assert_called_once_with(group="kedro.global_commands")

    def test_init(self, entry_points, entry_point):
        _init_plugins()
        entry_points.assert_called_once_with(group="kedro.init")
        entry_point.load().assert_called_once_with()

    def test_init_error_is_caught(self, entry_points, entry_point):
        entry_point.load.side_effect = Exception()
        with raises(KedroCliError, match="Initializing"):
            _init_plugins()
        entry_points.assert_called_once_with(group="kedro.init")


class TestKedroCLI:
    def test_project_commands_no_clipy(self, mocker, fake_metadata):
        mocker.patch(
            "kedro.framework.cli.cli.importlib.import_module",
            side_effect=cycle([ModuleNotFoundError()]),
        )
        mocker.patch("kedro.framework.cli.cli._is_project", return_value=True)
        mocker.patch(
            "kedro.framework.cli.cli.bootstrap_project", return_value=fake_metadata
        )
        kedro_cli = KedroCLI(fake_metadata.project_path)
        assert len(kedro_cli.project_groups) == 6
        assert kedro_cli.project_groups == [
            catalog_cli,
            jupyter_cli,
            pipeline_cli,
            micropkg_cli,
            project_group,
            registry_cli,
        ]

    def test_project_commands_no_project(self, mocker, tmp_path):
        mocker.patch("kedro.framework.cli.cli._is_project", return_value=False)
        kedro_cli = KedroCLI(tmp_path)
        assert len(kedro_cli.project_groups) == 0
        assert kedro_cli._metadata is None

    def test_project_commands_invalid_clipy(self, mocker, fake_metadata):
        mocker.patch(
            "kedro.framework.cli.cli.importlib.import_module", return_value=None
        )
        mocker.patch("kedro.framework.cli.cli._is_project", return_value=True)
        mocker.patch(
            "kedro.framework.cli.cli.bootstrap_project", return_value=fake_metadata
        )
        with raises(KedroCliError, match="Cannot load commands from"):
            _ = KedroCLI(fake_metadata.project_path)

    def test_project_commands_valid_clipy(self, mocker, fake_metadata):
        Module = namedtuple("Module", ["cli"])
        mocker.patch(
            "kedro.framework.cli.cli.importlib.import_module",
            return_value=Module(cli=cli),
        )
        mocker.patch("kedro.framework.cli.cli._is_project", return_value=True)
        mocker.patch(
            "kedro.framework.cli.cli.bootstrap_project", return_value=fake_metadata
        )
        kedro_cli = KedroCLI(fake_metadata.project_path)
        assert len(kedro_cli.project_groups) == 7
        assert kedro_cli.project_groups == [
            catalog_cli,
            jupyter_cli,
            pipeline_cli,
            micropkg_cli,
            project_group,
            registry_cli,
            cli,
        ]

    def test_kedro_cli_no_project(self, mocker, tmp_path):
        mocker.patch("kedro.framework.cli.cli._is_project", return_value=False)
        kedro_cli = KedroCLI(tmp_path)
        assert len(kedro_cli.global_groups) == 2
        assert kedro_cli.global_groups == [cli, create_cli]

        result = CliRunner().invoke(kedro_cli, [])

        assert result.exit_code == 0
        assert "Global commands from Kedro" in result.output
        assert "Project specific commands from Kedro" not in result.output

    def test_kedro_cli_with_project(self, mocker, fake_metadata):
        Module = namedtuple("Module", ["cli"])
        mocker.patch(
            "kedro.framework.cli.cli.importlib.import_module",
            return_value=Module(cli=cli),
        )
        mocker.patch("kedro.framework.cli.cli._is_project", return_value=True)
        mocker.patch(
            "kedro.framework.cli.cli.bootstrap_project", return_value=fake_metadata
        )
        kedro_cli = KedroCLI(fake_metadata.project_path)

        assert len(kedro_cli.global_groups) == 2
        assert kedro_cli.global_groups == [cli, create_cli]
        assert len(kedro_cli.project_groups) == 7
        assert kedro_cli.project_groups == [
            catalog_cli,
            jupyter_cli,
            pipeline_cli,
            micropkg_cli,
            project_group,
            registry_cli,
            cli,
        ]

        result = CliRunner().invoke(kedro_cli, [])
        assert result.exit_code == 0
        assert "Global commands from Kedro" in result.output
        assert "Project specific commands from Kedro" in result.output


@mark.usefixtures("chdir_to_dummy_project", "patch_log")
class TestRunCommand:
    @staticmethod
    @fixture(params=["run_config.yml", "run_config.json"])
    def fake_run_config(request, fake_root_dir):
        config_path = str(fake_root_dir / request.param)
        anyconfig.dump(
            {
                "run": {
                    "pipeline": "pipeline1",
                    "tag": ["tag1", "tag2"],
                    "node_names": ["node1", "node2"],
                }
            },
            config_path,
        )
        return config_path

    @staticmethod
    @fixture()
    def fake_run_config_with_params(fake_run_config, request):
        config = anyconfig.load(fake_run_config)
        config["run"].update(request.param)
        anyconfig.dump(config, fake_run_config)
        return fake_run_config

    def test_run_successfully(
        self, fake_project_cli, fake_metadata, fake_session, mocker
    ):
        result = CliRunner().invoke(fake_project_cli, ["run"], obj=fake_metadata)
        assert not result.exit_code

        fake_session.run.assert_called_once_with(
            tags=(),
            runner=mocker.ANY,
            node_names=(),
            from_nodes=[],
            to_nodes=[],
            from_inputs=[],
            to_outputs=[],
            load_versions={},
            pipeline_name=None,
        )

        runner = fake_session.run.call_args_list[0][1]["runner"]
        assert isinstance(runner, SequentialRunner)
        assert not runner._is_async

    def test_run_with_pipeline_filters(
        self, fake_project_cli, fake_metadata, fake_session, mocker
    ):
        from_nodes = ["--from-nodes", "splitting_data"]
        to_nodes = ["--to-nodes", "training_model"]
        tags = ["--tag", "de"]
        result = CliRunner().invoke(
            fake_project_cli, ["run", *from_nodes, *to_nodes, *tags], obj=fake_metadata
        )
        assert not result.exit_code

        fake_session.run.assert_called_once_with(
            tags=("de",),
            runner=mocker.ANY,
            node_names=(),
            from_nodes=from_nodes[1:],
            to_nodes=to_nodes[1:],
            from_inputs=[],
            to_outputs=[],
            load_versions={},
            pipeline_name=None,
        )

        runner = fake_session.run.call_args_list[0][1]["runner"]
        assert isinstance(runner, SequentialRunner)
        assert not runner._is_async

    def test_with_sequential_runner_and_parallel_flag(
        self, fake_project_cli, fake_session
    ):
        result = CliRunner().invoke(
            fake_project_cli, ["run", "--parallel", "--runner=SequentialRunner"]
        )
        assert result.exit_code
        assert "Please use either --parallel or --runner" in result.stdout

        fake_session.return_value.run.assert_not_called()

    def test_run_successfully_parallel_via_flag(
        self, fake_project_cli, fake_metadata, fake_session, mocker
    ):
        result = CliRunner().invoke(
            fake_project_cli, ["run", "--parallel"], obj=fake_metadata
        )
        assert not result.exit_code
        fake_session.run.assert_called_once_with(
            tags=(),
            runner=mocker.ANY,
            node_names=(),
            from_nodes=[],
            to_nodes=[],
            from_inputs=[],
            to_outputs=[],
            load_versions={},
            pipeline_name=None,
        )

        runner = fake_session.run.call_args_list[0][1]["runner"]
        assert isinstance(runner, ParallelRunner)
        assert not runner._is_async

    def test_run_successfully_parallel_via_name(
        self, fake_project_cli, fake_metadata, fake_session
    ):
        result = CliRunner().invoke(
            fake_project_cli, ["run", "--runner=ParallelRunner"], obj=fake_metadata
        )
        assert not result.exit_code
        runner = fake_session.run.call_args_list[0][1]["runner"]
        assert isinstance(runner, ParallelRunner)
        assert not runner._is_async

    def test_run_async(self, fake_project_cli, fake_metadata, fake_session):
        result = CliRunner().invoke(
            fake_project_cli, ["run", "--async"], obj=fake_metadata
        )
        assert not result.exit_code
        runner = fake_session.run.call_args_list[0][1]["runner"]
        assert isinstance(runner, SequentialRunner)
        assert runner._is_async

    @mark.parametrize("config_flag", ["--config", "-c"])
    def test_run_with_config(
        self,
        config_flag,
        fake_project_cli,
        fake_metadata,
        fake_session,
        fake_run_config,
        mocker,
    ):
        result = CliRunner().invoke(
            fake_project_cli, ["run", config_flag, fake_run_config], obj=fake_metadata
        )
        assert not result.exit_code
        fake_session.run.assert_called_once_with(
            tags=("tag1", "tag2"),
            runner=mocker.ANY,
            node_names=("node1", "node2"),
            from_nodes=[],
            to_nodes=[],
            from_inputs=[],
            to_outputs=[],
            load_versions={},
            pipeline_name="pipeline1",
        )

    @mark.parametrize(
        "fake_run_config_with_params,expected",
        [
            ({}, {}),
            ({"params": {"foo": "baz"}}, {"foo": "baz"}),
            ({"params": "foo:baz"}, {"foo": "baz"}),
            (
                {"params": {"foo": "123.45", "baz": "678", "bar": 9}},
                {"foo": "123.45", "baz": "678", "bar": 9},
            ),
        ],
        indirect=["fake_run_config_with_params"],
    )
    def test_run_with_params_in_config(
        self,
        expected,
        fake_project_cli,
        fake_metadata,
        fake_run_config_with_params,
        mocker,
    ):
        mock_session_create = mocker.patch.object(KedroSession, "create")
        mocked_session = mock_session_create.return_value.__enter__.return_value

        result = CliRunner().invoke(
            fake_project_cli,
            ["run", "-c", fake_run_config_with_params],
            obj=fake_metadata,
        )

        assert not result.exit_code
        mocked_session.run.assert_called_once_with(
            tags=("tag1", "tag2"),
            runner=mocker.ANY,
            node_names=("node1", "node2"),
            from_nodes=[],
            to_nodes=[],
            from_inputs=[],
            to_outputs=[],
            load_versions={},
            pipeline_name="pipeline1",
        )
        mock_session_create.assert_called_once_with(
            env=mocker.ANY, extra_params=expected
        )

    @mark.parametrize(
        "cli_arg,expected_extra_params",
        [
            ("foo:bar", {"foo": "bar"}),
            (
                "foo:123.45, bar:1a,baz:678. ,qux:1e-2,quux:0,quuz:",
                {
                    "foo": 123.45,
                    "bar": "1a",
                    "baz": 678,
                    "qux": 0.01,
                    "quux": 0,
                    "quuz": "",
                },
            ),
            ("foo:bar,baz:fizz:buzz", {"foo": "bar", "baz": "fizz:buzz"}),
            (
                "foo:bar, baz: https://example.com",
                {"foo": "bar", "baz": "https://example.com"},
            ),
            ("foo:bar,baz:fizz buzz", {"foo": "bar", "baz": "fizz buzz"}),
            ("foo:bar, foo : fizz buzz  ", {"foo": "fizz buzz"}),
            ("foo.nested:bar", {"foo": {"nested": "bar"}}),
            ("foo.nested:123.45", {"foo": {"nested": 123.45}}),
            (
                "foo.nested_1.double_nest:123.45,foo.nested_2:1a",
                {"foo": {"nested_1": {"double_nest": 123.45}, "nested_2": "1a"}},
            ),
        ],
    )
    def test_run_extra_params(
        self,
        mocker,
        fake_project_cli,
        fake_metadata,
        cli_arg,
        expected_extra_params,
    ):
        mock_session_create = mocker.patch.object(KedroSession, "create")

        result = CliRunner().invoke(
            fake_project_cli, ["run", "--params", cli_arg], obj=fake_metadata
        )

        assert not result.exit_code
        mock_session_create.assert_called_once_with(
            env=mocker.ANY, extra_params=expected_extra_params
        )

    @mark.parametrize("bad_arg", ["bad", "foo:bar,bad"])
    def test_bad_extra_params(self, fake_project_cli, fake_metadata, bad_arg):
        result = CliRunner().invoke(
            fake_project_cli, ["run", "--params", bad_arg], obj=fake_metadata
        )
        assert result.exit_code
        assert (
            "Item `bad` must contain a key and a value separated by `:`"
            in result.stdout
        )

    @mark.parametrize("bad_arg", [":", ":value", " :value"])
    def test_bad_params_key(self, fake_project_cli, fake_metadata, bad_arg):
        result = CliRunner().invoke(
            fake_project_cli, ["run", "--params", bad_arg], obj=fake_metadata
        )
        assert result.exit_code
        assert "Parameter key cannot be an empty string" in result.stdout

    @mark.parametrize(
        "option,value",
        [("--load-version", "dataset1:time1"), ("-lv", "dataset2:time2")],
    )
    def test_reformat_load_versions(
        self, fake_project_cli, fake_metadata, fake_session, option, value, mocker
    ):
        result = CliRunner().invoke(
            fake_project_cli, ["run", option, value], obj=fake_metadata
        )
        assert not result.exit_code, result.output

        ds, t = value.split(":", 1)
        fake_session.run.assert_called_once_with(
            tags=(),
            runner=mocker.ANY,
            node_names=(),
            from_nodes=[],
            to_nodes=[],
            from_inputs=[],
            to_outputs=[],
            load_versions={ds: t},
            pipeline_name=None,
        )

    def test_fail_reformat_load_versions(self, fake_project_cli, fake_metadata):
        load_version = "2020-05-12T12.00.00"
        result = CliRunner().invoke(
            fake_project_cli, ["run", "-lv", load_version], obj=fake_metadata
        )
        assert result.exit_code, result.output

        expected_output = (
            f"Error: Expected the form of `load_version` to be "
            f"`dataset_name:YYYY-MM-DDThh.mm.ss.sssZ`,"
            f"found {load_version} instead\n"
        )
        assert expected_output in result.output
