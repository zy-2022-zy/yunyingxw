"""统一管理网页自动化操作之间的节奏。"""

import asyncio
import random


async def wait_between_actions(
    min_seconds: float = 0.8,
    max_seconds: float = 1.5,
) -> None:
    """在连续的主动页面操作之间随机等待，默认间隔 0.8～1.5 秒。"""
    if min_seconds < 0 or max_seconds < min_seconds:
        raise ValueError("等待时间范围无效：需满足 0 <= min_seconds <= max_seconds。")

    await asyncio.sleep(random.uniform(min_seconds, max_seconds))
