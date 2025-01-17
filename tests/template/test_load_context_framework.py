import re
import sys

import pytest
import toml

from kedro import __version__ as kedro_version
from kedro.framework.context import KedroContext, load_context
from kedro.framework.project import Validator, _ProjectSettings
from kedro.framework.startup import _get_project_metadata


@pytest.fixture(autouse=True)
def mock_logging_config(mocker):
    # Disable logging.config.dictConfig in KedroContext._setup_logging as
    # it changes logging.config and affects other unit tests
    mocker.patch("logging.config.dictConfig")


def _create_kedro_config(project_path, payload):
    kedro_conf = project_path / "pyproject.toml"
    kedro_conf.parent.mkdir(parents=True, exist_ok=True)
    toml_str = toml.dumps(payload)
    kedro_conf.write_text(toml_str)


class MyContext(KedroContext):
    pass


class MockSettings(_ProjectSettings):
    _HOOKS = Validator("HOOKS", default=())
    _CONTEXT_CLASS = Validator("CONTEXT_CLASS", default=lambda *_: MyContext)


@pytest.fixture
def mock_settings(mocker):
    mocked_settings = MockSettings()
    mocker.patch("kedro.framework.project.settings", mocked_settings)
    mocker.patch("kedro.framework.context.context.settings", mocked_settings)


@pytest.mark.usefixtures("fake_project_cli")
class TestLoadContext:
    def test_valid_context(self, fake_repo_path, mocker):
        """Test getting project context."""
        get_project_metadata_mock = mocker.patch(
            "kedro.framework.context.context._get_project_metadata",
            wraps=_get_project_metadata,
        )
        result = load_context(str(fake_repo_path))
        assert result.package_name == "dummy_package"
        assert str(fake_repo_path.resolve() / "src") in sys.path
        get_project_metadata_mock.assert_called_with(fake_repo_path)

    def test_valid_context_with_env(self, mocker, monkeypatch, fake_repo_path):
        """Test getting project context when Kedro config environment is
        specified in the environment variable.
        """
        mocker.patch("kedro.config.config.ConfigLoader.get")
        monkeypatch.setenv("KEDRO_ENV", "my_fake_env")
        result = load_context(str(fake_repo_path))
        assert result.env == "my_fake_env"

    def test_invalid_path(self, tmp_path):
        """Test for loading context from an invalid path."""
        other_path = tmp_path / "other"
        other_path.mkdir()
        pattern = "Could not find the project configuration file 'pyproject.toml'"
        with pytest.raises(RuntimeError, match=re.escape(pattern)):
            load_context(str(other_path))

    def test_pyproject_toml_has_missing_mandatory_keys(self, fake_repo_path):
        payload = {
            "tool": {
                "kedro": {"fake_key": "fake_value", "project_version": kedro_version}
            }
        }
        _create_kedro_config(fake_repo_path, payload)

        pattern = (
            "Missing required keys ['package_name', 'project_name'] "
            "from 'pyproject.toml'."
        )
        with pytest.raises(RuntimeError, match=re.escape(pattern)):
            load_context(str(fake_repo_path))

    def test_pyproject_toml_has_extra_keys(self, fake_repo_path, fake_metadata):
        project_name = "Test Project"
        payload = {
            "tool": {
                "kedro": {
                    "project_version": kedro_version,
                    "project_name": project_name,
                    "package_name": fake_metadata.package_name,
                    "unexpected_key": "hello",
                }
            }
        }
        _create_kedro_config(fake_repo_path, payload)

        pattern = (
            "Found unexpected keys in 'pyproject.toml'. Make sure it "
            "only contains the following keys: ['package_name', "
            "'project_name', 'project_version', 'source_dir']."
        )
        with pytest.raises(RuntimeError, match=re.escape(pattern)):
            load_context(str(fake_repo_path))

    def test_settings_py_has_no_context_path(self, fake_repo_path):
        """Test for loading default `KedroContext` context."""
        payload = {
            "tool": {
                "kedro": {
                    "package_name": "dummy_package",
                    "project_version": kedro_version,
                    "project_name": "fake_project",
                }
            }
        }
        _create_kedro_config(fake_repo_path, payload)

        context = load_context(str(fake_repo_path))
        assert isinstance(context, KedroContext)
        assert context.__class__ is KedroContext

    @pytest.mark.usefixtures("mock_settings")
    def test_settings_py_has_context_path(
        self,
        fake_repo_path,
        fake_metadata,
    ):
        """Test for loading custom `ProjectContext` context."""
        payload = {
            "tool": {
                "kedro": {
                    "package_name": fake_metadata.package_name,
                    "project_version": kedro_version,
                    "project_name": "fake_project",
                }
            }
        }

        _create_kedro_config(fake_repo_path, payload)

        context = load_context(str(fake_repo_path))

        assert isinstance(context, KedroContext)
        assert context.__class__ is not KedroContext
        assert context.__class__.__name__ == "MyContext"
