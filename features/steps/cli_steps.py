"""Behave step definitions for the cli_scenarios feature."""

import itertools
import json
import shlex
import shutil
from pathlib import Path
from time import time

import behave
import requests
import toml
import yaml
from behave import given, then, when

import kedro
from features.steps import util
from features.steps.sh_run import ChildTerminatingPopen, check_run, run

OK_EXIT_CODE = 0


TEST_JUPYTER_ORG = r"""
{
 "cells": [
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": [
    "print(\"Hello World!\")"
   ]
  }
 ],
 "metadata": {
  "kernelspec": {
   "display_name": "Python 3",
   "language": "python",
   "name": "python3"
  },
  "language_info": {
   "codemirror_mode": {
    "name": "ipython",
    "version": 3
   },
   "file_extension": ".py",
   "mimetype": "text/x-python",
   "name": "python",
   "nbconvert_exporter": "python",
   "pygments_lexer": "ipython3",
   "version": "3.5.6"
  }
 },
 "nbformat": 4,
 "nbformat_minor": 2
}
"""

# The difference
TEST_JUPYTER_AFTER_EXEC = r"""
{
 "cells": [
  {
   "cell_type": "code",
   "execution_count": 1,
   "metadata": {
    "tags": [
     "node"
    ]
   },
   "outputs": [
    {
     "name": "stdout",
     "output_type": "stream",
     "text": [
      "Hello World!\n"
     ]
    }
   ],
   "source": [
    "print(\"Hello World!\")"
   ]
  },
  {
   "cell_type": "code",
   "execution_count": null,
   "metadata": {},
   "outputs": [],
   "source": []
  }
 ],
 "metadata": {
  "kernelspec": {
   "display_name": "Python 3",
   "language": "python",
   "name": "python3"
  },
  "language_info": {
   "codemirror_mode": {
    "name": "ipython",
    "version": 3
   },
   "file_extension": ".py",
   "mimetype": "text/x-python",
   "name": "python",
   "nbconvert_exporter": "python",
   "pygments_lexer": "ipython3",
   "version": "3.5.6"
  }
 },
 "nbformat": 4,
 "nbformat_minor": 2
}
"""


behave.register_type(CSV=util.parse_csv)


def _check_service_up(context: behave.runner.Context, url: str, string: str):
    """Check that a service is running and responding appropriately.

    Args:
        context: Test context.
        url: Url that is to be read.
        string: The string to be checked.

    """
    response = requests.get(url, timeout=1.0)
    response.raise_for_status()

    data = response.text
    assert string in data
    assert context.result.poll() is None


@given("I have prepared a run_config file with config options")
def create_run_config_file(context):
    curr_dir = Path(__file__).parent
    run_config_file = context.root_project_dir / "run_config.yml"
    shutil.copyfile(str(curr_dir / "e2e_test_cli_config.yml"), str(run_config_file))


@given("I have prepared a config file")
def create_config_file(context):
    """Behave step to create a temporary config file
    (given the existing temp directory) and store it in the context.
    """
    context.config_file = context.temp_dir / "config.yml"
    context.project_name = "project-dummy"
    context.root_project_dir = context.temp_dir / context.project_name
    context.package_name = context.project_name.replace("-", "_")
    config = {
        "project_name": context.project_name,
        "repo_name": context.project_name,
        "output_dir": str(context.temp_dir),
        "python_package": context.package_name,
    }
    with context.config_file.open("w") as config_file:
        yaml.dump(config, config_file, default_flow_style=False)


@given('I have executed the kedro command "{command}"')
def exec_kedro_target_checked(context, command):
    """Execute Kedro command and check the status."""
    cmd = [context.kedro] + command.split()

    res = run(cmd, env=context.env, cwd=str(context.root_project_dir))

    if res.returncode != OK_EXIT_CODE:
        print(res.stdout)
        print(res.stderr)
        assert False


@given('I have created new environment "{}"')
def create_new_env(context, env_name):
    env_path = context.root_project_dir / "conf" / env_name
    env_path.mkdir()

    for config_name in ("catalog", "parameters", "credentials"):
        path = env_path / f"{config_name}.yml"
        with path.open("w") as config_file:
            yaml.dump({}, config_file, default_flow_style=False)

    # overwrite the log level for anyconfig from WARNING to INFO
    logging_path = env_path / "logging.yml"
    logging_json = {
        "loggers": {
            "anyconfig": {
                "level": "INFO",
                "handlers": ["console", "info_file_handler", "error_file_handler"],
                "propagate": "no",
            },
            "kedro.io": {
                "level": "INFO",
                "handlers": ["console", "info_file_handler", "error_file_handler"],
                "propagate": "no",
            },
            "kedro.pipeline": {
                "level": "INFO",
                "handlers": ["console", "info_file_handler", "error_file_handler"],
                "propagate": "no",
            },
        }
    }
    with logging_path.open("w") as config_file:
        yaml.dump(logging_json, config_file, default_flow_style=False)


@given('the python package "{package}" has been uninstalled')
def uninstall_package_via_pip(context, package):
    """Uninstall a python package using pip."""
    run([context.pip, "uninstall", "-y", package], env=context.env)


@given("I have installed the project's python package")
@when("I install the project's python package")
def install_project_package_via_pip(context):
    """Install a python package using pip."""
    dist_dir = context.root_project_dir / "src" / "dist"
    (whl_file,) = dist_dir.glob("*.whl")
    run([context.pip, "install", str(whl_file)], env=context.env)


@given("I have installed the test plugin")
def install_test_plugin(context):
    """Install a python package using pip."""
    plugin_dir = Path(__file__).parent / "test_plugin"
    res = run([context.pip, "install", "-e", str(plugin_dir)], env=context.env)
    assert res.returncode == OK_EXIT_CODE, res


@given('I have disabled hooks for "{plugin}" plugin via config')
def disable_plugin_hooks(context, plugin):
    """Set `disable_hooks_for_plugins` in `settings.py`."""
    settings_path = (
        context.root_project_dir / "src" / context.package_name / "settings.py"
    )
    to_add = f"""\nDISABLE_HOOKS_FOR_PLUGINS = ("{plugin}",)"""
    with settings_path.open("a") as settings_file:
        settings_file.write(to_add)


@given("I have initialized a git repository")
def init_git_repo(context):
    """Init git repo"""
    with util.chdir(context.root_project_dir):
        check_run("git init")
        check_run("git config user.name 'Tester'")
        check_run("git config user.email 'tester.kedro@kedro.com'")


@given("I have added a test jupyter notebook")
def add_test_jupyter_nb(context):
    """Create a test jupyter notebook using TEST_JUPYTER_ORG."""
    with open(
        str(context.root_project_dir / "notebooks" / "hello_world.ipynb"),
        "wt",
        encoding="utf-8",
    ) as test_nb_fh:
        test_nb_fh.write(TEST_JUPYTER_ORG)


@given("I have run a non-interactive kedro new with starter")
@when("I run a non-interactive kedro new with starter")
def create_project_with_starter(context):
    """Behave step to run kedro new given the config I previously created."""
    starter_dir = Path(__file__).parent / "test_starter"
    res = run(
        [
            context.kedro,
            "new",
            "-c",
            str(context.config_file),
            "--starter",
            str(starter_dir),
        ],
        env=context.env,
        cwd=context.temp_dir,
    )
    assert res.returncode == OK_EXIT_CODE, res


@given("I have run a non-interactive kedro new without starter")
@when("I run a non-interactive kedro new without starter")
def create_project_without_starter(context):
    """Behave step to run kedro new given the config I previously created."""
    res = run(
        [context.kedro, "new", "-c", str(context.config_file)],
        env=context.env,
        cwd=context.temp_dir,
    )
    assert res.returncode == OK_EXIT_CODE, res
    # prevent telemetry from prompting for input during e2e tests
    telemetry_file = context.root_project_dir / ".telemetry"
    telemetry_file.write_text("consent: false", encoding="utf-8")


@given("I have deleted the credentials file")
def delete_credentials_file(context):
    """Delete configuration file from project"""
    path_to_config_file = context.root_project_dir / "conf" / "base" / "credentials.yml"
    path_to_config_file.unlink()


@given("I have added the project directory to staging")
@when("I add the project directory to staging")
def add_proj_dir_to_staging(context):
    """Add root project dir to staging"""
    with util.chdir(context.root_project_dir):
        check_run("git add .")


@given("I have committed changes to git")
@when("I commit changes to git")
def commit_changes_to_git(context):
    """Commit changes to git"""
    with util.chdir(context.root_project_dir):
        check_run(f"git commit -m 'Change {time()}'")


@when('I execute the kedro command "{command}"')
def exec_kedro_target(context, command):
    """Execute Kedro target."""
    split_command = command.split()
    cmd = [context.kedro] + split_command
    context.result = run(cmd, env=context.env, cwd=str(context.root_project_dir))


@when("I execute the installed project package")
def exec_project(context):
    """Execute installed Kedro project target."""
    cmd = [str(context.bin_dir / context.project_name)]
    # N.B.: prior to the introduction of load_package_context, this test was passing
    # accidentally because it was executing the installed project package at the
    # same directory as project root, so a lot of things were available on Path.cwd().
    # We take care to delete with `delete_unnecessary_assets` to simulate the behaviour
    # of a installed package in a fresh environment.
    context.result = run(cmd, env=context.env, cwd=str(context.root_project_dir))


@when('with tags {tags:CSV}, I execute the kedro command "{cmd}"')
def exec_kedro_run_with_tag(context, cmd, tags):
    """Execute `kedro run` with tags"""
    kedro_args = shlex.split(cmd)
    context.logfile_count = util.get_logline_count(
        util.get_logfile_path(context.root_project_dir)
    )

    tag_list = [["--tag", t] for t in tags]
    tag_args = list(itertools.chain.from_iterable(tag_list))
    run_cmd = [context.kedro] + kedro_args + tag_args

    context.result = run(run_cmd, env=context.env, cwd=str(context.root_project_dir))


@when("I ask the CLI for a version")
def get_kedro_version(context):
    """Behave step to run `kedro -V`."""
    res = run([context.kedro, "-V"], env=context.env, cwd=context.temp_dir)
    context.version_str = res.stdout
    assert context.version_str, res  # check non-empty


@when("I ask the CLI for a version using python -m")
def get_kedro_version_python(context):
    """Behave step to run `python -m kedro -V`."""
    cmd = [context.python, "-m", "kedro", "-V"]
    context.version_str = run(cmd, env=context.env, cwd=context.temp_dir).stdout
    assert context.version_str  # check non-empty


@when('I execute the kedro jupyter command "{command}"')
def exec_notebook(context, command):
    """Execute Kedro Jupyter target."""
    split_command = command.split()
    cmd = [context.kedro, "jupyter"] + split_command

    # Jupyter notebook forks a child process from a parent process, and
    # only kills the parent process when it is terminated
    context.result = ChildTerminatingPopen(
        cmd, env=context.env, cwd=str(context.root_project_dir)
    )


@when("Wait until the process is finished")
def wait(context):
    """Wait for child process to terminate."""
    context.result.wait()


@when("I execute the test jupyter notebook and save changes")
def simulate_nb_execution(context):
    """Change test jupyter notebook to TEST_JUPYTER_AFTER_EXEC
    simulate that it was executed and output was saved.
    """
    with open(
        str(context.root_project_dir / "notebooks" / "hello_world.ipynb"),
        "wt",
        encoding="utf-8",
    ) as test_nb_fh:
        test_nb_fh.write(TEST_JUPYTER_AFTER_EXEC)


@when("I remove the notebooks directory")
def delete_notebooks_dir(context):
    """Delete notebooks directory in project"""
    with util.chdir(context.root_project_dir):
        shutil.rmtree("notebooks/")


@when("I perform a hard git reset to restore the project to last commit")
def do_git_reset_hard(context):
    """Perform a hard git reset"""
    with util.chdir(context.root_project_dir):
        check_run("git reset --hard HEAD")


@when('I move the package to "{new_source_dir}"')
def move_package(context: behave.runner.Context, new_source_dir):
    """Move the project package to a new directory."""
    current_src_path = (context.root_project_dir / "src").resolve()
    new_src_path = (context.root_project_dir / new_source_dir).resolve()

    new_src_path.mkdir(exist_ok=True)
    shutil.move(str(current_src_path / context.package_name), str(new_src_path))


@when('Source directory is updated to "{new_source_dir}" in pyproject.toml')
def update_pyproject_toml(context: behave.runner.Context, new_source_dir):
    """Update `source_dir` in pyproject.toml file."""
    pyproject_toml_path = context.root_project_dir / "pyproject.toml"
    content = toml.load(pyproject_toml_path)
    content["tool"]["kedro"]["source_dir"] = new_source_dir
    content_str = toml.dumps(content)
    pyproject_toml_path.write_text(content_str)


@given("I have updated kedro requirements")
def update_kedro_req(context: behave.runner.Context):
    """Replace kedro as a standalone requirement with a line
    that includes all of kedro's dependencies (-r kedro/requirements.txt)
    """
    reqs_path = context.root_project_dir / "src" / "requirements.txt"
    kedro_reqs = f"-r {context.requirements_path.as_posix()}"

    if reqs_path.is_file():
        old_reqs = reqs_path.read_text().splitlines()
        new_reqs = []
        for req in old_reqs:
            if req.startswith("kedro"):
                new_reqs.append(kedro_reqs)
            else:
                new_reqs.append(req)
        new_reqs = "\n".join(new_reqs)
        assert old_reqs != new_reqs
        reqs_path.write_text(new_reqs)


@when("I add {dependency} to the requirements")
def add_req(context: behave.runner.Context, dependency: str):
    reqs_path = context.root_project_dir / "src" / "requirements.in"
    if reqs_path.is_file():
        reqs_path.write_text(reqs_path.read_text() + "\n" + dependency + "\n")


@then("CLI should print the version in an expected format")
def check_kedro_version(context):
    """Behave step to check validity of the kedro version."""
    version_no = context.version_str.split()[-1]
    assert version_no == kedro.__version__


@then("the expected project directories and files should be created")
def check_created_project_structure(context):
    """Behave step to check the subdirectories created by kedro new."""

    def is_created(name):
        """Check if path exists."""
        return (context.root_project_dir / name).exists()

    for path in ("README.md", "src", "data"):
        assert is_created(path)


@then("the pipeline should contain no nodes")
def check_empty_pipeline_exists(context):
    """Check if the created pipeline in
    `pipeline_registry.py` contains no nodes.
    """
    pipeline_file = (
        context.root_project_dir
        / "src"
        / context.project_name.replace("-", "_")
        / "pipeline_registry.py"
    )
    assert '"__default__": pipeline([])' in pipeline_file.read_text("utf-8")


@then("the pipeline should contain nodes")
def check_pipeline_not_empty(context):
    """Check if the created pipeline in
    `pipeline_registry.py` contains nodes.
    """
    pipeline_file = (
        context.root_project_dir
        / "src"
        / context.project_name.replace("-", "_")
        / "pipeline_registry.py"
    )
    assert "pipeline = pipeline([])" not in pipeline_file.read_text("utf-8")


@then("the console log should show that {number} nodes were run")
def check_one_node_run(context, number):
    expected_log_line = f"Completed {number} out of {number} tasks"
    assert expected_log_line in context.result.stdout


@then('the console log should show that "{node}" was run')
def check_correct_nodes_run(context, node):
    expected_log_line = f"Running node: {node}"
    stdout = context.result.stdout
    assert expected_log_line in stdout, (
        "Expected the following message segment to be printed on stdout: "
        f"{expected_log_line},\nbut got {stdout}"
    )


@then("I should get a successful exit code")
def check_status_code(context):
    if context.result.returncode != OK_EXIT_CODE:
        print(context.result.stdout)
        print(context.result.stderr)

        error_msg = (
            f"Expected exit code {OK_EXIT_CODE} but got {context.result.returncode}"
        )
        assert False, error_msg


@then("I should get an error exit code")
def check_failed_status_code(context):
    if context.result.returncode == OK_EXIT_CODE:
        print(context.result.stdout)
        print(context.result.stderr)

        error_msg = (
            f"Expected exit code other than {OK_EXIT_CODE} "
            f"but got {context.result.returncode}"
        )
        assert False, error_msg


@then("the relevant packages should be created")
def check_python_packages_created(context):
    """Check that egg and whl files exist in dist dir."""
    dist_dir = context.root_project_dir / "src" / "dist"
    egg_file = dist_dir.glob("*.egg")
    whl_file = dist_dir.glob("*.whl")
    assert any(egg_file)
    assert any(whl_file)


@then('I should get a message including "{msg}"')
def check_message_printed(context, msg):
    """Check that specified message is printed to stdout (can be a segment)."""

    if isinstance(context.result, ChildTerminatingPopen):
        stdout = context.result.stdout.read().decode()
        context.result.terminate()
    else:
        stdout = context.result.stdout

    assert msg in stdout, (
        "Expected the following message segment to be printed on stdout: "
        f"{msg},\nbut got {stdout}"
    )


@then('I should not get a message including "{msg}"')
def check_message_not_printed(context, msg):
    """Check that specified message is not printed to stdout."""

    if isinstance(context.result, ChildTerminatingPopen):
        stdout = context.result.stdout.read().decode()
        context.result.terminate()
    else:
        stdout = context.result.stdout

    assert msg not in stdout, (
        "Expected the following message segment not to be printed on stdout: "
        f"{msg},\nbut got {stdout}"
    )


@then('I should get an error message including "{msg}"')
def check_error_message_printed(context, msg):
    """Check that specified message is printed to stderr (can be a segment)."""

    if isinstance(context.result, ChildTerminatingPopen):
        stderr = context.result.stderr.read().decode()
        context.result.terminate()
    else:
        stderr = context.result.stderr

    assert msg in stderr, (
        "Expected the following message segment to be printed on stderr: "
        f"{msg},\nbut got {stderr}"
    )


@then("there should be an additional cell in the jupyter notebook")
def check_additional_cell_added(context):
    """Check that an addiitonal cell has been added compared to notebook
    coded by TEST_JUPYTER_ORG.
    """
    with open(
        str(context.root_project_dir / "notebooks" / "hello_world.ipynb"),
        encoding="utf-8",
    ) as test_nb_fh:
        context.nb_data = json.load(test_nb_fh)
        assert len(context.nb_data["cells"]) == 2


@then("the output should be empty in all the cells in the jupyter notebook")
def check_output_cells_empty(context):
    """Check that all cells contain empty output array."""
    for cell in context.nb_data["cells"]:
        assert cell["outputs"] == []


@then("jupyter notebook should run on port {port:d}")
def check_jupyter_nb_proc_on_port(context: behave.runner.Context, port: int):
    """Check that jupyter notebook service is running on specified port.

    Args:
        context: Test context
        port: Port to check

    """
    url = f"http://localhost:{port}"
    try:
        util.wait_for(
            func=_check_service_up,
            context=context,
            url=url,
            string="Jupyter Notebook",
            timeout_=15,
            print_error=True,
        )
    finally:
        context.result.terminate()


@then("Jupyter Lab should run on port {port:d}")
def check_jupyter_lab_proc_on_port(context: behave.runner.Context, port: int):
    """Check that jupyter lab service is running on specified port.

    Args:
        context: Test context
        port: Port to check

    """
    url = f"http://localhost:{port}"
    try:
        util.wait_for(
            func=_check_service_up,
            timeout_=20,
            context=context,
            url=url,
            string='<a href="/lab"',
            print_error=True,
        )
    finally:
        context.result.terminate()


@then("docs should be generated")
def check_docs_generated(context: behave.runner.Context):
    """Check that new project docs are generated."""
    index_html = (
        context.root_project_dir / "docs" / "build" / "html" / "index.html"
    ).read_text("utf-8")
    project_repo = context.project_name.replace("-", "_")
    assert f"Welcome to project {project_repo}’s API docs!" in index_html, index_html


@then("requirements should be generated")
def check_reqs_generated(context: behave.runner.Context):
    """Check that new project docs are generated."""
    reqs_path = context.root_project_dir / "src" / "requirements.in"
    assert reqs_path.is_file()
    assert (
        "This file is autogenerated by pip-compile"
        in (context.root_project_dir / "src" / "requirements.txt").read_text()
    )


@then("{dependency} should be in the requirements")
def check_dependency_in_reqs(context: behave.runner.Context, dependency: str):
    reqs_path = context.root_project_dir / "src" / "requirements.txt"
    assert dependency in reqs_path.read_text()


@then("Code cell with node tag should be converted into kedro node")
def check_cell_conversion(context: behave.runner.Context):
    converted_file = (
        context.root_project_dir
        / "src"
        / context.package_name
        / "nodes"
        / "hello_world.py"
    )
    assert "Hello World!" in converted_file.read_text()


@then("{path} must not exist")
def check_path_doesnt_exist(context: behave.runner.Context, path: str):
    path = context.root_project_dir / path
    assert not path.exists()


@then("{filepath} file must exist")
def check_file_exists(context: behave.runner.Context, filepath: str):
    filepath = context.root_project_dir / filepath
    assert filepath.is_file()
