"""Thread cancellation must not release ownership while its writes are still running."""

import asyncio
from collections.abc import Callable
from typing import Any


async def run_blocking(function: Callable, *args: Any):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:  # noqa: BLE001 - task.result below preserves the original exception
            # Re-raised below unless cancellation takes precedence.
            break
    if cancelled:
        # Retrieve any thread exception before honouring cancellation.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()
