from __future__ import annotations

import abc
import logging
import time

import httpx

from ..config import HTTP_TIMEOUT, MAX_RETRIES, RETRY_BACKOFF_BASE, USER_AGENT
from ..models import PlanInfo

logger = logging.getLogger(__name__)


class BaseScraper(abc.ABC):
    source_name: str = ""

    def __init__(self) -> None:
        self.client = httpx.Client(
            timeout=HTTP_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _get_with_retry(self, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.client.get(url, **kwargs)
                resp.raise_for_status()
                return resp
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                last_exc = e
                wait = RETRY_BACKOFF_BASE ** attempt
                logger.warning(
                    "[%s] リクエスト失敗 (attempt %d/%d): %s - %s秒待機",
                    self.source_name, attempt + 1, MAX_RETRIES, e, wait,
                )
                time.sleep(wait)
        raise last_exc  # type: ignore[misc]

    @abc.abstractmethod
    def scrape(self) -> list[PlanInfo]:
        ...
