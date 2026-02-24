#!/usr/bin/env python3
"""
Retry Utility — Exponential Backoff for Transient Failures

Provides a decorator for retrying failed function calls with exponential
backoff. Useful for handling transient API errors, network hiccups, etc.

Usage:
    @retry(max_attempts=3, backoff_factor=1.0, backoff_jitter=0.1)
    def flaky_api_call():
        return requests.get(url).json()

    result = flaky_api_call()  # Will retry with 1s, 2s, 4s delays
"""

import logging
import time
from functools import wraps
from typing import Callable, Type, Tuple, Any

logger = logging.getLogger(__name__)


def retry(
    max_attempts: int = 3,
    backoff_factor: float = 1.0,
    backoff_jitter: float = 0.1,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """
    Decorator to retry a function with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (default 3 = 1 + 2 retries)
        backoff_factor: Initial backoff duration in seconds (default 1.0)
                        Delays will be: 1s, 2s, 4s, ...
        backoff_jitter: Random jitter ±% to add to delay (default 0.1 = ±10%)
        exceptions: Tuple of exception types to catch (default all Exceptions)

    Returns:
        Decorated function that retries on failure

    Example:
        @retry(max_attempts=3, backoff_factor=1.0)
        def get_account_info():
            return api.get("/account/info")

        info = get_account_info()  # Retries automatically on transient errors
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            last_exception = None
            
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt == max_attempts:
                        # Final attempt failed — re-raise
                        logger.error(
                            f"[{func.__name__}] All {max_attempts} attempts failed: {e}"
                        )
                        raise
                    
                    # Calculate backoff with exponential growth
                    delay = backoff_factor * (2 ** (attempt - 1))
                    
                    # Add jitter (±random % of delay)
                    import random
                    jitter = delay * random.uniform(-backoff_jitter, backoff_jitter)
                    delay = max(0.1, delay + jitter)
                    
                    logger.warning(
                        f"[{func.__name__}] Attempt {attempt}/{max_attempts} failed: {e} "
                        f"— retrying in {delay:.2f}s"
                    )
                    time.sleep(delay)
            
            # Should never reach here, but just in case
            if last_exception:
                raise last_exception
        
        return wrapper
    return decorator
