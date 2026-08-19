from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path


HEARTBEAT_PATH = Path(os.getenv("BOT_HEARTBEAT_PATH", "./data/bot_heartbeat"))


def write_heartbeat(path: Path = HEARTBEAT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(str(int(time.time())), encoding="ascii")
    temp_path.replace(path)


async def run_heartbeat(interval_seconds: int = 60) -> None:
    while True:
        write_heartbeat()
        await asyncio.sleep(max(1, int(interval_seconds)))
