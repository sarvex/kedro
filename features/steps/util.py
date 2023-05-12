"""Common functions for e2e testing.
"""

import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from time import sleep, time
from typing import Any, Callable, Iterator, List

import pandas as pd


def get_sample_csv_content():
    return """col1, col2, col3
    1, 2, 3
    4, 5, 6
    """


def get_sample_data_frame():
    data = {"col1": [1, 2], "col2": [4, 5], "col3": [5, 6]}
    return pd.DataFrame(data)


def create_temp_csv():
    _, csv_file_path = tempfile.mkstemp(suffix=".csv")
    return csv_file_path


def create_sample_csv():
    csv_file_path = create_temp_csv()
    with open(csv_file_path, mode="w", encoding="utf-8") as output_file:
        output_file.write(get_sample_csv_content())
    return csv_file_path


@contextmanager
def chdir(path: Path) -> Iterator:
    """Context manager to help execute code in a different directory.

    Args:
        path: directory to change to.

    Yields:
        None
    """
    old_pwd = os.getcwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(old_pwd)


class WaitForException(Exception):
    pass


def wait_for(
    func: Callable,
    timeout_: int = 10,
    print_error: bool = False,
    sleep_for: int = 1,
    **kwargs,
) -> Any:
    """Run specified function until it returns expected result until timeout.

    Args:
        func: Specified function.
        timeout_: Time out in seconds. Defaults to 10.
        print_error: whether any exceptions raised should be printed.
            Defaults to False.
        sleep_for: Execute func every specified number of seconds.
            Defaults to 1.
        **kwargs: Arguments to be passed to func.

    Raises:
         WaitForException: if func doesn't return expected result within the
         specified time.

    Returns:
        Function return.

    """
    end = time() + timeout_
    while time() <= end:
        try:
            return func(**kwargs)
        except Exception as err:  # pylint: disable=broad-except
            if print_error:
                print(err)

        sleep(sleep_for)
    raise WaitForException(
        f"func: {func}, didn't return within specified timeout: {timeout_}"
    )


def get_logline_count(logfile: str) -> int:
    """Get line count in logfile

    Note: If logfile doesn't exist will return 0

    Args:
        logfile: path to logfile

    Returns:
        line count of logfile
    """
    try:
        with open(logfile, encoding="utf-8") as file_handle:
            return sum(1 for _ in file_handle)
    except FileNotFoundError:
        return 0


def get_last_logline(logfile: str) -> str:
    """Get last line of logfile

    Args:
        logfile: path to logfile

    Returns:
        last line of logfile
    """
    line = ""
    with open(logfile, encoding="utf-8") as file_handle:
        for line in file_handle:
            pass

    return line


def get_logfile_path(proj_dir: Path) -> str:
    """
    Helper function to fet full path of `pipeline.log` inside project

    Args:
        proj_dir: path to proj_dir

    Returns:
        path to `pipeline.log`
    """
    log_file = (proj_dir / "logs" / "visualization" / "pipeline.log").absolute()
    return str(log_file)


def parse_csv(text: str) -> List[str]:
    """Parse comma separated **double quoted** strings in behave steps

    Args:
        text: double quoted comma separated string

    Returns:
        List of string tokens
    """
    return re.findall(r"\"(.+?)\"\s*,?", text)
