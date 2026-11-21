"""Pins the log file encoding: the hook log is UTF-8 on every platform.

Without an explicit encoding the handler writes in the locale code page
(cp1252, cp932, ...) where the system is not in UTF-8 mode, e.g. Windows.
There the log is not valid UTF-8, and a character the code page cannot encode
makes logging drop the record and print a traceback to stderr instead."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


UNICODE_MESSAGE = "unicode probe — café 日本語 ✓"


def test_unicode_log_message_is_written_as_utf8(hook_module: Any) -> None:
    hook_module.info(UNICODE_MESSAGE)

    raw = Path(hook_module.LOG_FILE).read_bytes()
    assert UNICODE_MESSAGE in raw.decode("utf-8")


def test_log_handler_encoding_does_not_depend_on_the_locale(hook_module: Any) -> None:
    # A UTF-8 locale (Linux CI) hides the bug in the content check above, so
    # pin the handler's configured encoding as well.
    hook_module.info("probe")

    handlers = [
        h for h in logging.getLogger("langfuse_hook").handlers
        if isinstance(h, RotatingFileHandler)
    ]
    assert len(handlers) == 1
    assert handlers[0].encoding == "utf-8"
