"""
This file contains the fixtures that are reusable by any tests within
this directory. You don't need to import the fixtures as pytest will
discover them automatically. More info here:
https://docs.pytest.org/en/latest/fixture.html
"""
import shutil
import sys
import tempfile
from importlib import import_module
from pathlib import Path

import click
import yaml
from click.testing import CliRunner
from pytest import fixture

from kedro import __version__ as kedro_version
from kedro.framework.cli.catalog import catalog_cli
from kedro.framework.cli.cli import cli
from kedro.framework.cli.jupyter import jupyter_cli
from kedro.framework.cli.micropkg import micropkg_cli
from kedro.framework.cli.pipeline import pipeline_cli
from kedro.framework.cli.project import project_group
from kedro.framework.cli.registry import registry_cli
from kedro.framework.cli.starters import create_cli
from kedro.framework.project import configure_project, pipelines, settings
from kedro.framework.startup import ProjectMetadata

REPO_NAME = "dummy_project"
PACKAGE_NAME = "dummy_package"


@fixture(scope="module")
def fake_root_dir():
    # using tempfile as tmp_path fixture doesn't support module scope
    tmpdir = tempfile.mkdtemp()
    try:
        yield Path(tmpdir).resolve()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


@fixture(scope="module")
def fake_repo_path(fake_root_dir):
    return fake_root_dir.resolve() / REPO_NAME


@fixture(scope="module")
def dummy_config(fake_root_dir, fake_metadata):
    config = {
        "project_name": fake_metadata.project_name,
        "repo_name": REPO_NAME,
        "python_package": fake_metadata.package_name,
        "output_dir": str(fake_root_dir),
    }

    config_path = fake_root_dir / "dummy_config.yml"
    with config_path.open("w") as f:
        yaml.dump(config, f)

    return config_path


@fixture(scope="module")
def fake_metadata(fake_root_dir):
    return ProjectMetadata(
        fake_root_dir / REPO_NAME / "pyproject.toml",
        PACKAGE_NAME,
        "CLI Testing Project",
        fake_root_dir / REPO_NAME,
        kedro_version,
        fake_root_dir / REPO_NAME / "src",
    )


# This is needed just for the tests, those CLI groups are merged in our
# code when invoking `kedro` but when imported, they still need to be merged
@fixture(scope="module")
def fake_kedro_cli():
    return click.CommandCollection(
        name="Kedro",
        sources=[
            cli,
            create_cli,
            catalog_cli,
            jupyter_cli,
            pipeline_cli,
            micropkg_cli,
            project_group,
            registry_cli,
        ],
    )


@fixture(scope="module")
def fake_project_cli(
    fake_repo_path: Path, dummy_config: Path, fake_kedro_cli: click.CommandCollection
):
    old_settings = settings.as_dict()
    starter_path = Path(__file__).parents[2].resolve()
    starter_path = starter_path / "features" / "steps" / "test_starter"
    CliRunner().invoke(
        fake_kedro_cli, ["new", "-c", str(dummy_config), "--starter", str(starter_path)]
    )

    # NOTE: Here we load a couple of modules, as they would be imported in
    # the code and tests.
    # It's safe to remove the new entries from path due to the python
    # module caching mechanism. Any `reload` on it will not work though.
    old_path = sys.path.copy()
    sys.path = [str(fake_repo_path / "src")] + sys.path

    import_module(PACKAGE_NAME)
    configure_project(PACKAGE_NAME)
    yield fake_kedro_cli

    # reset side-effects of configure_project
    pipelines._clear(PACKAGE_NAME)  # this resets pipelines loading state
    for key, value in old_settings.items():
        settings.set(key, value)
    sys.path = old_path
    del sys.modules[PACKAGE_NAME]
