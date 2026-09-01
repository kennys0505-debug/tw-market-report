from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class SourceError(RuntimeError):
    pass


class HttpClient:
    def __init__(self, timeout: int = 25, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries
        self.headers = {
            # Some official market-data gateways reject library/bot user agents
            # even though the endpoint is public.  Use ordinary browser request
            # headers so scheduled GitHub runners receive the same public JSON
            # that an investor opening the TPEx page receives.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/140.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,text/html,text/csv;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
            "Cache-Control": "no-cache",
        }

    def _request_bytes(self, request: urllib.request.Request) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as error:
                last_error = error
                if attempt < self.retries:
                    time.sleep(1.5 * (attempt + 1))
        raise SourceError(f"Request failed: {request.full_url}: {last_error}")

    def _headers_for(self, url: str) -> dict[str, str]:
        headers = dict(self.headers)
        if urllib.parse.urlparse(url).hostname == "www.tpex.org.tw":
            headers.update({
                "Referer": "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/mi-index.html",
                "Origin": "https://www.tpex.org.tw",
                "X-Requested-With": "XMLHttpRequest",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            })
        return headers

    def get_bytes(self, url: str) -> bytes:
        return self._request_bytes(urllib.request.Request(url, headers=self._headers_for(url)))

    def get_text(self, url: str) -> str:
        payload = self.get_bytes(url)
        for encoding in ("utf-8-sig", "utf-8", "big5"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="replace")

    def get_json(self, url: str):
        try:
            return json.loads(self.get_text(url))
        except json.JSONDecodeError as error:
            raise SourceError(f"Invalid JSON from {url}: {error}") from error

    def post_form_text(self, url: str, fields: dict[str, str]) -> str:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        headers = {**self._headers_for(url), "Content-Type": "application/x-www-form-urlencoded"}
        payload = self._request_bytes(urllib.request.Request(url, data=data, headers=headers, method="POST"))
        for encoding in ("utf-8-sig", "utf-8", "big5"):
            try:
                return payload.decode(encoding)
            except UnicodeDecodeError:
                continue
        return payload.decode("utf-8", errors="replace")
