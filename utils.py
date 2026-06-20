"""Shared logging and retry helpers for the APOD bot."""

import functools
import logging
import os
import time

_LOGGING_CONFIGURED = False


def setup_logging() -> None:
    """Configure root logging once, honoring the LOG_LEVEL env var."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Azure/google libraries are very chatty at INFO; keep them at WARNING.
    for noisy in ("azure", "urllib3", "googleapiclient", "google"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _LOGGING_CONFIGURED = True


def retry(
    exceptions=(Exception,),
    tries: int = 4,
    delay: float = 3.0,
    backoff: float = 2.0,
    max_delay: float = 60.0,
):
    """Retry a function with exponential backoff.

    Args:
        exceptions: Exception type(s) that trigger a retry.
        tries: Total attempts before giving up.
        delay: Initial sleep between attempts, in seconds.
        backoff: Multiplier applied to the delay after each failure.
        max_delay: Upper bound on the sleep between attempts.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            log = logging.getLogger(func.__module__)
            current_delay = delay
            for attempt in range(1, tries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt >= tries:
                        log.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            tries,
                            exc,
                        )
                        raise
                    log.warning(
                        "%s failed (attempt %d/%d): %s; retrying in %.1fs",
                        func.__name__,
                        attempt,
                        tries,
                        exc,
                        current_delay,
                    )
                    time.sleep(current_delay)
                    current_delay = min(current_delay * backoff, max_delay)

        return wrapper

    return decorator
