"""``HoloviewsWriter`` saves Holoviews objects as image file(s) to an underlying
filesystem (e.g. local, S3, GCS)."""

import io
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Dict, TypeVar

import fsspec
import holoviews as hv

from kedro.io.core import (
    AbstractVersionedDataSet,
    DataSetError,
    Version,
    get_filepath_str,
    get_protocol_and_path,
)

# HoloViews to be passed in `hv.save()`
HoloViews = TypeVar("HoloViews")


class HoloviewsWriter(AbstractVersionedDataSet):
    """``HoloviewsWriter`` saves Holoviews objects to image file(s) in an underlying
    filesystem (e.g. local, S3, GCS).

    Example:
    ::

        >>> import holoviews as hv
        >>> from kedro.extras.datasets.holoviews import HoloviewsWriter
        >>>
        >>> curve = hv.Curve(range(10))
        >>> holoviews_writer = HoloviewsWriter("/tmp/holoviews")
        >>>
        >>> holoviews_writer.save(curve)

    """

    DEFAULT_SAVE_ARGS = {"fmt": "png"}  # type: Dict[str, Any]

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        filepath: str,
        fs_args: Dict[str, Any] = None,
        credentials: Dict[str, Any] = None,
        save_args: Dict[str, Any] = None,
        version: Version = None,
    ) -> None:
        """Creates a new instance of ``HoloviewsWriter``.

        Args:
            filepath: Filepath in POSIX format to a text file prefixed with a protocol like `s3://`.
                If prefix is not provided, `file` protocol (local filesystem) will be used.
                The prefix should be any protocol supported by ``fsspec``.
                Note: `http(s)` doesn't support versioning.
            fs_args: Extra arguments to pass into underlying filesystem class constructor
                (e.g. `{"project": "my-project"}` for ``GCSFileSystem``), as well as
                to pass to the filesystem's `open` method through nested key `open_args_save`.
                Here you can find all available arguments for `open`:
                https://filesystem-spec.readthedocs.io/en/latest/api.html#fsspec.spec.AbstractFileSystem.open
                All defaults are preserved, except `mode`, which is set to `wb` when saving.
            credentials: Credentials required to get access to the underlying filesystem.
                E.g. for ``S3FileSystem`` it should look like:
                `{'key': '<id>', 'secret': '<key>'}}`
            save_args: Extra save args passed to `holoviews.save()`. See
                http://holoviews.org/reference_manual/holoviews.util.html#holoviews.util.save
            version: If specified, should be an instance of
                ``kedro.io.core.Version``. If its ``load`` attribute is
                None, the latest version will be loaded. If its ``save``
                attribute is None, save version will be autogenerated.
        """
        _credentials = deepcopy(credentials) or {}
        _fs_args = deepcopy(fs_args) or {}
        _fs_open_args_save = _fs_args.pop("open_args_save", {})
        _fs_open_args_save.setdefault("mode", "wb")

        protocol, path = get_protocol_and_path(filepath, version)
        if protocol == "file":
            _fs_args.setdefault("auto_mkdir", True)

        self._protocol = protocol
        self._fs = fsspec.filesystem(self._protocol, **_credentials, **_fs_args)

        super().__init__(
            filepath=PurePosixPath(path),
            version=version,
            exists_function=self._fs.exists,
            glob_function=self._fs.glob,
        )

        self._fs_open_args_save = _fs_open_args_save

        # Handle default save arguments
        self._save_args = deepcopy(self.DEFAULT_SAVE_ARGS)
        if save_args is not None:
            self._save_args.update(save_args)

    def _describe(self) -> Dict[str, Any]:
        return dict(
            filepath=self._filepath,
            protocol=self._protocol,
            save_args=self._save_args,
            version=self._version,
        )

    def _load(self) -> str:
        raise DataSetError(f"Loading not supported for `{self.__class__.__name__}`")

    def _save(self, data: HoloViews) -> None:
        bytes_buffer = io.BytesIO()
        hv.save(data, bytes_buffer, **self._save_args)

        save_path = get_filepath_str(self._get_save_path(), self._protocol)
        with self._fs.open(save_path, **self._fs_open_args_save) as fs_file:
            fs_file.write(bytes_buffer.getvalue())

        self._invalidate_cache()

    def _exists(self) -> bool:
        load_path = get_filepath_str(self._get_load_path(), self._protocol)
        return self._fs.exists(load_path)

    def _release(self) -> None:
        super()._release()
        self._invalidate_cache()

    def _invalidate_cache(self) -> None:
        """Invalidate underlying filesystem caches."""
        filepath = get_filepath_str(self._filepath, self._protocol)
        self._fs.invalidate_cache(filepath)
