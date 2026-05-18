from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx


SHORT_LINK_PATTERN = r"https?://v\.douyin\.com/[\w\-]+/?"
VIDEO_ID_PATTERNS = [
    r"/video/(\d+)",
    r"modal_id=(\d+)",
    r"/share/video/(\d+)",
]


@dataclass(slots=True)
class DouyinDownloadInfo:
    share_url: str
    resolved_url: str
    video_id: str | None
    title: str
    download_url: str


class DouyinDownloader:
    def __init__(self) -> None:
        self._client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=20.0),
            headers={
                "Referer": "https://www.douyin.com/",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
            trust_env=False,
        )

    def extract_short_link(self, text: str) -> str | None:
        match = re.search(SHORT_LINK_PATTERN, text)
        return match.group(0) if match else None

    def looks_like_douyin(self, text: str) -> bool:
        return "douyin.com" in text or "iesdouyin.com" in text

    def media_headers(self) -> dict[str, str]:
        return dict(self._client.headers)

    def parse_share_text(self, text: str) -> tuple[str | None, str]:
        share_url = self.extract_short_link(text)
        cleaned = text
        if share_url:
            cleaned = cleaned.replace(share_url, " ")
        cleaned = re.sub(r"打开抖音.*?搜索.*", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"复制此链接.*", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，,。;；")
        return share_url, cleaned

    def fetch_info(self, source: str) -> DouyinDownloadInfo:
        share_url = self.extract_short_link(source) or source.strip()
        resolved_url = self.resolve_share_url(share_url)
        video_id = self.extract_video_id(resolved_url)
        html = self.fetch_share_page(video_id, resolved_url)
        page_data = self._extract_json_block(html)
        if page_data is None:
            raise RuntimeError("Unable to parse Douyin share page JSON payload.")
        video_info = self._walk_for_video_info(page_data)
        if not video_info:
            raise RuntimeError("Unable to locate video info in Douyin payload.")
        title = (
            video_info.get("desc")
            or video_info.get("share_info", {}).get("share_title")
            or video_id
            or "douyin-video"
        )
        video = video_info.get("video")
        if not isinstance(video, dict):
            raise RuntimeError("Douyin payload does not contain a video object.")
        download_url = self._extract_url_from_video_dict(video)
        if not download_url:
            raise RuntimeError("Unable to extract playable video URL from Douyin payload.")
        return DouyinDownloadInfo(
            share_url=share_url,
            resolved_url=resolved_url,
            video_id=video_id,
            title=title,
            download_url=download_url,
        )

    def download(self, download_url: str, output_path: str | Path) -> Path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream("GET", download_url) as response:
            response.raise_for_status()
            with output.open("wb") as handle:
                for chunk in response.iter_bytes():
                    if chunk:
                        handle.write(chunk)
        return output

    def resolve_share_url(self, share_link: str) -> str:
        response = self._client.get(share_link)
        response.raise_for_status()
        return str(response.url)

    def extract_video_id(self, url: str) -> str | None:
        for pattern in VIDEO_ID_PATTERNS:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def fetch_share_page(self, video_id: str | None, resolved_url: str) -> str:
        url = f"https://www.iesdouyin.com/share/video/{video_id}/" if video_id else resolved_url
        response = self._client.get(
            url,
            headers={
                "Referer": "https://www.iesdouyin.com/",
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                    "Mobile/15E148 Safari/604.1"
                ),
            },
        )
        response.raise_for_status()
        return response.text

    def _extract_json_block(self, html: str) -> Any | None:
        patterns = [
            r'<script id="RENDER_DATA" type="application/json">(.*?)</script>',
            r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>",
            r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;",
            r"window\.__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*(\{.*?\})\s*;",
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if not match:
                continue
            raw = match.group(1).strip()
            for candidate in (raw, unquote(raw)):
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
        return None

    def _walk_for_video_info(self, node: Any) -> dict[str, Any] | None:
        if isinstance(node, dict):
            if "video" in node and any(key in node for key in ("desc", "aweme_id", "statistics")):
                return node
            for value in node.values():
                result = self._walk_for_video_info(value)
                if result:
                    return result
        if isinstance(node, list):
            for item in node:
                result = self._walk_for_video_info(item)
                if result:
                    return result
        return None

    def _extract_url_from_video_dict(self, video: dict[str, Any]) -> str | None:
        for parent_key in ("play_addr", "play_api", "download_addr"):
            parent = video.get(parent_key)
            if isinstance(parent, dict):
                url_list = parent.get("url_list")
                if isinstance(url_list, list) and url_list:
                    return url_list[0]
        for bitrate_key in ("bit_rate", "bitRateList"):
            bitrate_list = video.get(bitrate_key)
            if not isinstance(bitrate_list, list):
                continue
            for bitrate_item in bitrate_list:
                if not isinstance(bitrate_item, dict):
                    continue
                play_addr = bitrate_item.get("play_addr") or bitrate_item.get("playAddr")
                if isinstance(play_addr, dict):
                    url_list = play_addr.get("url_list")
                    if isinstance(url_list, list) and url_list:
                        return url_list[0]
                src = bitrate_item.get("src")
                if isinstance(src, list) and src:
                    return src[0]
        play_addr = video.get("play_addr")
        if isinstance(play_addr, dict) and play_addr.get("uri"):
            uri = play_addr["uri"]
            return f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0"
        return None
