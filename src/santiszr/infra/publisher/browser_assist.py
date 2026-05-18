from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import threading

import httpx

from santiszr.config.settings import load_settings
from santiszr.core.paths import resolve_runtime_paths
from santiszr.domain.schemas.common import ErrorInfo
from santiszr.domain.schemas.publish import PublishPlatform, PublishRequest, PublishResult


PLATFORM_UPLOAD_URLS = {
    PublishPlatform.douyin: "https://creator.douyin.com/creator-micro/content/upload",
    PublishPlatform.xiaohongshu: "https://creator.xiaohongshu.com/publish/publish",
    PublishPlatform.wechat_channels: "https://channels.weixin.qq.com/platform/post/create",
}

PLATFORM_LABELS = {
    PublishPlatform.douyin: "抖音",
    PublishPlatform.xiaohongshu: "小红书",
    PublishPlatform.wechat_channels: "视频号",
}


@dataclass(slots=True)
class BrowserAssistOutcome:
    filled_video: bool = False
    filled_cover: bool = False
    filled_title: bool = False
    filled_description: bool = False

    @property
    def touched_page(self) -> bool:
        return self.filled_video or self.filled_cover or self.filled_title or self.filled_description


class BrowserPublishAssistant:
    """Open platform publishing pages on the local backend machine and fill what is safe."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._playwright = None
        self._context = None

    def publish(
        self,
        request: PublishRequest,
        *,
        video_path: Path,
        cover_path: Path | None,
    ) -> PublishResult:
        label = PLATFORM_LABELS.get(request.platform, request.platform.value)
        url = PLATFORM_UPLOAD_URLS.get(request.platform)
        if not url:
            return PublishResult(
                success=False,
                platform=request.platform,
                status="failed",
                error=ErrorInfo(
                    code="publish_platform_unsupported",
                    message=f"{label} 暂未配置半自动发布入口。",
                ),
            )

        try:
            page = self._new_page(url)
            outcome = self._fill_page(page, request, video_path=video_path, cover_path=cover_path)
        except ModuleNotFoundError:
            return PublishResult(
                success=False,
                platform=request.platform,
                status="failed",
                command=["browser-assist", url],
                error=ErrorInfo(
                    code="publish_browser_missing_dependency",
                    message="缺少 Playwright，无法打开本机半自动发布浏览器。请安装 playwright 并执行 playwright install chromium。",
                ),
            )
        except Exception as exc:
            return PublishResult(
                success=False,
                platform=request.platform,
                status="failed",
                command=["browser-assist", url],
                error=ErrorInfo(code="publish_browser_assist_failed", message=str(exc)),
            )

        notes = [f"已在本机浏览器打开{label}发布页。"]
        if outcome.filled_video:
            notes.append("已尝试上传视频。")
        else:
            notes.append("未找到可用的视频上传控件；如果页面停在登录页，请登录后再点一次打开发布页。")
        if cover_path:
            notes.append("已尝试填充封面。" if outcome.filled_cover else "封面可能需要在平台页面中手动选择。")
        if outcome.filled_title or outcome.filled_description:
            notes.append("已尝试填充标题、描述和标签。")
        else:
            notes.append("标题、描述和标签已准备好；如果页面结构变化，请手动粘贴。")
        notes.append("请在平台页面检查内容，确认无误后手动点击发布。")

        return PublishResult(
            success=True,
            platform=request.platform,
            status="browser_opened",
            command=["browser-assist", url],
            notes=notes,
        )

    def _new_page(self, url: str):
        with self._lock:
            context = self._ensure_context()
            try:
                page = context.new_page()
            except Exception as exc:
                if "closed" not in str(exc).lower():
                    raise
                self._reset_context()
                context = self._ensure_context()
                page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.bring_to_front()
            return page

    def _ensure_context(self):
        if self._context is not None:
            return self._context

        from playwright.sync_api import sync_playwright

        settings = load_settings()
        user_data_dir = resolve_runtime_paths(settings).cache / "publish-browser"
        user_data_dir.mkdir(parents=True, exist_ok=True)

        self._playwright = sync_playwright().start()
        chromium = self._playwright.chromium
        cdp_context = self._connect_existing_chrome(chromium)
        if cdp_context is not None:
            self._context = cdp_context
            return self._context

        launch_kwargs = {
            "headless": False,
            "viewport": {"width": 1440, "height": 1000},
            "accept_downloads": True,
        }
        try:
            self._context = chromium.launch_persistent_context(
                str(user_data_dir),
                channel="chrome",
                **launch_kwargs,
            )
        except Exception:
            self._context = chromium.launch_persistent_context(str(user_data_dir), **launch_kwargs)
        return self._context

    def _connect_existing_chrome(self, chromium):
        try:
            httpx.get("http://127.0.0.1:9222/json/version", timeout=0.35)
        except Exception:
            return None
        try:
            browser = chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=2500)
        except Exception:
            return None
        if browser.contexts:
            return browser.contexts[0]
        return browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)

    def _reset_context(self) -> None:
        self._context = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None

    def _fill_page(
        self,
        page,
        request: PublishRequest,
        *,
        video_path: Path,
        cover_path: Path | None,
    ) -> BrowserAssistOutcome:
        outcome = BrowserAssistOutcome()
        page.wait_for_timeout(1500)

        outcome.filled_video = self._set_file(page, video_path, preferred_accept="video")
        if outcome.filled_video:
            page.wait_for_timeout(2500)

        if cover_path:
            outcome.filled_cover = self._set_file(page, cover_path, preferred_accept="image")

        title = request.title.strip()
        tags_line = self._format_tags_line(request.tags)
        description = self._publish_body(request.description or "", tags_line)

        if title:
            outcome.filled_title = self._fill_text(
                page,
                [
                    r"标题",
                    r"作品标题",
                    r"请输入标题",
                    r"填写标题",
                    r"添加标题",
                ],
                title,
            )

        if description:
            outcome.filled_description = self._fill_text(
                page,
                [
                    r"描述",
                    r"简介",
                    r"正文",
                    r"文案",
                    r"说点什么",
                    r"分享你的",
                    r"添加作品简介",
                ],
                description,
            )

        if not outcome.filled_description and tags_line:
            outcome.filled_description = self._fill_first_empty_editor(page, tags_line)

        return outcome

    def _set_file(self, page, file_path: Path, *, preferred_accept: str) -> bool:
        if self._set_existing_file_input(page, file_path, preferred_accept=preferred_accept):
            return True
        if self._set_file_with_chooser(page, file_path, preferred_accept=preferred_accept):
            return True
        page.wait_for_timeout(1200)
        return self._set_existing_file_input(page, file_path, preferred_accept=preferred_accept)

    def _set_existing_file_input(self, page, file_path: Path, *, preferred_accept: str) -> bool:
        inputs = page.locator('input[type="file"]')
        try:
            count = inputs.count()
        except Exception:
            return False

        fallback_index: int | None = None
        for index in range(count):
            item = inputs.nth(index)
            try:
                accept = (item.get_attribute("accept", timeout=800) or "").lower()
                if self._accept_matches(accept, preferred_accept):
                    item.set_input_files(str(file_path), timeout=5000)
                    return True
                if fallback_index is None and not accept:
                    fallback_index = index
            except Exception:
                continue

        if fallback_index is not None:
            try:
                inputs.nth(fallback_index).set_input_files(str(file_path), timeout=5000)
                return True
            except Exception:
                return False
        return False

    def _set_file_with_chooser(self, page, file_path: Path, *, preferred_accept: str) -> bool:
        labels = (
            ["上传视频", "点击上传", "拖拽视频", "选择视频", "上传"]
            if preferred_accept == "video"
            else ["上传封面", "更换封面", "选择封面", "上传图片", "上传"]
        )
        for label in labels:
            locators = [
                page.get_by_text(re.compile(label, re.I)).first,
                page.get_by_role("button", name=re.compile(label, re.I)).first,
            ]
            for locator in locators:
                try:
                    if locator.count() <= 0:
                        continue
                    with page.expect_file_chooser(timeout=5000) as chooser_info:
                        locator.click(timeout=2500)
                    chooser_info.value.set_files(str(file_path))
                    return True
                except Exception:
                    continue
        return False

    def _accept_matches(self, accept: str, preferred_accept: str) -> bool:
        if preferred_accept in accept:
            return True
        if preferred_accept == "video":
            return any(token in accept for token in [".mp4", ".mov", ".m4v", ".avi", "video/"])
        return any(token in accept for token in [".png", ".jpg", ".jpeg", ".webp", "image/"])

    def _fill_text(self, page, patterns: list[str], text: str) -> bool:
        for pattern in patterns:
            regex = re.compile(pattern, re.I)
            for getter in (page.get_by_placeholder, page.get_by_label):
                try:
                    locator = getter(regex).first
                    if locator.count() > 0:
                        locator.fill(text, timeout=2500)
                        return True
                except Exception:
                    continue

        return self._fill_contenteditable_by_hint(page, patterns, text)

    def _fill_contenteditable_by_hint(self, page, patterns: list[str], text: str) -> bool:
        hints = [pattern.strip("\\") for pattern in patterns]
        try:
            return bool(
                page.evaluate(
                    """
                    ({ hints, text }) => {
                      const editors = Array.from(document.querySelectorAll('[contenteditable="true"], textarea, input'));
                      const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 20 && rect.height > 12;
                      };
                      for (const el of editors) {
                        if (!visible(el)) continue;
                        const meta = [
                          el.getAttribute('placeholder'),
                          el.getAttribute('aria-label'),
                          el.getAttribute('data-placeholder'),
                          el.getAttribute('name'),
                          el.closest('label')?.innerText,
                          el.parentElement?.innerText,
                        ].filter(Boolean).join(' ');
                        if (!hints.some((hint) => meta.includes(hint))) continue;
                        el.focus();
                        if ('value' in el) {
                          el.value = text;
                          el.dispatchEvent(new Event('input', { bubbles: true }));
                          el.dispatchEvent(new Event('change', { bubbles: true }));
                        } else {
                          document.execCommand('selectAll', false, null);
                          document.execCommand('insertText', false, text);
                          el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
                        }
                        return true;
                      }
                      return false;
                    }
                    """,
                    {"hints": hints, "text": text},
                )
            )
        except Exception:
            return False

    def _fill_first_empty_editor(self, page, text: str) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    (text) => {
                      const editors = Array.from(document.querySelectorAll('[contenteditable="true"], textarea'));
                      for (const el of editors) {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        if (style.visibility === 'hidden' || style.display === 'none' || rect.width < 20 || rect.height < 12) continue;
                        const current = 'value' in el ? el.value : el.innerText;
                        if (current && current.trim()) continue;
                        el.focus();
                        if ('value' in el) {
                          el.value = text;
                          el.dispatchEvent(new Event('input', { bubbles: true }));
                        } else {
                          document.execCommand('insertText', false, text);
                          el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: text }));
                        }
                        return true;
                      }
                      return false;
                    }
                    """,
                    text,
                )
            )
        except Exception:
            return False

    def _format_tags_line(self, tags: list[str]) -> str:
        normalized: list[str] = []
        for tag in tags:
            value = tag.strip().strip("#").strip()
            if not value or value in normalized:
                continue
            normalized.append(value)
        return " ".join(f"#{tag}" for tag in normalized)

    def _publish_body(self, description: str, tags_line: str) -> str:
        parts = [description.strip(), tags_line.strip()]
        return "\n".join(part for part in parts if part)


browser_publish_assistant = BrowserPublishAssistant()
