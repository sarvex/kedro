"""``ParallelRunner`` is an ``AbstractRunner`` implementation. It can
be used to run the ``Pipeline`` in parallel groups formed by toposort.
"""
import logging.config
import multiprocessing
import os
import pickle
import sys
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from itertools import chain
from multiprocessing.managers import BaseProxy, SyncManager  # type: ignore
from multiprocessing.reduction import ForkingPickler
from pickle import PicklingError
from typing import Any, Dict, Iterable, Set

from kedro.io import DataCatalog, DataSetError, MemoryDataSet
from kedro.pipeline import Pipeline
from kedro.pipeline.node import Node
from kedro.runner.runner import AbstractRunner, run_node

# see https://github.com/python/cpython/blob/master/Lib/concurrent/futures/process.py#L114
_MAX_WINDOWS_WORKERS = 61


class _SharedMemoryDataSet:
    """``_SharedMemoryDataSet`` is a wrapper class for a shared MemoryDataSet in SyncManager.
    It is not inherited from AbstractDataSet class.
    """

    def __init__(self, manager: SyncManager):
        """Creates a new instance of ``_SharedMemoryDataSet``,
        and creates shared memorydataset attribute.

        Args:
            manager: An instance of multiprocessing manager for shared objects.

        """
        self.shared_memory_dataset = manager.MemoryDataSet()  # type: ignore

    def __getattr__(self, name):
        # This if condition prevents recursive call when deserializing
        if name == "__setstate__":
            raise AttributeError()
        return getattr(self.shared_memory_dataset, name)

    def save(self, data: Any):
        """Calls save method of a shared MemoryDataSet in SyncManager."""
        try:
            self.shared_memory_dataset.save(data)
        except Exception as exc:  # pylint: disable=broad-except
            # Checks if the error is due to serialisation or not
            try:
                pickle.dumps(data)
            except Exception as exc:  # SKIP_IF_NO_SPARK
                raise DataSetError(
                    f"{str(data.__class__)} cannot be serialized. ParallelRunner "
                    "implicit memory datasets can only be used with serializable data"
                ) from exc
            else:
                raise exc


class ParallelRunnerManager(SyncManager):
    """``ParallelRunnerManager`` is used to create shared ``MemoryDataSet``
    objects as default data sets in a pipeline.
    """


ParallelRunnerManager.register(  # pylint: disable=no-member
    "MemoryDataSet", MemoryDataSet
)


def _bootstrap_subprocess(package_name: str, conf_logging: Dict[str, Any]):
    # pylint: disable=import-outside-toplevel,cyclic-import
    from kedro.framework.project import configure_project

    configure_project(package_name)
    logging.config.dictConfig(conf_logging)


def _run_node_synchronization(  # pylint: disable=too-many-arguments
    node: Node,
    catalog: DataCatalog,
    is_async: bool = False,
    run_id: str = None,
    package_name: str = None,
    conf_logging: Dict[str, Any] = None,
) -> Node:
    """Run a single `Node` with inputs from and outputs to the `catalog`.
    `KedroSession` instance is activated in every subprocess because of Windows
    (and latest OSX with Python 3.8) limitation.
    Windows has no "fork", so every subprocess is a brand new process
    created via "spawn", hence the need to a) setup the logging, b) register
    the hooks, and c) activate `KedroSession` in every subprocess.

    Args:
        node: The ``Node`` to run.
        catalog: A ``DataCatalog`` containing the node's inputs and outputs.
        is_async: If True, the node inputs and outputs are loaded and saved
            asynchronously with threads. Defaults to False.
        run_id: The id of the pipeline run.
        package_name: The name of the project Python package.
        conf_logging: A dictionary containing logging configuration.

    Returns:
        The node argument.

    """
    if multiprocessing.get_start_method() == "spawn" and package_name:  # type: ignore
        conf_logging = conf_logging or {}
        _bootstrap_subprocess(package_name, conf_logging)

    return run_node(node, catalog, is_async, run_id)


class ParallelRunner(AbstractRunner):
    """``ParallelRunner`` is an ``AbstractRunner`` implementation. It can
    be used to run the ``Pipeline`` in parallel groups formed by toposort.
    Please note that this `runner` implementation validates dataset using the
    ``_validate_catalog`` method, which checks if any of the datasets are
    single process only using the `_SINGLE_PROCESS` dataset attribute.
    """

    def __init__(self, max_workers: int = None, is_async: bool = False):
        """
        Instantiates the runner by creating a Manager.

        Args:
            max_workers: Number of worker processes to spawn. If not set,
                calculated automatically based on the pipeline configuration
                and CPU core count. On windows machines, the max_workers value
                cannot be larger than 61 and will be set to min(61, max_workers).
            is_async: If True, the node inputs and outputs are loaded and saved
                asynchronously with threads. Defaults to False.

        Raises:
            ValueError: bad parameters passed
        """
        super().__init__(is_async=is_async)
        self._manager = ParallelRunnerManager()
        self._manager.start()  # pylint: disable=consider-using-with

        # This code comes from the concurrent.futures library
        # https://github.com/python/cpython/blob/master/Lib/concurrent/futures/process.py#L588
        if max_workers is None:
            # NOTE: `os.cpu_count` might return None in some weird cases.
            # https://github.com/python/cpython/blob/3.7/Modules/posixmodule.c#L11431
            max_workers = os.cpu_count() or 1
            if sys.platform == "win32":
                max_workers = min(_MAX_WINDOWS_WORKERS, max_workers)

        self._max_workers = max_workers

    def __del__(self):
        self._manager.shutdown()

    def create_default_data_set(  # type: ignore
        self, ds_name: str
    ) -> _SharedMemoryDataSet:
        """Factory method for creating the default dataset for the runner.

        Args:
            ds_name: Name of the missing dataset.

        Returns:
            An instance of ``_SharedMemoryDataSet`` to be used for all
            unregistered datasets.

        """
        return _SharedMemoryDataSet(self._manager)

    @classmethod
    def _validate_nodes(cls, nodes: Iterable[Node]):
        """Ensure all tasks are serializable."""
        unserializable = []
        for node in nodes:
            try:
                ForkingPickler.dumps(node)
            except (AttributeError, PicklingError):
                unserializable.append(node)

        if unserializable:
            raise AttributeError(
                f"The following nodes cannot be serialized: {sorted(unserializable)}\n"
                f"In order to utilize multiprocessing you need to make sure all nodes "
                f"are serializable, i.e. nodes should not include lambda "
                f"functions, nested functions, closures, etc.\nIf you "
                f"are using custom decorators ensure they are correctly decorated using "
                f"functools.wraps()."
            )

    @classmethod
    def _validate_catalog(cls, catalog: DataCatalog, pipeline: Pipeline):
        """Ensure that all data sets are serializable and that we do not have
        any non proxied memory data sets being used as outputs as their content
        will not be synchronized across threads.
        """

        data_sets = catalog._data_sets  # pylint: disable=protected-access

        unserializable = []
        for name, data_set in data_sets.items():
            if getattr(data_set, "_SINGLE_PROCESS", False):  # SKIP_IF_NO_SPARK
                unserializable.append(name)
                continue
            try:
                ForkingPickler.dumps(data_set)
            except (AttributeError, PicklingError):
                unserializable.append(name)

        if unserializable:
            raise AttributeError(
                f"The following data sets cannot be used with multiprocessing: "
                f"{sorted(unserializable)}\nIn order to utilize multiprocessing you "
                f"need to make sure all data sets are serializable, i.e. data sets "
                f"should not make use of lambda functions, nested functions, closures "
                f"etc.\nIf you are using custom decorators ensure they are correctly "
                f"decorated using functools.wraps()."
            )

        if memory_data_sets := [
            name
            for name, data_set in data_sets.items()
            if (
                name in pipeline.all_outputs()
                and isinstance(data_set, MemoryDataSet)
                and not isinstance(data_set, BaseProxy)
            )
        ]:
            raise AttributeError(
                f"The following data sets are memory data sets: "
                f"{sorted(memory_data_sets)}\n"
                f"ParallelRunner does not support output to externally created "
                f"MemoryDataSets"
            )

    def _get_required_workers_count(self, pipeline: Pipeline):
        """
        Calculate the max number of processes required for the pipeline,
        limit to the number of CPU cores.
        """
        # Number of nodes is a safe upper-bound estimate.
        # It's also safe to reduce it by the number of layers minus one,
        # because each layer means some nodes depend on other nodes
        # and they can not run in parallel.
        # It might be not a perfect solution, but good enough and simple.
        required_processes = len(pipeline.nodes) - len(pipeline.grouped_nodes) + 1

        return min(required_processes, self._max_workers)

    def _run(  # pylint: disable=too-many-locals,useless-suppression
        self, pipeline: Pipeline, catalog: DataCatalog, run_id: str = None
    ) -> None:
        """The abstract interface for running pipelines.

        Args:
            pipeline: The ``Pipeline`` to run.
            catalog: The ``DataCatalog`` from which to fetch data.
            run_id: The id of the run.

        Raises:
            AttributeError: When the provided pipeline is not suitable for
                parallel execution.
            RuntimeError: If the runner is unable to schedule the execution of
                all pipeline nodes.
            Exception: In case of any downstream node failure.

        """
        # pylint: disable=import-outside-toplevel,cyclic-import
        from kedro.framework.session.session import get_current_session

        nodes = pipeline.nodes
        self._validate_catalog(catalog, pipeline)
        self._validate_nodes(nodes)

        load_counts = Counter(chain.from_iterable(n.inputs for n in nodes))
        node_dependencies = pipeline.node_dependencies
        todo_nodes = set(node_dependencies.keys())
        done_nodes = set()  # type: Set[Node]
        futures = set()
        done = None
        max_workers = self._get_required_workers_count(pipeline)

        from kedro.framework.project import PACKAGE_NAME

        session = get_current_session(silent=True)
        # pylint: disable=protected-access
        conf_logging = session._get_logging_config() if session else None

        with ProcessPoolExecutor(max_workers=max_workers) as pool:
            while True:
                ready = {n for n in todo_nodes if node_dependencies[n] <= done_nodes}
                todo_nodes -= ready
                for node in ready:
                    futures.add(
                        pool.submit(
                            _run_node_synchronization,
                            node,
                            catalog,
                            self._is_async,
                            run_id,
                            package_name=PACKAGE_NAME,
                            conf_logging=conf_logging,
                        )
                    )
                if not futures:
                    if todo_nodes:
                        debug_data = {
                            "todo_nodes": todo_nodes,
                            "done_nodes": done_nodes,
                            "ready_nodes": ready,
                            "done_futures": done,
                        }
                        debug_data_str = "\n".join(
                            f"{k} = {v}" for k, v in debug_data.items()
                        )
                        raise RuntimeError(
                            f"Unable to schedule new tasks although some nodes "
                            f"have not been run:\n{debug_data_str}"
                        )
                    break  # pragma: no cover
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        node = future.result()
                    except Exception:
                        self._suggest_resume_scenario(pipeline, done_nodes)
                        raise
                    done_nodes.add(node)

                    # Decrement load counts, and release any datasets we
                    # have finished with. This is particularly important
                    # for the shared, default datasets we created above.
                    for data_set in node.inputs:
                        load_counts[data_set] -= 1
                        if (
                            load_counts[data_set] < 1
                            and data_set not in pipeline.inputs()
                        ):
                            catalog.release(data_set)
                    for data_set in node.outputs:
                        if (
                            load_counts[data_set] < 1
                            and data_set not in pipeline.outputs()
                        ):
                            catalog.release(data_set)
