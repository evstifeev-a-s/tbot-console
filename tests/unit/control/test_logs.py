from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import pytest

from tbot_console.control.logs import LOG_FORMAT, configure_logging


@pytest.fixture
def added_file_handlers():
    root = logging.getLogger()
    before = list(root.handlers)

    def added() -> list[RotatingFileHandler]:
        return [h for h in root.handlers if isinstance(h, RotatingFileHandler) and h not in before]

    yield added
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


def test_a_log_file_gets_its_folder_and_the_shared_format(tmp_path, added_file_handlers):
    log_path = tmp_path / "nested" / "unit.log"
    configure_logging(str(log_path))
    assert log_path.parent.is_dir()
    [handler] = added_file_handlers()
    assert handler.formatter is not None and handler.formatter._fmt == LOG_FORMAT


def test_the_same_file_is_attached_once(tmp_path, added_file_handlers):
    log_path = str(tmp_path / "unit.log")
    configure_logging(log_path)
    configure_logging(log_path)
    assert len(added_file_handlers()) == 1


def test_without_a_path_no_file_is_attached(added_file_handlers):
    configure_logging()
    assert added_file_handlers() == []
