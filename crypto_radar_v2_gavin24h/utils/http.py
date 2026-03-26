"""
Crypto Radar V2 - HTTP 工具
统一的异步 HTTP 请求，带重试、限频、代理支持
"""
from __future__ import annotations
import asyncio
import logging
import aiohttp
from typing import Any, Optional

logger = logging.getLogger("radar.http")


class HttpClient:
    def __init__(
        self,
        timeout: int = 15,
        retries: int = 3,
        proxy: str = "",
        rate_limit_ms: int = 100,
    ):
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.retries = retries
        self.proxy = proxy or None
        self.rate_limit_ms = rate_limit_ms
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()
        self._last_request_time: float = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def _rate_limit(self):
        if self.rate_limit_ms > 0:
            now = asyncio.get_event_loop().time()
            elapsed = (now - self._last_request_time) * 1000
            if elapsed < self.rate_limit_ms:
                await asyncio.sleep((self.rate_limit_ms - elapsed) / 1000)
            self._last_request_time = asyncio.get_event_loop().time()

    async def get_json(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> Any:
        """GET请求返回JSON"""
        async with self._lock:
            await self._rate_limit()

        session = await self._get_session()
        last_err = None

        for attempt in range(1, self.retries + 1):
            try:
                async with session.get(
                    url, params=params, headers=headers, proxy=self.proxy
                ) as resp:
                    if resp.status == 429:
                        wait = min(2 ** attempt, 10)
                        logger.warning(f"Rate limited {url}, wait {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_err = e
                if attempt < self.retries:
                    wait = min(2 ** attempt, 8)
                    logger.warning(
                        f"HTTP error {url} attempt {attempt}/{self.retries}: {e}, retry in {wait}s"
                    )
                    await asyncio.sleep(wait)

        logger.error(f"HTTP failed after {self.retries} retries: {url} - {last_err}")
        return None

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
