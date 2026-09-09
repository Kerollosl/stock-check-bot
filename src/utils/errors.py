import os
import re


def sanitize_error(error: BaseException) -> str:
    """Return a useful error string without leaking configured secrets."""
    message = str(error)
    message = re.sub(
        r"(?i)(api[_-]?key|token|secret)(=|%3D)[^&\s]+",
        r"\1\2***",
        message,
    )

    for variable in ("FRED_API_KEY",):
        value = os.getenv(variable)
        if value:
            message = message.replace(value, "***")

    message = " ".join(message.splitlines())

    return f"{type(error).__name__}: {message}" if message else type(error).__name__
