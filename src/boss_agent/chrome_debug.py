from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import urlopen
from websocket import create_connection


class ChromeDebugClient:
    def __init__(self, endpoint: str, mock_dir: Path | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.mock_dir = mock_dir
        self._message_id = 0
        self._mock_current_candidate_id = ""

    def version(self) -> dict[str, Any]:
        return self._get_json("/json/version", "version.json")

    def list_pages(self) -> list[dict[str, Any]]:
        payload = self._get_json("/json/list", "list.json")
        if not isinstance(payload, list):
            raise TypeError("Expected list payload from /json/list")
        return payload

    def capture_visible_text(self, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return json.loads((self.mock_dir / "evaluate_chat_text.json").read_text(encoding="utf-8"))

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = """
(() => {
  const text = document.body ? document.body.innerText : "";
  return JSON.stringify({
    title: document.title,
    text,
    url: location.href
  });
})()
""".strip()

        ws = create_connection(websocket_url, timeout=10)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
        finally:
            ws.close()

        runtime_result = response.get("result", {}).get("result", {})
        if "exceptionDetails" in response.get("result", {}) or "value" not in runtime_result:
            raise RuntimeError(f"Chrome Runtime.evaluate failed: {response}")
        result = runtime_result["value"]
        payload = json.loads(result)
        return {
            "title": payload.get("title", page.get("title", "")),
            "text": payload.get("text", ""),
            "url": payload.get("url", page.get("url", "")),
        }

    def capture_conversation(self, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            fixture_name = "hr_conversation_structured.json" if url_contains and "/web/chat/index" in url_contains else "conversation_structured.json"
            return json.loads((self.mock_dir / fixture_name).read_text(encoding="utf-8"))

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = build_capture_conversation_expression()

        ws = create_connection(websocket_url, timeout=10)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
        finally:
            ws.close()

        runtime_result = response.get("result", {}).get("result", {})
        if "exceptionDetails" in response.get("result", {}) or "value" not in runtime_result:
            raise RuntimeError(f"Chrome Runtime.evaluate failed: {response}")
        result = runtime_result["value"]
        if isinstance(result, dict):
            return result
        return json.loads(result)

    def fill_chat_input(self, body: str, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "filled": True,
                "selector": ".chat-input",
                "body": body,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        escaped_body = json.dumps(body, ensure_ascii=False)
        expression = f"""
(() => {{
  const selectors = ['.chat-input', '.boss-chat-editor-input', '[contenteditable=\"true\"]'];
  const el = selectors.map((selector) => document.querySelector(selector)).find(Boolean);
  if (!el) {{
    return JSON.stringify({{"filled": false, "selector": selectors.join(', '), "reason": "not_found"}});
  }}
  el.focus();
  el.innerHTML = '';
  el.textContent = {escaped_body};
  el.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: {escaped_body} }}));
  return JSON.stringify({{"filled": true, "selector": selectors.find((selector) => document.querySelector(selector) === el) || 'dynamic', "body": {escaped_body}}});
}})()
""".strip()

        ws = create_connection(websocket_url, timeout=5)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
        finally:
            ws.close()

        runtime_result = response.get("result", {}).get("result", {})
        if "value" not in runtime_result:
            return {
                "closed": False,
                "selector": "resume_panel",
                "reason": "runtime_evaluate_no_value",
                "response": response,
            }
        result = runtime_result["value"]
        if isinstance(result, dict):
            return result
        return json.loads(result)

    def click_resume_consent(self, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "clicked": True,
                "selector": ".op a.btn",
                "text": "鍚屾剰",
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = """
(() => {
  const candidates = Array.from(document.querySelectorAll('.op a.btn, a.btn, button'))
    .filter((el) => (el.innerText || '').trim() === '鍚屾剰');
  const target = candidates[0];
  if (!target) {
    return JSON.stringify({clicked: false, selector: '.op a.btn', text: '鍚屾剰', reason: 'not_found'});
  }
  target.click();
  return JSON.stringify({clicked: true, selector: '.op a.btn', text: '鍚屾剰'});
})()
""".strip()

        ws = create_connection(websocket_url, timeout=5)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
        finally:
            ws.close()

        result = response["result"]["result"]["value"]
        payload = json.loads(result)
        if not payload.get("closed"):
            try:
                self._dispatch_escape_key(websocket_url)
                time.sleep(0.4)
                verify = self._evaluate_json(
                    websocket_url,
                    """
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
  };
  const stillOpen = Array.from(document.querySelectorAll('.resume-common-dialog, .search-resume, .boss-popup__wrapper, .dialog-wrap.active'))
    .some(isVisible);
  return JSON.stringify({ stillOpen });
})()
""".strip(),
                    timeout=5,
                )
                if not verify.get("stillOpen"):
                    return {**payload, "closed": True, "escapeFallback": True}
                return {**payload, "escapeFallback": True, "verify": verify}
            except Exception as exc:
                return {**payload, "escapeFallback": False, "escapeError": str(exc)}
        return payload

    def detect_resume_consent_prompt(self, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            fixture = self.mock_dir / "resume_consent_prompt.json"
            if fixture.exists():
                return json.loads(fixture.read_text(encoding="utf-8"))
            return {
                "visible": False,
                "text": "",
                "hasAgreeButton": False,
                "hasRejectButton": False,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = """
(() => {
  const text = (el) => el ? (el.innerText || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && style.visibility !== 'hidden'
      && style.display !== 'none';
  };
  const agreeButton = Array.from(document.querySelectorAll('.op a.btn, a.btn, button'))
    .find((el) => text(el) === '鍚屾剰' && isVisible(el));
  const rejectButton = Array.from(document.querySelectorAll('.op a.btn, a.btn, button'))
    .find((el) => text(el) === '鎷掔粷' && isVisible(el));
  const container = agreeButton?.closest('.item-resume, .message-item, .op') || rejectButton?.closest('.item-resume, .message-item, .op');
  const containerText = text(container);
  const promptVisible = Boolean(
    (agreeButton && rejectButton)
    || containerText.includes('瀵规柟鎯冲彂閫侀檮浠剁畝鍘嗙粰鎮紝鎮ㄦ槸鍚﹀悓鎰?)
  );
  return JSON.stringify({
    visible: promptVisible,
    text: containerText,
    hasAgreeButton: Boolean(agreeButton),
    hasRejectButton: Boolean(rejectButton)
  });
})()
""".strip()

        ws = create_connection(websocket_url, timeout=5)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True},
            )
        finally:
            ws.close()

        runtime_result = response.get("result", {}).get("result", {})
        if "value" not in runtime_result:
            return {
                "closed": False,
                "selector": "resume_panel",
                "reason": "runtime_evaluate_no_value",
                "response": response,
            }
        result = runtime_result["value"]
        if isinstance(result, dict):
            return result
        return json.loads(result)

    def capture_attachment_preview_link(self, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return json.loads((self.mock_dir / "attachment_link.json").read_text(encoding="utf-8"))

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        ws = create_connection(websocket_url, timeout=5)
        try:
            self._send_cdp_command(ws, "Network.enable", {})
            response = self._send_cdp_command(
                ws,
                "Runtime.evaluate",
                {"expression": build_attachment_preview_probe_expression(), "returnByValue": True},
            )
            probe = json.loads(response["result"]["result"]["value"])
            if not probe.get("found"):
                return {
                    "type": "attachment_preview",
                    "previewUrl": "",
                    "viewerUrl": "",
                    "checkUrl": "",
                }
            end = time.time() + 5
            found: dict[str, Any] = {
                "type": "attachment_preview",
                "previewUrl": "",
                "viewerUrl": "",
                "checkUrl": "",
            }
            if probe.get("alreadyOpen"):
                found["previewUrl"] = "already_open"
            ws.settimeout(0.5)
            while time.time() < end:
                try:
                    raw = ws.recv()
                except Exception:
                    continue
                message = json.loads(raw)
                if message.get("method") != "Network.requestWillBeSent":
                    continue
                request = message["params"]["request"]
                url = request.get("url", "")
                if "preview/check.json" in url:
                    found["checkUrl"] = url
                elif "pdf-viewer-b" in url:
                    found["viewerUrl"] = url
                elif "preview4boss" in url:
                    found["previewUrl"] = url
                if found["previewUrl"] and found["viewerUrl"] and found["checkUrl"]:
                    break
            return found
        finally:
            ws.close()

    def capture_inbox(self, url_contains: str | None = None, filter_label: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            fixture_name = "inbox_all_today.json" if filter_label == "鍏ㄩ儴" else "inbox_unread.json"
            return json.loads((self.mock_dir / fixture_name).read_text(encoding="utf-8"))

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = """
(() => {
  const text = (node) => node ? (node.innerText || '').trim() : '';
  const filterTabs = Array.from(document.querySelectorAll('.chat-message-filter-left span')).map((el) => ({
    label: text(el),
    active: el.classList.contains('active')
  }));
  const activeFilter = filterTabs.find((item) => item.active)?.label || '';
  const items = Array.from(document.querySelectorAll('.user-list [role="listitem"] .geek-item')).map((el) => ({
    dataId: el.getAttribute('data-id') || '',
    unreadCount: text(el.querySelector('.badge-count')),
    time: text(el.querySelector('.title .time')),
    candidateName: text(el.querySelector('.geek-name')),
    jobTitle: text(el.querySelector('.source-job')),
    summary: text(el.querySelector('.desc')) || text(el.querySelector('.text.uid')).split('\\n').slice(-1)[0] || '',
    selected: el.classList.contains('selected') || !!el.closest('.geek-item-wrap.selected')
  })).filter((item) => item.dataId);

  return JSON.stringify({
    title: document.title,
    url: location.href,
    activeFilter,
    filterTabs,
    count: items.length,
    conversations: items
  });
})()
""".strip()

        ws = create_connection(websocket_url, timeout=5)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True},
            )
        finally:
            ws.close()

        result = response["result"]["result"]["value"]
        payload = json.loads(result)
        if filter_label:
            payload["activeFilter"] = filter_label
        return payload

    def capture_unread_inbox(self, url_contains: str | None = None) -> dict[str, Any]:
        return self.capture_inbox(url_contains=url_contains, filter_label="鏈")

    def capture_inbox_scrolled(
        self,
        url_contains: str | None = None,
        filter_label: str | None = None,
        max_items: int = 200,
        max_scrolls: int = 20,
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            payload = self.capture_inbox(url_contains=url_contains, filter_label=filter_label)
            payload["scrollCapture"] = {
                "enabled": True,
                "maxItems": max_items,
                "maxScrolls": max_scrolls,
                "scrolls": 0,
                "exhausted": True,
            }
            payload["count"] = len(payload.get("conversations", []))
            return payload

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = f"""
(async () => {{
  const maxItems = {int(max_items)};
  const maxScrolls = {int(max_scrolls)};
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const text = (node) => node ? (node.innerText || '').trim() : '';
  const collect = () => Array.from(document.querySelectorAll('.user-list [role="listitem"] .geek-item')).map((el) => ({{
    dataId: el.getAttribute('data-id') || '',
    unreadCount: text(el.querySelector('.badge-count')),
    time: text(el.querySelector('.title .time')),
    candidateName: text(el.querySelector('.geek-name')),
    jobTitle: text(el.querySelector('.source-job')),
    summary: text(el.querySelector('.desc')) || text(el.querySelector('.text.uid')).split('\\n').slice(-1)[0] || '',
    selected: el.classList.contains('selected') || !!el.closest('.geek-item-wrap.selected')
  }})).filter((item) => item.dataId);
  const findScroller = () => {{
    const firstItem = document.querySelector('.user-list [role="listitem"] .geek-item');
    const candidates = Array.from(document.querySelectorAll('.user-list, .user-list *, .chat-conversation, .chat-conversation *'))
      .filter((el) => el.scrollHeight > el.clientHeight + 8 && (!firstItem || el.contains(firstItem)));
    candidates.sort((a, b) => (b.clientHeight * b.clientWidth) - (a.clientHeight * a.clientWidth));
    return candidates[0] || document.querySelector('.user-list') || null;
  }};
  const filterTabs = Array.from(document.querySelectorAll('.chat-message-filter-left span')).map((el) => ({{
    label: text(el),
    active: el.classList.contains('active')
  }}));
  const activeFilter = filterTabs.find((item) => item.active)?.label || '';
  const scroller = findScroller();
  const byId = new Map();
  let scrolls = 0;
  let exhausted = true;
  if (scroller) {{
    scroller.scrollTop = 0;
    await wait(350);
  }}
  for (;;) {{
    for (const item of collect()) {{
      if (!byId.has(item.dataId)) byId.set(item.dataId, item);
    }}
    if (!scroller || byId.size >= maxItems || scrolls >= maxScrolls) {{
      exhausted = !scroller || (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 8);
      break;
    }}
    const beforeTop = scroller.scrollTop;
    scroller.scrollTop = Math.min(scroller.scrollHeight, scroller.scrollTop + Math.max(120, Math.floor(scroller.clientHeight * 0.85)));
    scrolls += 1;
    await wait(450);
    const atBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 8;
    if (atBottom || scroller.scrollTop === beforeTop) {{
      for (const item of collect()) {{
        if (!byId.has(item.dataId)) byId.set(item.dataId, item);
      }}
      exhausted = true;
      break;
    }}
  }}
  const conversations = Array.from(byId.values()).slice(0, maxItems);
  return JSON.stringify({{
    title: document.title,
    url: location.href,
    activeFilter,
    filterTabs,
    count: conversations.length,
    conversations,
    scrollCapture: {{
      enabled: true,
      maxItems,
      maxScrolls,
      scrolls,
      exhausted,
      scrollTop: scroller ? scroller.scrollTop : 0,
      scrollHeight: scroller ? scroller.scrollHeight : 0,
      clientHeight: scroller ? scroller.clientHeight : 0
    }}
  }});
}})()
""".strip()

        ws = create_connection(websocket_url, timeout=10)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
        finally:
            ws.close()

        payload = json.loads(response["result"]["result"]["value"])
        if filter_label:
            payload["activeFilter"] = filter_label
        return payload

    def capture_unread_inbox_scrolled(
        self,
        url_contains: str | None = None,
        max_items: int = 200,
        max_scrolls: int = 20,
    ) -> dict[str, Any]:
        return self.capture_inbox_scrolled(
            url_contains=url_contains,
            filter_label="\u672a\u8bfb",
            max_items=max_items,
            max_scrolls=max_scrolls,
        )

    def open_conversation(
        self,
        data_id: str | None = None,
        candidate_name: str | None = None,
        url_contains: str | None = None,
        max_scrolls: int = 20,
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            return json.loads((self.mock_dir / "open_conversation.json").read_text(encoding="utf-8"))

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        escaped_data_id = json.dumps(data_id or "", ensure_ascii=False)
        escaped_name = json.dumps(candidate_name or "", ensure_ascii=False)
        expression = f"""
(async () => {{
  const maxScrolls = {int(max_scrolls)};
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const text = (node) => node ? (node.innerText || '').trim() : '';
  const findTarget = () => {{
    const items = Array.from(document.querySelectorAll('.user-list [role="listitem"] .geek-item'));
    return items.find((el) => {{
      const currentDataId = el.getAttribute('data-id') || '';
      const currentName = text(el.querySelector('.geek-name'));
      if ({escaped_data_id} && currentDataId === {escaped_data_id}) return true;
      if (!{escaped_data_id} && {escaped_name} && currentName === {escaped_name}) return true;
      return false;
    }});
  }};
  const findScroller = () => {{
    const firstItem = document.querySelector('.user-list [role="listitem"] .geek-item');
    const candidates = Array.from(document.querySelectorAll('.user-list, .user-list *, .chat-conversation, .chat-conversation *'))
      .filter((el) => el.scrollHeight > el.clientHeight + 8 && (!firstItem || el.contains(firstItem)));
    candidates.sort((a, b) => (b.clientHeight * b.clientWidth) - (a.clientHeight * a.clientWidth));
    return candidates[0] || document.querySelector('.user-list') || null;
  }};
  const scroller = findScroller();
  let target = findTarget();
  let scrolls = 0;
  while (!target && scroller && scrolls < maxScrolls) {{
    const beforeTop = scroller.scrollTop;
    scroller.scrollTop = Math.min(scroller.scrollHeight, scroller.scrollTop + Math.max(120, Math.floor(scroller.clientHeight * 0.85)));
    scrolls += 1;
    await wait(450);
    target = findTarget();
    if (scroller.scrollTop === beforeTop || scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 8) break;
  }}
  if (!target) {{
    return JSON.stringify({{ opened: false, reason: 'not_found', scrolls }});
  }}
  target.scrollIntoView({{ block: 'center' }});
  target.click();
  return JSON.stringify({{
    opened: true,
    dataId: target.getAttribute('data-id') || '',
    candidateName: text(target.querySelector('.geek-name')),
    jobTitle: text(target.querySelector('.source-job')),
    scrolls
  }});
}})()
""".strip()

        ws = create_connection(websocket_url, timeout=5)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
        finally:
            ws.close()

        result = response["result"]["result"]["value"]
        return json.loads(result)

    def wait_for_conversation_ready(
        self,
        expected_candidate_name: str | None = None,
        expected_job_title: str | None = None,
        url_contains: str | None = None,
        timeout_seconds: float = 6.0,
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            fixture = self.mock_dir / "conversation_ready.json"
            if fixture.exists():
                return json.loads(fixture.read_text(encoding="utf-8"))
            return {
                "ready": True,
                "candidateMatched": True,
                "jobMatched": True,
                "hasMessages": True,
                "conversation": self.capture_conversation(url_contains=url_contains),
            }

        deadline = time.time() + timeout_seconds
        latest: dict[str, Any] = {}
        expected_candidate_name = (expected_candidate_name or "").strip()
        expected_job_title = (expected_job_title or "").strip()

        while time.time() < deadline:
            latest = self.capture_conversation(url_contains=url_contains)
            actual_candidate = (latest.get("candidateName") or "").strip()
            actual_job = (latest.get("jobTitle") or "").strip()
            messages = latest.get("messages", [])
            candidate_matched = (not expected_candidate_name) or actual_candidate == expected_candidate_name
            job_matched = (not expected_job_title) or actual_job == expected_job_title
            has_messages = bool(messages)
            not_placeholder = bool(actual_candidate) and actual_candidate != "\u5728\u7ebf\u7b80\u5386"

            if candidate_matched and job_matched and not_placeholder and (has_messages or bool(actual_job)):
                return {
                    "ready": True,
                    "candidateMatched": candidate_matched,
                    "jobMatched": job_matched,
                    "hasMessages": has_messages,
                    "conversation": latest,
                }
            time.sleep(0.5)

        actual_candidate = (latest.get("candidateName") or "").strip()
        actual_job = (latest.get("jobTitle") or "").strip()
        messages = latest.get("messages", [])
        return {
            "ready": False,
            "candidateMatched": (not expected_candidate_name) or actual_candidate == expected_candidate_name,
            "jobMatched": (not expected_job_title) or actual_job == expected_job_title,
            "hasMessages": bool(messages),
            "conversation": latest,
        }

    def switch_message_filter(self, label: str, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "switched": True,
                "label": label,
                "selector": ".chat-message-filter-left span",
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        escaped_label = json.dumps(label, ensure_ascii=False)
        expression = f"""
(() => {{
  const target = Array.from(document.querySelectorAll('.chat-message-filter-left span'))
    .find((el) => ((el.innerText || '').trim() === {escaped_label}));
  if (!target) {{
    return JSON.stringify({{ switched: false, label: {escaped_label}, selector: '.chat-message-filter-left span', reason: 'not_found' }});
  }}
  target.click();
  return JSON.stringify({{ switched: true, label: {escaped_label}, selector: '.chat-message-filter-left span' }});
}})()
""".strip()

        ws = create_connection(websocket_url, timeout=5)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True},
            )
        finally:
            ws.close()

        return json.loads(response["result"]["result"]["value"])

    def switch_conversation_status_tab(self, label: str, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "switched": True,
                "label": label,
                "matchedText": f"{label}(45)" if label == "\u65b0\u62db\u547c" else label,
                "selector": ".chat-message-status span",
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        escaped_label = json.dumps(label, ensure_ascii=False)
        expression = f"""
(() => {{
  const text = (node) => node ? (node.innerText || '').trim() : '';
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && style.visibility !== 'hidden'
      && style.display !== 'none';
  }};
  const normalize = (value) => String(value || '').replace(/\\s+/g, '').replace(/[（(]\\d+[）)]/g, '');
  const targetLabel = normalize({escaped_label});
  const candidates = Array.from(document.querySelectorAll(
    '.chat-message-status span, .chat-message-status a, .chat-message-status li, .chat-status span, .chat-status a, .chat-status li, .chat-tab span, .chat-tab a, .chat-tab li, span, a, li'
  )).filter(isVisible);
  const target = candidates.find((el) => normalize(text(el)) === targetLabel);
  if (!target) {{
    return JSON.stringify({{
      switched: false,
      label: {escaped_label},
      selector: '.chat-message-status span',
      reason: 'not_found',
      visibleTabs: candidates.map((el) => text(el)).filter(Boolean).slice(0, 40)
    }});
  }}
  target.click();
  return JSON.stringify({{
    switched: true,
    label: {escaped_label},
    matchedText: text(target),
    selector: '.chat-message-status span'
  }});
}})()
""".strip()

        ws = create_connection(websocket_url, timeout=5)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True},
            )
        finally:
            ws.close()

        return json.loads(response["result"]["result"]["value"])

    def click_request_resume_button(self, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            fixture = self.mock_dir / "request_resume_button.json"
            if fixture.exists():
                return json.loads(fixture.read_text(encoding="utf-8"))
            return {
                "clicked": True,
                "selector": ".conversation-editor",
                "text": "\u6c42\u7b80\u5386",
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = """
(() => {
  const text = (node) => node ? (node.innerText || node.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && style.visibility !== 'hidden'
      && style.display !== 'none';
  };
  const root = document.querySelector('.conversation-operate')
    || document.querySelector('.chat-conversation, .conversation, .chat-main, .chat-container')
    || document;
  const candidates = Array.from(root.querySelectorAll('button, a, span, div, [role="button"]'))
    .filter((el) => isVisible(el) && text(el) === '求简历');
  const target = candidates[0];
  if (!target) {
    return JSON.stringify({
      clicked: false,
      selector: '.conversation-editor',
      text: '求简历',
      reason: 'not_found',
      visibleButtons: Array.from(root.querySelectorAll('button, a, [role="button"], span'))
        .filter(isVisible)
        .map((el) => text(el))
        .filter(Boolean)
        .slice(0, 40)
    });
  }
  const rect = target.getBoundingClientRect();
  const payload = {
    clicked: true,
    selector: '.conversation-editor',
    text: text(target),
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2
  };
  target.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, clientX: payload.x, clientY: payload.y }));
  target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: payload.x, clientY: payload.y, button: 0 }));
  target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: payload.x, clientY: payload.y, button: 0 }));
  target.click();
  return JSON.stringify(payload);
})()
""".strip()

        ws = create_connection(websocket_url, timeout=5)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True},
            )
        finally:
            ws.close()

        payload = json.loads(response["result"]["result"]["value"])
        if payload.get("clicked") and "x" in payload and "y" in payload:
            self._dispatch_mouse_click(websocket_url, payload["x"], payload["y"])
            confirm = self._click_request_resume_confirm(websocket_url)
            payload["confirm"] = confirm
        return payload

    def _click_request_resume_confirm(self, websocket_url: str) -> dict[str, Any]:
        expression = """
(() => {
  const text = (node) => node ? (node.innerText || node.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && style.visibility !== 'hidden'
      && style.display !== 'none';
  };
  const tooltip = Array.from(document.querySelectorAll('.exchange-tooltip, .boss-popover, .popover, div'))
    .find((el) => text(el).includes('确定向牛人索取简历吗'));
  if (!tooltip) {
    return JSON.stringify({ clicked: false, reason: 'confirm_not_visible' });
  }
  const target = Array.from(tooltip.querySelectorAll('button, a, span, div, [role="button"]'))
    .filter((el) => isVisible(el) && text(el) === '确定')
    .pop();
  if (!target) {
    const rect = tooltip.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      return JSON.stringify({
        clicked: true,
        text: '确定',
        fallback: 'tooltip_rect',
        x: rect.left + rect.width * 0.82,
        y: rect.top + rect.height * 0.72
      });
    }
    return JSON.stringify({ clicked: false, reason: 'confirm_button_not_found', text: text(tooltip) });
  }
  const rect = target.getBoundingClientRect();
  return JSON.stringify({
    clicked: true,
    text: text(target),
    x: rect.left + rect.width / 2,
    y: rect.top + rect.height / 2
  });
})()
""".strip()
        deadline = time.time() + 3.0
        latest: dict[str, Any] = {"clicked": False, "reason": "confirm_not_checked"}
        while time.time() < deadline:
            latest = self._evaluate_json(websocket_url, expression, timeout=5)
            if latest.get("clicked") and "x" in latest and "y" in latest:
                self._dispatch_mouse_click(websocket_url, latest["x"], latest["y"])
                return latest
            time.sleep(0.25)
        return latest

    def capture_job_filter_options(self, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "selected": "鍏ㄩ儴鑱屼綅",
                "opened": False,
                "options": ["鍏ㄩ儴鑱屼綅", "鏂囧憳 _ 鐝犳捣 5-6K"],
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        state = self._chat_job_filter_state(websocket_url)
        if not state.get("opened") and state.get("trigger"):
            trigger = state["trigger"]
            self._dispatch_mouse_click(websocket_url, trigger["x"], trigger["y"])
            time.sleep(0.3)
            state = self._chat_job_filter_state(websocket_url)

        return {
            "selected": state.get("selected", ""),
            "opened": state.get("opened", False),
            "options": [option.get("text", "") for option in state.get("options", []) if option.get("text")],
        }

    def switch_job_filter(self, job_text: str, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "switched": True,
                "jobText": job_text,
                "selected": job_text,
                "options": ["鍏ㄩ儴鑱屼綅", "鏂囧憳 _ 鐝犳捣 5-6K"],
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        target = " ".join(job_text.split())
        state = self._chat_job_filter_state(websocket_url)
        if state.get("selected") == target:
            return {
                "switched": True,
                "jobText": job_text,
                "selected": state.get("selected", ""),
                "alreadySelected": True,
                "options": [option.get("text", "") for option in state.get("options", []) if option.get("text")],
            }

        if not state.get("opened"):
            trigger = state.get("trigger")
            if not trigger:
                return {
                    "switched": False,
                    "jobText": job_text,
                    "selected": state.get("selected", ""),
                    "reason": "trigger_not_found",
                    "options": [option.get("text", "") for option in state.get("options", []) if option.get("text")],
                }
            self._dispatch_mouse_click(websocket_url, trigger["x"], trigger["y"])
            time.sleep(0.5)
            state = self._chat_job_filter_state(websocket_url)

        options = state.get("options", [])
        option = next((item for item in options if item.get("text") == target), None)
        if not option:
            return {
                "switched": False,
                "jobText": job_text,
                "selected": state.get("selected", ""),
                "opened": state.get("opened", False),
                "reason": "option_not_found",
                "options": [item.get("text", "") for item in options if item.get("text")],
            }

        self._dispatch_mouse_click(websocket_url, option["x"], option["y"])
        time.sleep(0.8)
        verified = self._chat_job_filter_state(websocket_url)
        return {
            "switched": verified.get("selected") == target,
            "jobText": job_text,
            "selected": verified.get("selected", ""),
            "opened": verified.get("opened", False),
            "options": [item.get("text", "") for item in verified.get("options", []) if item.get("text")]
            or [item.get("text", "") for item in options if item.get("text")],
        }

    def capture_recommend_job_filter_options(self, url_contains: str | None = "/web/chat/recommend") -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "selected": "\u7535\u5546\u8fd0\u8425\u7ecf\u7406\uff08\u7f8e\u5986\uff09",
                "opened": False,
                "options": ["\u7535\u5546\u8fd0\u8425\u7ecf\u7406\uff08\u7f8e\u5986\uff09", "\u9ad8\u7ea7\u5929\u732b\u8fd0\u8425"],
                "mock": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        state = self._recommend_job_filter_state(websocket_url)
        if not state.get("opened") and state.get("trigger"):
            trigger = state["trigger"]
            self._dispatch_mouse_click(websocket_url, trigger["x"], trigger["y"])
            time.sleep(0.4)
            state = self._recommend_job_filter_state(websocket_url)

        return {
            "selected": state.get("selected", ""),
            "opened": state.get("opened", False),
            "options": [option.get("text", "") for option in state.get("options", []) if option.get("text")],
        }

    def switch_recommend_job_filter(
        self,
        job_text: str,
        url_contains: str | None = "/web/chat/recommend",
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "switched": True,
                "jobText": job_text,
                "selected": job_text,
                "options": [job_text, "缁惧じ绗傞崚鍡涙敘閿涘牏绶ㄦ俊鍡礆"],
                "mock": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        target = " ".join(job_text.split())
        state = self._recommend_job_filter_state(websocket_url)
        if state.get("selected") == target:
            return {
                "switched": True,
                "jobText": job_text,
                "selected": state.get("selected", ""),
                "alreadySelected": True,
                "options": [option.get("text", "") for option in state.get("options", []) if option.get("text")],
            }

        if not state.get("opened"):
            trigger = state.get("trigger")
            if not trigger:
                return {
                    "switched": False,
                    "jobText": job_text,
                    "selected": state.get("selected", ""),
                    "reason": "trigger_not_found",
                    "options": [option.get("text", "") for option in state.get("options", []) if option.get("text")],
                }
            self._dispatch_mouse_click(websocket_url, trigger["x"], trigger["y"])
            time.sleep(0.5)
            state = self._recommend_job_filter_state(websocket_url)

        options = state.get("options", [])
        option = next(
            (
                item for item in options
                if item.get("text") == target or target in str(item.get("text") or "")
            ),
            None,
        )
        if not option:
            return {
                "switched": False,
                "jobText": job_text,
                "selected": state.get("selected", ""),
                "opened": state.get("opened", False),
                "reason": "option_not_found",
                "options": [item.get("text", "") for item in options if item.get("text")],
            }

        self._dispatch_mouse_click(websocket_url, option["x"], option["y"])
        time.sleep(1.0)
        verified = self._recommend_job_filter_state(websocket_url)
        return {
            "switched": verified.get("selected") == target or target in str(verified.get("selected") or ""),
            "jobText": job_text,
            "selected": verified.get("selected", ""),
            "opened": verified.get("opened", False),
            "options": [item.get("text", "") for item in verified.get("options", []) if item.get("text")]
            or [item.get("text", "") for item in options if item.get("text")],
        }

    def close_attachment_preview(self, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "closed": True,
                "selector": ".boss-popup__close",
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = """
(async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && style.visibility !== 'hidden'
      && style.display !== 'none';
  };
  const hasResumePanel = () => {
    const selectors = [
      'iframe[src*="/web/frame/c-resume"]',
      '.resume-common-dialog',
      '.search-resume',
      '.dialog-resume-full',
      '.dialog-wrap.active .resume-layout-wrap',
      '.dialog-wrap.active .resume-detail-wrap',
      '.boss-popup__wrapper canvas',
      '.boss-dialog__body canvas'
    ];
    if (selectors.some((selector) => Array.from(document.querySelectorAll(selector)).some(isVisible))) {
      return true;
    }
    const popupText = Array.from(document.querySelectorAll('.dialog-wrap.active, .boss-popup__wrapper, .boss-dialog__body'))
      .filter(isVisible)
      .map((el) => (el.innerText || el.textContent || '').trim())
      .join('\\n');
    return popupText.includes('在线简历')
      || popupText.includes('工作经历')
      || popupText.includes('教育经历')
      || popupText.includes('期望职位')
      || popupText.includes('个人优势');
  };
  if (!hasResumePanel()) {
    return JSON.stringify({ closed: false, selector: 'resume_panel', reason: 'resume_panel_not_open' });
  }
  const target = Array.from(document.querySelectorAll(
    '.resume-common-dialog .close-btn, .search-resume .close-btn, .new-chat-resume-dialog-main-ui .close-btn, .resume-container .close-btn, .resume-common-dialog .boss-popup__close, .resume-common-dialog .resume-custom-close, .search-resume .boss-popup__close, .search-resume .resume-custom-close, .dialog-resume-full .close, .dialog-resume-full .boss-popup__close, .dialog-wrap.active .close, .dialog-wrap.active .boss-popup__close, .boss-dialog__body .boss-popup__close, .boss-popup__wrapper .boss-popup__close'
  )).find((el) => isVisible(el));
  if (!target) {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
    await wait(500);
    return JSON.stringify({
      closed: !hasResumePanel(),
      selector: 'Escape',
      reason: hasResumePanel() ? 'close_not_found' : '',
      escapeFallback: true
    });
  }
  target.click();
  await wait(500);
  if (!hasResumePanel()) {
    return JSON.stringify({ closed: true, selector: '.boss-popup__close' });
  }
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
  await wait(500);
  return JSON.stringify({
    closed: !hasResumePanel(),
    selector: '.boss-popup__close',
    escapeFallback: true,
    reason: hasResumePanel() ? 'panel_still_open' : ''
  });
})()
""".strip()

        ws = create_connection(websocket_url, timeout=5)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
        finally:
            ws.close()

        runtime_result = response.get("result", {}).get("result", {})
        if "value" not in runtime_result:
            return {
                "closed": False,
                "selector": "resume_panel",
                "reason": "runtime_evaluate_no_value",
                "response": response,
            }
        result = runtime_result["value"]
        if isinstance(result, dict):
            return result
        return json.loads(result)

    def open_online_resume(self, url_contains: str | None = "/web/chat/index") -> dict[str, Any]:
        if self.mock_dir is not None:
            fixture = self.mock_dir / "online_resume_open.json"
            if fixture.exists():
                return json.loads(fixture.read_text(encoding="utf-8"))
            return {
                "opened": True,
                "selector": "text:在线简历",
                "text": "\u5728\u7ebf\u7b80\u5386",
                "mock": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = """
(async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const text = (node) => node ? (node.innerText || node.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && style.visibility !== 'hidden'
      && style.display !== 'none';
  };
  const hasResumePanel = () => {
    const selectors = [
      'iframe[src*="/web/frame/c-resume"]',
      '.dialog-resume-full',
      '.lib-standard-resume',
      '.resume-layout-wrap',
      '.resume-detail-wrap',
      '.boss-popup__wrapper canvas',
      '.boss-dialog__body canvas'
    ];
    if (selectors.some((selector) => Array.from(document.querySelectorAll(selector)).some(isVisible))) {
      return true;
    }
    const popupText = text(document.querySelector('.boss-popup__wrapper, .boss-dialog__body, .dialog-wrap.active'));
    return popupText.includes('在线简历')
      || popupText.includes('工作经历')
      || popupText.includes('教育经历')
      || popupText.includes('期望职位')
      || popupText.includes('个人优势');
  };
  if (hasResumePanel()) {
    return JSON.stringify({ opened: true, alreadyOpen: true, selector: 'resume_panel' });
  }
  const roots = [
    document.querySelector('.chat-conversation'),
    document.querySelector('.conversation-container'),
    document.querySelector('.chat-im'),
    document.body
  ].filter(Boolean);
  const candidates = [];
  for (const root of roots) {
    for (const el of Array.from(root.querySelectorAll('button, a, span, div, [role="button"]'))) {
      const label = text(el).replace(/\s+/g, '');
      if (label === '在线简历') {
        const rect = el.getBoundingClientRect();
        candidates.push({ el, area: rect.width * rect.height });
      }
    }
  }
  candidates.sort((a, b) => a.area - b.area);
  const targetItem = candidates.find((item) => isVisible(item.el));
  const target = targetItem?.el || null;
  if (!target) {
    return JSON.stringify({ opened: false, selector: 'text:在线简历', reason: 'not_found' });
  }
  target.scrollIntoView({ block: 'center' });
  target.click();
  for (let i = 0; i < 24; i += 1) {
    await wait(250);
    if (hasResumePanel()) {
      return JSON.stringify({ opened: true, selector: 'text:在线简历', text: text(target), waits: i + 1 });
    }
  }
  return JSON.stringify({ opened: false, selector: 'text:在线简历', text: text(target), reason: 'resume_panel_not_loaded' });
})()
""".strip()

        return self._evaluate_json(websocket_url, expression, timeout=10)

    def click_first_common_phrase(self, url_contains: str | None = "/web/chat/index") -> dict[str, Any]:
        if self.mock_dir is not None:
            fixture = self.mock_dir / "first_common_phrase.json"
            if fixture.exists():
                return json.loads(fixture.read_text(encoding="utf-8"))
            return {
                "clicked": True,
                "selector": "text:\u5e38\u7528\u8bed",
                "phraseText": "\u4f60\u597d\u554a\uff0c\u53ef\u4ee5\u804a\u4e00\u804a~",
                "mock": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = """
(async () => {
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const text = (node) => node ? (node.innerText || node.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && style.visibility !== 'hidden'
      && style.display !== 'none';
  };
  const root = document.querySelector('.conversation-operate')
    || document.querySelector('.chat-conversation, .conversation, .chat-main, .chat-container')
    || document;
  const commonButton = document.querySelector('.conversation-operate .toolbar-icon.changyongyu')
    || Array.from(root.querySelectorAll('.operate-icon-item, button, a, span, div, [role="button"]'))
      .find((el) => isVisible(el) && text(el).replace(/\s+/g, '') === '常用语');
  if (!commonButton) {
    return JSON.stringify({
      clicked: false,
      selector: 'text:常用语',
      reason: 'common_phrase_button_not_found',
      visibleButtons: Array.from(root.querySelectorAll('button, a, [role="button"], span, div'))
        .filter(isVisible)
        .map((el) => text(el))
        .filter(Boolean)
        .slice(0, 60)
    });
  }
  commonButton.click();
  await wait(700);
  const panelCandidates = Array.from(document.querySelectorAll(
    '.phrase-content, .boss-popover, .popover, .common-phrase, .phrase-list, [class*="phrase"]'
  )).filter(isVisible);
  const panel = panelCandidates.find((el) => el.classList.contains('phrase-content'))
    || panelCandidates
    .filter((el) => text(el).length > 0)
    .sort((a, b) => text(b).length - text(a).length)[0]
    || null;
  if (!panel) {
    return JSON.stringify({
      clicked: false,
      selector: 'first_common_phrase',
      reason: 'common_phrase_panel_not_found'
    });
  }
  const phraseCandidates = Array.from(panel.querySelectorAll('li, p, a, span, div, [role="button"]'))
    .filter(isVisible)
    .map((el) => ({ el, value: text(el), className: String(el.className || '') }))
    .filter((item) => {
      const value = item.value.replace(/\s+/g, ' ').trim();
      if (!value) return false;
      if (value.includes('常用语') || value.includes('快捷回复') || value.includes('话术')) return false;
      if (value.includes('添加') || value.includes('管理') || value.includes('搜索') || value.includes('设置')) return false;
      if (item.className.includes('sentence-blank') || item.className.includes('link-add') || item.className.includes('title')) return false;
      return item.value.length >= 2 && item.value.length <= 200;
    });
  const target = phraseCandidates[0]?.el || null;
  if (!target) {
    return JSON.stringify({
      clicked: false,
      selector: 'first_common_phrase',
      reason: 'common_phrase_not_found',
      panelText: text(panel).slice(0, 500)
    });
  }
  const phraseText = text(target);
  target.click();
  return JSON.stringify({ clicked: true, selector: 'first_common_phrase', phraseText });
})()
""".strip()

        return self._evaluate_json(websocket_url, expression, timeout=10)

    def send_current_message(self, url_contains: str | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "sent": True,
                "selector": ".conversation-editor .submit.active",
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = """
(() => {
  const input = document.querySelector('.boss-chat-editor-input, .chat-input, [contenteditable="true"]');
  const text = input ? (input.innerText || input.textContent || '').trim() : '';
  if (!text) {
    return JSON.stringify({ sent: false, selector: '.conversation-editor .submit.active', reason: 'empty_input' });
  }
  const target = document.querySelector('.conversation-editor .submit.active');
  if (!target) {
    return JSON.stringify({ sent: false, selector: '.conversation-editor .submit.active', reason: 'not_found' });
  }
  target.click();
  return JSON.stringify({ sent: true, selector: '.conversation-editor .submit.active' });
})()
""".strip()

        ws = create_connection(websocket_url, timeout=5)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True},
            )
        finally:
            ws.close()

        return json.loads(response["result"]["result"]["value"])

    def download_resume(self, url_contains: str | None = None, downloads_dir: Path | None = None) -> dict[str, Any]:
        if self.mock_dir is not None:
            return json.loads((self.mock_dir / "download_resume.json").read_text(encoding="utf-8"))

        if downloads_dir is None:
            downloads_dir = Path.home() / "Downloads"

        started_at = time.time()

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        ws = create_connection(websocket_url, timeout=5)
        try:
            self._send_cdp_command(ws, method="Page.bringToFront", params={})
            preview_response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": build_attachment_preview_probe_expression(), "returnByValue": True},
            )
            preview_probe = json.loads(preview_response["result"]["result"]["value"])
            if not preview_probe.get("found"):
                return {"clicked": False, "downloaded": False, "file": ""}
            probe: dict[str, Any] = {"found": False}
            probe_deadline = time.time() + 5
            while time.time() < probe_deadline:
                response = self._send_cdp_command(
                    ws,
                    method="Runtime.evaluate",
                    params={"expression": build_download_click_probe_expression(), "returnByValue": True},
                )
                probe = json.loads(response["result"]["result"]["value"])
                if probe.get("found"):
                    break
                time.sleep(0.5)
            if not probe.get("found"):
                return {"clicked": False, "downloaded": False, "file": ""}

            x = probe["x"]
            y = probe["y"]
            self._send_cdp_command(
                ws,
                method="Input.dispatchMouseEvent",
                params={"type": "mouseMoved", "x": x, "y": y, "button": "left", "clickCount": 1},
            )
            self._send_cdp_command(
                ws,
                method="Input.dispatchMouseEvent",
                params={"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
            )
            self._send_cdp_command(
                ws,
                method="Input.dispatchMouseEvent",
                params={"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
            )
        finally:
            ws.close()

        end = time.time() + 10
        while time.time() < end:
            latest = self.find_latest_downloaded_file(downloads_dir=downloads_dir, not_before=started_at)
            if latest is not None:
                return {
                    "clicked": True,
                    "downloaded": True,
                    "file": str(latest),
                }
            time.sleep(1)

        return {
            "clicked": True,
            "downloaded": False,
            "file": "",
        }

    def find_latest_downloaded_file(self, downloads_dir: Path, not_before: float) -> Path | None:
        if not downloads_dir.exists():
            return None

        candidates = [
            file for file in downloads_dir.iterdir()
            if file.is_file()
            and not file.name.endswith(".crdownload")
            and file.stat().st_mtime >= not_before
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)[0]

    def open_recommend_page(self, url_contains: str | None = "/web/chat/recommend") -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "opened": True,
                "url": "https://www.zhipin.com/web/chat/recommend",
                "mock": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = """
(() => {
  const isRecommend = location.href.includes('/web/chat/recommend') || location.href.includes('/web/boss/recommend');
  return JSON.stringify({
    opened: isRecommend,
    navigated: false,
    url: location.href,
    reason: isRecommend ? '' : 'current_page_is_not_recommend'
  });
})()
""".strip()
        return self._evaluate_json(websocket_url, expression)

    def apply_recommend_filters(
        self,
        job_title: str,
        city: str,
        filters: dict[str, Any] | None = None,
        url_contains: str | None = "/web/chat/recommend",
        operation_delay_seconds: float = 0,
        max_filter_retries: int = 2,
        _attempt: int = 1,
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            tasks = [
                {
                    "rawKey": str(key),
                    "rawValue": value,
                    "expectedValues": self._split_filter_values(value),
                    "clickedValues": self._split_filter_values(value),
                    "missingValues": [],
                    "normalizedKey": self._normalize_recommend_filter_key(key),
                    "controlType": "mock",
                    "status": "verified",
                    "evidence": ["mock"],
                }
                for key, value in (filters or {}).items()
            ]
            return {
                "searched": True,
                "jobTitle": job_title,
                "city": city,
                "filters": filters or {},
                "appliedFilters": list((filters or {}).keys()),
                "clickedFilters": list((filters or {}).keys()),
                "missingFilters": [],
                "unverifiedFilters": [],
                "filterTasks": tasks,
                "filterVerificationPassed": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        raw_filters = filters or {}
        age_filter_items = [
            (key, value) for key, value in raw_filters.items()
            if self._is_age_filter_key(key) and self._parse_age_range(value)
        ]
        remaining_filters = {
            key: value for key, value in raw_filters.items()
            if key not in {item[0] for item in age_filter_items}
        }
        escaped_job_title = json.dumps(job_title, ensure_ascii=False)
        escaped_city = json.dumps(city, ensure_ascii=False)
        escaped_filters = json.dumps(remaining_filters, ensure_ascii=False)
        expression = build_apply_recommend_filters_expression(
            escaped_job_title=escaped_job_title,
            escaped_city=escaped_city,
            escaped_filters=escaped_filters,
            escaped_auto_confirm="false" if age_filter_items else "true",
            operation_delay_seconds=operation_delay_seconds,
        )
        filter_action_count = max(1, len(remaining_filters) + len(age_filter_items))
        expression_timeout = max(
            20,
            int(20 + (operation_delay_seconds * max(3, filter_action_count * 4))),
        )
        payload = self._evaluate_json(websocket_url, expression, timeout=expression_timeout)
        age_applied_filters: list[str] = []
        age_clicked_filters: list[str] = []
        age_missing_filters: list[str] = []
        age_special_filters: dict[str, Any] = {}
        age_filter_tasks: list[dict[str, Any]] = []
        for key, value in age_filter_items:
            result = self._apply_recommend_age_slider(
                websocket_url=websocket_url,
                raw_value=value,
                confirm=False,
                operation_delay_seconds=operation_delay_seconds,
            )
            age_special_filters[key] = result
            if result.get("applied"):
                age_clicked_filters.append(key)
            if result.get("applied"):
                if result.get("verified"):
                    age_applied_filters.append(key)
            else:
                age_missing_filters.append(key)
            age_filter_tasks.append(
                {
                    "rawKey": str(key),
                    "rawValue": value,
                    "expectedValues": self._split_filter_values(value),
                    "clickedValues": [str(value)] if result.get("applied") else [],
                    "missingValues": [] if result.get("applied") else [str(value)],
                    "normalizedKey": self._normalize_recommend_filter_key(key),
                    "controlType": result.get("method") or "slider",
                    "status": "verified" if result.get("verified") else ("unverified" if result.get("applied") else "missing"),
                    "evidence": ["age_slider_actual_value"] if result.get("verified") else [],
                    "actual": result.get("actual", []),
                    "verified": bool(result.get("verified")),
                }
            )
        if (payload.get("needsConfirm") or age_clicked_filters) and not payload.get("confirmed"):
            confirm_result = self._confirm_recommend_filter_panel(websocket_url)
            if operation_delay_seconds > 0:
                time.sleep(operation_delay_seconds)
            payload["confirmResult"] = confirm_result
            payload["confirmed"] = bool(confirm_result.get("clicked"))
        payload["filters"] = raw_filters
        payload.setdefault("specialFilters", {}).update(age_special_filters)
        payload["appliedFilters"] = payload.get("appliedFilters", []) + age_applied_filters
        payload["clickedFilters"] = payload.get("clickedFilters", []) + age_clicked_filters
        payload["missingFilters"] = payload.get("missingFilters", []) + age_missing_filters
        payload["filterTasks"] = payload.get("filterTasks", []) + age_filter_tasks
        verification = payload.setdefault("verification", {})
        for key, value in age_filter_items:
            result = age_special_filters.get(key, {})
            verification[key] = {
                "applied": bool(result.get("applied")),
                "verified": bool(result.get("verified")),
                "expected": value,
                "actual": result.get("actual", []),
            }
        payload["unverifiedFilters"] = [
            str(key) for key, item in verification.items()
            if not item.get("verified")
        ]
        unverified_filters = [str(item) for item in payload.get("unverifiedFilters", [])]
        if unverified_filters:
            missing = list(payload.get("missingFilters", []))
            payload["missingFilters"] = missing + [item for item in unverified_filters if item not in missing]
        if not unverified_filters and raw_filters:
            time.sleep(1.0)
            result_verification = self._verify_recommend_result_filters(websocket_url, raw_filters)
            payload["resultVerification"] = result_verification
            failed_result_filters = [
                str(item.get("key") or "")
                for item in result_verification.get("failedFilters", [])
                if item.get("key")
            ]
            payload["resultFailedFilters"] = failed_result_filters
            payload["resultVerificationPassed"] = not failed_result_filters
        elif raw_filters:
            payload.setdefault("resultVerification", None)
            payload.setdefault("resultFailedFilters", [])
            payload.setdefault("resultVerificationPassed", False)
        payload["filterVerificationPassed"] = not unverified_filters
        payload["skippedSearch"] = not (
            payload.get("applied", {}).get("jobTitle", {}).get("applied")
            or payload.get("applied", {}).get("city", {}).get("applied")
            or payload.get("appliedFilters")
        )
        failed_retry_filters = self._recommend_failed_retry_filters(raw_filters, payload)
        if failed_retry_filters and _attempt <= max(0, max_filter_retries):
            retry_payload = self.apply_recommend_filters(
                job_title=job_title,
                city=city,
                filters=failed_retry_filters,
                url_contains=url_contains,
                operation_delay_seconds=operation_delay_seconds,
                max_filter_retries=max_filter_retries,
                _attempt=_attempt + 1,
            )
            return self._merge_recommend_filter_retry_payloads(
                base=payload,
                retry=retry_payload,
                raw_filters=raw_filters,
                retried_filters=failed_retry_filters,
                attempt=_attempt + 1,
            )
        payload.setdefault("retryCount", _attempt - 1)
        payload.setdefault("retryHistory", [])
        return payload

    @staticmethod
    def _recommend_failed_retry_filters(
        raw_filters: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, str]:
        verification = payload.get("verification") or {}
        failed_keys: list[str] = []
        for key in payload.get("unverifiedFilters", []) or []:
            if str(key) not in failed_keys:
                failed_keys.append(str(key))
        for key in payload.get("missingFilters", []) or []:
            key_text = str(key)
            item = verification.get(key_text) or {}
            if (not item or not item.get("verified")) and key_text not in failed_keys:
                failed_keys.append(key_text)
        retry_filters: dict[str, Any] = {}
        for key, value in raw_filters.items():
            if str(key) not in failed_keys:
                continue
            item = verification.get(str(key)) or verification.get(key) or {}
            missing_values = [
                str(item_value)
                for item_value in item.get("missingValues", [])
                if str(item_value).strip()
            ]
            retry_filters[key] = ",".join(missing_values) if missing_values else value
        return retry_filters

    @classmethod
    def _merge_recommend_filter_retry_payloads(
        cls,
        base: dict[str, Any],
        retry: dict[str, Any],
        raw_filters: dict[str, Any],
        retried_filters: dict[str, Any],
        attempt: int,
    ) -> dict[str, Any]:
        merged = {**base, **retry}
        merged["filters"] = raw_filters
        merged["previousAttempt"] = base
        merged["retryCount"] = max(
            int(base.get("retryCount") or 0),
            int(retry.get("retryCount") or 0),
            attempt - 1,
        )
        merged["retryHistory"] = [
            *list(base.get("retryHistory") or []),
            {"attempt": attempt, "filters": retried_filters},
            *list(retry.get("retryHistory") or []),
        ]

        special_filters = {
            **(base.get("specialFilters") or {}),
            **(retry.get("specialFilters") or {}),
        }
        verification = {
            **(base.get("verification") or {}),
            **(retry.get("verification") or {}),
        }
        merged["specialFilters"] = special_filters
        merged["verification"] = verification

        merged["filterTasks"] = cls._merge_filter_tasks(
            list(base.get("filterTasks") or []),
            list(retry.get("filterTasks") or []),
        )
        merged["appliedFilters"] = cls._ordered_unique(
            list(base.get("appliedFilters") or []) + list(retry.get("appliedFilters") or [])
        )
        merged["clickedFilters"] = cls._ordered_unique(
            list(base.get("clickedFilters") or []) + list(retry.get("clickedFilters") or [])
        )
        unverified = [
            str(key) for key in raw_filters
            if not (verification.get(str(key)) or verification.get(key) or {}).get("verified")
        ]
        merged["unverifiedFilters"] = unverified
        missing = cls._ordered_unique(
            [
                str(item) for item in (
                    list(base.get("missingFilters") or []) + list(retry.get("missingFilters") or []) + unverified
                )
                if str(item) in {str(key) for key in raw_filters}
                and not (verification.get(str(item)) or {}).get("verified")
            ]
        )
        merged["missingFilters"] = missing
        merged["filterVerificationPassed"] = not unverified
        merged["skippedSearch"] = not (
            merged.get("applied", {}).get("jobTitle", {}).get("applied")
            or merged.get("applied", {}).get("city", {}).get("applied")
            or merged.get("appliedFilters")
        )
        return merged

    @staticmethod
    def _merge_filter_tasks(
        base_tasks: list[dict[str, Any]],
        retry_tasks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for task in base_tasks + retry_tasks:
            key = str(task.get("rawKey") or "")
            if not key:
                key = f"__index_{len(order)}"
            if key not in merged:
                order.append(key)
            merged[key] = task
        return [merged[key] for key in order]

    @staticmethod
    def _ordered_unique(items: list[Any]) -> list[Any]:
        result: list[Any] = []
        seen: set[str] = set()
        for item in items:
            key = str(item)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    @staticmethod
    def _split_filter_values(value: Any) -> list[str]:
        if isinstance(value, (list, tuple, set)):
            raw_items = [str(item) for item in value]
        else:
            raw_items = re.split(r"[,，、;；|/]|(?:\s+or\s+)|或", str(value or ""), flags=re.IGNORECASE)
        return [item.strip() for item in raw_items if item.strip()]

    @staticmethod
    def _normalize_recommend_filter_key(key: str) -> str:
        raw = str(key or "").strip().lower()
        aliases = {
            "salary": "钖祫寰呴亣",
            "钖祫": "钖祫寰呴亣",
            "钖祫寰呴亣[鍗曢€塢": "钖祫寰呴亣",
            "experience": "缁忛獙瑕佹眰",
            "缁忛獙": "缁忛獙瑕佹眰",
            "宸ヤ綔缁忛獙": "缁忛獙瑕佹眰",
            "education": "瀛﹀巻瑕佹眰",
            "degree": "瀛﹀巻瑕佹眰",
            "瀛﹀巻": "瀛﹀巻瑕佹眰",
            "active": "\u6d3b\u8dc3\u5ea6",
            "\u6d3b\u8dc3": "\u6d3b\u8dc3\u5ea6",
        }
        aliases.update({
            "薪资": "薪资待遇",
            "薪资待遇": "薪资待遇",
            "薪资待遇[单选]": "薪资待遇",
            "经验": "经验要求",
            "工作经验": "经验要求",
            "经验要求": "经验要求",
            "学历": "学历要求",
            "学历要求": "学历要求",
            "活跃": "活跃度",
            "活跃度": "活跃度",
        })
        return aliases.get(raw, str(key or "").strip())

    def _verify_recommend_result_filters(
        self,
        websocket_url: str,
        filters: dict[str, Any],
        sample_size: int = 8,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        candidates: list[dict[str, Any]] = []
        last_error = ""
        for _ in range(6):
            try:
                payload = self._evaluate_json(
                    websocket_url,
                    build_capture_recommend_candidates_expression_v2(max_count=sample_size),
                    timeout=10,
                )
                candidates = payload.get("candidates", [])
                if candidates:
                    break
            except Exception as exc:
                last_error = str(exc)
            time.sleep(0.8)
        if last_error and not candidates:
            return {
                "verified": False,
                "reason": "capture_candidates_failed",
                "error": last_error,
                "failedFilters": [{"key": key, "reason": "capture_failed"} for key in filters],
            }
        if not candidates:
            return {
                "verified": False,
                "reason": "no_candidates_after_filter",
                "failedFilters": [{"key": key, "reason": "no_candidates"} for key in filters],
                "sampleCount": 0,
            }

        failed_filters: list[dict[str, Any]] = []
        checks: dict[str, Any] = {}
        for key, expected in filters.items():
            normalized_key = str(key).lower()
            if self._is_age_filter_key(key):
                age_range = self._parse_age_range(expected)
                if not age_range:
                    continue
                lower, upper = age_range
                mismatches = []
                unknown = []
                for item in candidates:
                    raw = str(item.get("detailTextPreview") or "")
                    match = re.search(r"(\d{1,2})\s*\u5c81", raw)
                    if not match:
                        unknown.append(item.get("candidateName") or item.get("cardIndex"))
                        continue
                    age = int(match.group(1))
                    if age < lower or age > upper:
                        mismatches.append(
                            {
                                "candidate": item.get("candidateName"),
                                "age": age,
                                "preview": raw[:120],
                            }
                        )
                checks[key] = {"expected": expected, "mismatches": mismatches, "unknown": unknown}
                if mismatches:
                    failed_filters.append({"key": key, "reason": "candidate_age_out_of_range", "mismatches": mismatches})
            elif any(label in normalized_key for label in ("\u5b66\u5386", "\u6559\u80b2")):
                expected_levels = [
                    level for level in (
                        self._normalize_education_level(value)
                        for value in self._split_filter_values(expected)
                    )
                    if level
                ]
                if not expected_levels:
                    continue
                mismatches = []
                unknown = []
                for item in candidates:
                    raw = str(item.get("detailTextPreview") or "")
                    level = self._highest_education_level(raw)
                    if not level:
                        unknown.append(item.get("candidateName") or item.get("cardIndex"))
                        continue
                    if not any(self._education_meets(level, expected_level) for expected_level in expected_levels):
                        mismatches.append(
                            {
                                "candidate": item.get("candidateName"),
                                "education": level,
                                "preview": raw[:120],
                            }
                        )
                checks[key] = {"expected": expected, "mismatches": mismatches, "unknown": unknown}
                if mismatches:
                    failed_filters.append({"key": key, "reason": "candidate_education_below_expected", "mismatches": mismatches})
            elif any(label in normalized_key for label in ("\u85aa\u8d44", "\u5f85\u9047")):
                salary_ranges = [
                    salary_range for salary_range in (
                        self._parse_salary_range(value)
                        for value in self._split_filter_values(expected)
                    )
                    if salary_range
                ]
                if not salary_ranges:
                    continue
                mismatches = []
                unknown = []
                for item in candidates:
                    raw_salary = str(item.get("salary") or "")
                    parsed = self._parse_salary_range(raw_salary)
                    if not parsed:
                        unknown.append(item.get("candidateName") or item.get("cardIndex"))
                        continue
                    item_lower, item_upper = parsed
                    if not any(item_upper >= lower and item_lower <= upper for lower, upper in salary_ranges):
                        mismatches.append(
                            {
                                "candidate": item.get("candidateName"),
                                "salary": raw_salary,
                                "preview": str(item.get("detailTextPreview") or "")[:120],
                            }
                        )
                checks[key] = {"expected": expected, "mismatches": mismatches, "unknown": unknown}
                if mismatches:
                    failed_filters.append({"key": key, "reason": "candidate_salary_out_of_range", "mismatches": mismatches})
            elif any(label in normalized_key for label in ("\u7ecf\u9a8c", "\u5de5\u4f5c\u7ecf\u9a8c")):
                experience_ranges = [
                    experience_range for experience_range in (
                        self._parse_experience_filter_range(value)
                        for value in self._split_filter_values(expected)
                    )
                    if experience_range
                ]
                if not experience_ranges:
                    continue
                mismatches = []
                unknown = []
                for item in candidates:
                    raw = str(item.get("detailTextPreview") or "")
                    years = self._parse_card_work_years(raw)
                    if years is None:
                        unknown.append(item.get("candidateName") or item.get("cardIndex"))
                        continue
                    if not any(lower <= years <= upper for lower, upper in experience_ranges):
                        mismatches.append(
                            {
                                "candidate": item.get("candidateName"),
                                "workYears": years,
                                "preview": raw[:120],
                            }
                        )
                checks[key] = {"expected": expected, "mismatches": mismatches, "unknown": unknown}
                if mismatches:
                    failed_filters.append({"key": key, "reason": "candidate_experience_out_of_range", "mismatches": mismatches})
        return {
            "verified": not failed_filters,
            "sampleCount": len(candidates),
            "checks": checks,
            "failedFilters": failed_filters,
            "candidates": candidates,
        }

    @staticmethod
    def _normalize_education_level(value: str) -> str:
        raw = str(value or "")
        levels = ["\u521d\u4e2d", "\u4e2d\u4e13", "\u9ad8\u4e2d", "\u5927\u4e13", "\u672c\u79d1", "\u7855\u58eb", "\u7814\u7a76\u751f", "\u535a\u58eb"]
        for level in reversed(levels):
            if level in raw:
                return "\u7855\u58eb" if level == "\u7814\u7a76\u751f" else level
        return ""

    @staticmethod
    def _highest_education_level(value: str) -> str:
        raw = str(value or "")
        order = ["\u521d\u4e2d", "\u4e2d\u4e13", "\u9ad8\u4e2d", "\u5927\u4e13", "\u672c\u79d1", "\u7855\u58eb", "\u7814\u7a76\u751f", "\u535a\u58eb"]
        found = [level for level in order if level in raw]
        if "\u7814\u7a76\u751f" in found:
            found.append("\u7855\u58eb")
        rank = {"\u521d\u4e2d": 1, "\u4e2d\u4e13": 2, "\u9ad8\u4e2d": 2, "\u5927\u4e13": 3, "\u672c\u79d1": 4, "\u7855\u58eb": 5, "\u7814\u7a76\u751f": 5, "\u535a\u58eb": 6}
        return max(found, key=lambda item: rank.get(item, 0), default="")

    @staticmethod
    def _education_meets(actual: str, expected: str) -> bool:
        rank = {"\u521d\u4e2d": 1, "\u4e2d\u4e13": 2, "\u9ad8\u4e2d": 2, "\u5927\u4e13": 3, "\u672c\u79d1": 4, "\u7855\u58eb": 5, "\u7814\u7a76\u751f": 5, "\u535a\u58eb": 6}
        return rank.get(actual, 0) >= rank.get(expected, 0)

    @staticmethod
    def _parse_salary_range(value: str) -> tuple[int, int] | None:
        raw = str(value or "").lower()
        numbers = [int(item) for item in re.findall(r"\d+", raw)]
        if not numbers:
            return None
        if len(numbers) == 1:
            return numbers[0], numbers[0]
        lower, upper = numbers[0], numbers[1]
        if lower > upper:
            lower, upper = upper, lower
        return lower, upper

    @staticmethod
    def _parse_experience_filter_range(value: str) -> tuple[int, int] | None:
        raw = str(value or "")
        if "\u4e0d\u9650" in raw:
            return None
        if "\u5e94\u5c4a" in raw or "\u5728\u6821" in raw:
            return 0, 0
        numbers = [int(item) for item in re.findall(r"\d+", raw)]
        if not numbers:
            return None
        if "\u4ee5\u4e0a" in raw or len(numbers) == 1:
            return numbers[0], 99
        lower, upper = numbers[0], numbers[1]
        if lower > upper:
            lower, upper = upper, lower
        return lower, upper

    @staticmethod
    def _parse_card_work_years(value: str) -> int | None:
        raw = str(value or "")
        if "\u5e94\u5c4a" in raw or "\u5728\u6821" in raw:
            return 0
        match = re.search(r"(\d{1,2})\s*\u5e74(?:\s|\u672c\u79d1|\u5927\u4e13|\u7855\u58eb|\u535a\u58eb|\u9ad8\u4e2d|\u4e2d\u4e13)", raw)
        if match:
            return int(match.group(1))
        match = re.search(r"(\d{1,2})\s*\u5e74\u4ee5\u4e0a", raw)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _is_age_filter_key(key: str) -> bool:
        normalized = str(key or "").strip().lower()
        age_labels = ("\u5e74\u9f84", "\u5e74\u7eaa", "age", "骞撮緞", "骞寸邯")
        return any(label.lower() in normalized for label in age_labels)

    @staticmethod
    def _parse_age_range(raw_value: str) -> tuple[int, int] | None:
        digits: list[str] = []
        current = ""
        for char in str(raw_value):
            if char.isdigit():
                current += char
            elif current:
                digits.append(current)
                current = ""
        if current:
            digits.append(current)
        if len(digits) < 2:
            return None
        lower = int(digits[0])
        upper = int(digits[1])
        if lower > upper:
            lower, upper = upper, lower
        return lower, upper

    def _apply_recommend_age_slider(
        self,
        websocket_url: str,
        raw_value: str,
        confirm: bool = True,
        operation_delay_seconds: float = 0,
    ) -> dict[str, Any]:
        age_range = self._parse_age_range(raw_value)
        if not age_range:
            return {"applied": False, "reason": "invalid_age_range"}
        lower, upper = age_range

        open_result = self._recommend_filter_panel_state(websocket_url)
        if not open_result.get("panelOpen"):
            filter_button = open_result.get("filterButton")
            if not filter_button:
                return {"applied": False, "reason": "filter_button_not_found"}
            self._dispatch_mouse_click(websocket_url, filter_button["x"], filter_button["y"])
            time.sleep(operation_delay_seconds if operation_delay_seconds > 0 else 0.6)

        slider = self._read_age_values_until_ready(websocket_url)
        if not self._is_age_slider_ready(slider):
            forced = self._force_recommend_age_slider_value(websocket_url, lower, upper)
            time.sleep(0.5)
            slider = self._read_age_values_until_ready(websocket_url, max_attempts=2)
            slider["forceSet"] = forced
            if not self._is_age_slider_ready(slider):
                return {"applied": False, "reason": "age_slider_not_found", "state": slider}

        lower_result = self._drag_age_handle_until_value(websocket_url, 0, lower)
        if operation_delay_seconds > 0:
            time.sleep(operation_delay_seconds)
        upper_result = self._drag_age_handle_until_value(websocket_url, 1, upper)
        if operation_delay_seconds > 0:
            time.sleep(operation_delay_seconds)
        age_attempts = lower_result.get("attempts", []) + upper_result.get("attempts", [])

        verified_slider = self._read_age_values(websocket_url)
        verified_labels = [str(dot.get("label") or "") for dot in verified_slider.get("dots", [])[:2]]
        verified = str(lower) in verified_labels and str(upper) in verified_labels

        confirm_button = self._recommend_filter_panel_state(websocket_url).get("confirmButton")
        if confirm and confirm_button:
            self._dispatch_mouse_click(websocket_url, confirm_button["x"], confirm_button["y"])
            time.sleep(operation_delay_seconds if operation_delay_seconds > 0 else 1.0)

        return {
            "applied": True,
            "method": "cdp_slider",
            "min": lower,
            "max": upper,
            "calibratedFrom": [dot.get("label") for dot in sorted(slider.get("dots", [])[:2], key=lambda item: item["x"])],
            "actual": verified_labels,
            "verified": verified,
            "ageAttempts": age_attempts,
            "lowerResult": lower_result,
            "upperResult": upper_result,
            "fallbackScale": lower_result.get("fallbackScale") or upper_result.get("fallbackScale"),
            "reason": "" if verified else self._age_failure_reason(lower, upper, verified_labels, lower_result, upper_result),
            "confirmed": bool(confirm and confirm_button),
            "frameUrl": slider.get("frameUrl", ""),
        }

    def _age_failure_reason(
        self,
        lower: int,
        upper: int,
        actual: list[str],
        lower_result: dict[str, Any],
        upper_result: dict[str, Any],
    ) -> str:
        if not lower_result.get("verified"):
            actual_lower = (actual or ["\u672a\u77e5"])[0]
            return f"\u5e74\u9f84\u5de6\u6ed1\u5757\u76ee\u6807{lower}\uff0c\u8fde\u7eed{lower_result.get('attemptCount', 0)}\u6b21\u540e\u4ecd\u4e3a{actual_lower}"
        if not upper_result.get("verified"):
            actual_upper = actual[1] if len(actual) > 1 else "\u672a\u77e5"
            if upper_result.get("reason") == "age_unlimited_not_resolved" or any(
                item.get("beforeUnlimited") or item.get("afterUnlimited")
                for item in upper_result.get("attempts", [])
            ):
                return f"\u5e74\u9f84\u53f3\u6ed1\u5757\u76ee\u6807{upper}\uff0c\u4ece\u4e0d\u9650\u5411\u5de6\u62d6\u52a8{upper_result.get('attemptCount', 0)}\u6b21\u540e\u4ecd\u672a\u8bfb\u5230{upper}\uff0c\u5b9e\u9645\u4e3a{actual_upper}"
            return f"\u5e74\u9f84\u53f3\u6ed1\u5757\u76ee\u6807{upper}\uff0c\u8fde\u7eed{upper_result.get('attemptCount', 0)}\u6b21\u540e\u4ecd\u4e3a{actual_upper}"
        return ""

    def _read_age_values(self, websocket_url: str) -> dict[str, Any]:
        return self._recommend_age_slider_state(websocket_url)

    def _read_age_values_until_ready(self, websocket_url: str, max_attempts: int = 6) -> dict[str, Any]:
        latest: dict[str, Any] = {}
        for attempt in range(1, max_attempts + 1):
            prep = self._prepare_recommend_age_slider(websocket_url, reopen=attempt in {4, 6})
            time.sleep(0.25 if attempt < 4 else 0.6)
            latest = self._read_age_values(websocket_url)
            latest["prepareAttempt"] = attempt
            latest["prepare"] = prep
            if self._is_age_slider_ready(latest):
                return latest
        return latest

    @staticmethod
    def _is_age_slider_ready(slider: dict[str, Any]) -> bool:
        rail = slider.get("rail") or {}
        dots = sorted(slider.get("dots", [])[:2], key=lambda item: item.get("x", 0))
        if not rail or float(rail.get("w") or 0) < 80 or len(dots) < 2:
            return False
        labels = [str(dot.get("label") or "").strip() for dot in dots]
        values = [dot.get("value") for dot in dots]
        if labels == ["0", "0"] or values == [0, 0]:
            return False
        if len(dots) == 1 and values[0] == 0:
            return False
        return True

    def _prepare_recommend_age_slider(self, websocket_url: str, reopen: bool = False) -> dict[str, Any]:
        expression = f"""
(async () => {{
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const win = doc.defaultView || window;
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const visible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = win.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }};
  const clickElement = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    for (const type of ['mouseover', 'mousemove', 'mousedown', 'mouseup', 'click']) {{
      el.dispatchEvent(new win.MouseEvent(type, {{
        bubbles: true,
        cancelable: true,
        view: win,
        clientX: x,
        clientY: y,
        button: 0,
        buttons: type === 'mousedown' ? 1 : 0
      }}));
    }}
    el.click?.();
    return true;
  }};
  const filterButton = () => Array.from(doc.querySelectorAll('.recommend-filter, .filter-label-wrap, .filter-label, button, a, span, label, li, div'))
    .filter((el) => visible(el) && text(el).includes('\\u7b5b\\u9009'))
    .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width)[0];
  if ({str(reopen).lower()} && doc.querySelector('.filter-panel')) {{
    clickElement(filterButton());
    await delay(250);
  }}
  if (!doc.querySelector('.filter-panel')) {{
    clickElement(filterButton());
    await delay(500);
  }}
  const panel = doc.querySelector('.filter-panel');
  const age = doc.querySelector('.filter-item.age')
    || Array.from(doc.querySelectorAll('.filter-item, .condition-item, .filter-wrap'))
      .find((el) => text(el).includes('\\u5e74\\u9f84'));
  if (panel) {{
    panel.scrollTop = 0;
    for (const el of Array.from(panel.querySelectorAll('*'))) {{
      if (el.scrollHeight > el.clientHeight + 20) el.scrollTop = 0;
    }}
  }}
  if (age) {{
    age.scrollIntoView?.({{ block: 'center', inline: 'nearest' }});
    await delay(300);
    const parent = age.closest('.filter-panel, .filter-content, .condition-list') || panel;
    if (parent && parent.scrollHeight > parent.clientHeight) {{
      parent.scrollTop = Math.max(0, age.offsetTop - 80);
      await delay(300);
    }}
  }}
  const rail = age?.querySelector('.vue-slider-rail');
  const dots = Array.from(age?.querySelectorAll('.vue-slider > .vue-slider-dot, .vue-slider-rail > .vue-slider-dot') || []);
  const rect = (el) => {{
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return {{ x: r.x, y: r.y, width: r.width, height: r.height, top: r.top, bottom: r.bottom }};
  }};
  return JSON.stringify({{
    panelOpen: Boolean(doc.querySelector('.filter-panel')),
    ageFound: Boolean(age),
    ageText: text(age).slice(0, 200),
    ageRect: rect(age),
    railRect: rect(rail),
    dotCount: dots.length,
    dots: dots.slice(0, 4).map((dot) => ({{ text: text(dot), rect: rect(dot) }})),
    frameUrl: frame?.src || ''
  }});
}})()
""".strip()
        return self._evaluate_json(websocket_url, expression, timeout=10)

    def _force_recommend_age_slider_value(self, websocket_url: str, lower: int, upper: int) -> dict[str, Any]:
        expression = f"""
(() => {{
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const age = doc.querySelector('.filter-item.age')
    || Array.from(doc.querySelectorAll('.filter-item, .condition-item, .filter-wrap'))
      .find((el) => text(el).includes('\\u5e74\\u9f84'));
  const slider = age?.querySelector('.vue-slider');
  const vue = slider?.__vue__;
  const parent = vue?.$parent;
  const before = {{
    value: vue?._props?.value,
    dotsValue: vue?._data?.control?.dotsValue,
    text: text(age),
    dotCount: age?.querySelectorAll('.vue-slider-dot').length || 0
  }};
  const target = [{int(lower)}, {int(upper)}];
  const calls = [];
  try {{
    if (vue?.setValue) {{
      vue.setValue(target);
      calls.push('vue.setValue');
    }}
  }} catch (error) {{
    calls.push('vue.setValue error ' + String(error));
  }}
  try {{
    if (parent?.changeAge) {{
      parent.changeAge(target);
      calls.push('parent.changeAge');
    }}
  }} catch (error) {{
    calls.push('parent.changeAge error ' + String(error));
  }}
  try {{
    vue?.$emit?.('change', target);
    calls.push('vue.emit.change');
  }} catch (error) {{
    calls.push('vue.emit.change error ' + String(error));
  }}
  const input = age?.querySelector('input[type="range"]');
  if (input) {{
    input.value = String({int(lower)});
    input.dispatchEvent(new Event('input', {{ bubbles: true }}));
    input.dispatchEvent(new Event('change', {{ bubbles: true }}));
    calls.push('input.change');
  }}
  return JSON.stringify({{
    applied: calls.length > 0,
    calls,
    before,
    after: {{
      value: vue?._props?.value,
      dotsValue: vue?._data?.control?.dotsValue,
      text: text(age),
      dotCount: age?.querySelectorAll('.vue-slider-dot').length || 0
    }}
  }});
}})()
""".strip()
        return self._evaluate_json(websocket_url, expression, timeout=10)

    def _drag_age_handle_until_value(
        self,
        websocket_url: str,
        handle_index: int,
        target_age: int,
        max_attempts: int = 8,
    ) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        pixels_per_year: float | None = None
        fallback_scale = None
        no_change_count = 0

        for attempt_index in range(1, max_attempts + 1):
            state = self._read_age_values(websocket_url)
            rail = state.get("rail") or {}
            dots = sorted(state.get("dots", [])[:2], key=lambda item: item.get("x", 0))
            if not rail or len(dots) < 2:
                return {
                    "verified": False,
                    "target": target_age,
                    "handleIndex": handle_index,
                    "attempts": attempts,
                    "attemptCount": len(attempts),
                    "reason": "age_slider_state_missing",
                }

            dot = dots[handle_index]
            current = dot.get("value")
            if current == target_age:
                return {
                    "verified": True,
                    "target": target_age,
                    "handleIndex": handle_index,
                    "actual": current,
                    "attempts": attempts,
                    "attemptCount": len(attempts),
                    "fallbackScale": fallback_scale,
                }
            if not isinstance(current, int):
                if not (handle_index == 1 and dot.get("isUnlimited")):
                    return {
                        "verified": False,
                        "target": target_age,
                        "handleIndex": handle_index,
                        "attempts": attempts,
                        "attemptCount": len(attempts),
                        "reason": "age_handle_value_missing",
                    }

            measured_pixels = self._resolve_age_pixels_per_year(dots)
            if measured_pixels is not None:
                pixels_per_year = measured_pixels
            if pixels_per_year is None:
                fallback_scale = [16, 46]
                pixels_per_year = float(rail.get("w", 0) or 0) / (fallback_scale[1] - fallback_scale[0])
            if not pixels_per_year:
                return {
                    "verified": False,
                    "target": target_age,
                    "handleIndex": handle_index,
                    "attempts": attempts,
                    "attemptCount": len(attempts),
                    "reason": "age_pixels_per_year_missing",
                }

            phase = "calibrate_to_target"
            current_for_delta = current
            if not isinstance(current_for_delta, int) and handle_index == 1 and dot.get("isUnlimited"):
                phase = "leave_unlimited"
                current_for_delta = 46
                if no_change_count:
                    current_for_delta += no_change_count * 2
            delta_years = target_age - current_for_delta
            direction = -1 if delta_years < 0 else 1
            multiplier = 1 + (0.55 * no_change_count)
            pixel_delta = delta_years * pixels_per_year * multiplier
            minimum_delta = direction * max(4.0, abs(pixels_per_year) * min(abs(delta_years), 1))
            if abs(pixel_delta) < abs(minimum_delta):
                pixel_delta = minimum_delta

            min_x = rail.get("left", rail.get("x", 0))
            max_x = rail.get("right", rail.get("x", 0) + rail.get("w", 0))
            if handle_index == 0:
                max_x = min(max_x, dots[1]["x"] - 2)
            else:
                min_x = max(min_x, dots[0]["x"] + 2)
            target_x = max(min_x, min(max_x, dot["x"] + pixel_delta))
            target_y = rail.get("y", dot.get("y"))

            self._dispatch_mouse_drag_precise(websocket_url, dot["x"], dot["y"], target_x, target_y)
            time.sleep(0.35)

            after_state = self._read_age_values(websocket_url)
            after_dots = sorted(after_state.get("dots", [])[:2], key=lambda item: item.get("x", 0))
            after_dot = after_dots[handle_index] if len(after_dots) > handle_index else {}
            after_value = after_dot.get("value")
            changed_years = after_value - current if isinstance(after_value, int) and isinstance(current, int) else None
            if isinstance(after_value, int) and not isinstance(current, int) and dot.get("isUnlimited"):
                changed_years = after_value - current_for_delta
            if changed_years:
                observed_pixels_per_year = abs(target_x - dot["x"]) / abs(changed_years)
                pixels_per_year = (pixels_per_year * 0.45) + (observed_pixels_per_year * 0.55)
                no_change_count = 0
            else:
                no_change_count += 1

            attempt = {
                "handleIndex": handle_index,
                "attempt": attempt_index,
                "target": target_age,
                "before": current,
                "after": after_value,
                "beforeLabel": dot.get("label") or "",
                "afterLabel": after_dot.get("label") or "",
                "beforeUnlimited": bool(dot.get("isUnlimited")),
                "afterUnlimited": bool(after_dot.get("isUnlimited")),
                "phase": phase,
                "direction": "left" if direction < 0 else "right",
                "pixels": round(target_x - dot["x"], 2),
                "hit": after_value == target_age,
                "pixelsPerYear": round(pixels_per_year, 4) if pixels_per_year else None,
            }
            attempts.append(attempt)
            if after_value == target_age:
                return {
                    "verified": True,
                    "target": target_age,
                    "handleIndex": handle_index,
                    "actual": after_value,
                    "attempts": attempts,
                    "attemptCount": len(attempts),
                    "fallbackScale": fallback_scale,
                }

        final_state = self._read_age_values(websocket_url)
        final_dots = sorted(final_state.get("dots", [])[:2], key=lambda item: item.get("x", 0))
        final_dot = final_dots[handle_index] if len(final_dots) > handle_index else {}
        final_value = final_dot.get("value")
        return {
            "verified": final_value == target_age,
            "target": target_age,
            "handleIndex": handle_index,
            "actual": final_value,
            "attempts": attempts,
            "attemptCount": len(attempts),
            "fallbackScale": fallback_scale,
            "reason": "age_unlimited_not_resolved" if final_dot.get("isUnlimited") else "",
        }

    @staticmethod
    def _resolve_age_pixels_per_year(dots: list[dict[str, Any]]) -> float | None:
        if len(dots) < 2:
            return None
        left = dots[0]
        right = dots[1]
        if not isinstance(left.get("value"), int) or not isinstance(right.get("value"), int):
            return None
        if left["value"] == right["value"]:
            return None
        return (right["x"] - left["x"]) / (right["value"] - left["value"])

    def _recommend_filter_panel_state(self, websocket_url: str) -> dict[str, Any]:
        expression = r"""
(() => {
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const frameRect = frame ? frame.getBoundingClientRect() : { left: 0, top: 0 };
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = (doc.defaultView || window).getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const point = (el) => {
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { x: frameRect.left + rect.left + rect.width / 2, y: frameRect.top + rect.top + rect.height / 2, text: text(el) };
  };
  const filterButton = Array.from(doc.querySelectorAll('.filter-label, .filter-label-wrap, .recommend-filter, button, a, span, label, li, div'))
    .filter((el) => visible(el) && text(el).includes('\u7b5b\u9009'))
    .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width)[0];
  const confirmButton = Array.from(doc.querySelectorAll('button, a, span, label, li, div'))
    .filter((el) => visible(el) && text(el) === '\u786e\u5b9a')
    .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width)[0];
  return JSON.stringify({
    panelOpen: Boolean(doc.querySelector('.filter-panel')),
    filterButton: point(filterButton),
    confirmButton: point(confirmButton),
    frameUrl: frame?.src || ''
  });
})()
""".strip()
        return self._evaluate_json(websocket_url, expression)

    def _confirm_recommend_filter_panel(self, websocket_url: str) -> dict[str, Any]:
        expression = r"""
(async () => {
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const win = doc.defaultView || window;
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = win.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const clickElement = (el) => {
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    for (const type of ['mouseover', 'mousemove', 'mousedown', 'mouseup', 'click']) {
      el.dispatchEvent(new win.MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        view: win,
        clientX: x,
        clientY: y,
        button: 0,
        buttons: type === 'mousedown' ? 1 : 0
      }));
    }
    if (typeof el.click === 'function') el.click();
  };
  const findFilterRootVue = (start) => {
    let current = start;
    while (current) {
      if (current.__vue__?._data && Object.prototype.hasOwnProperty.call(current.__vue__._data, 'checkedFilters')) {
        return current.__vue__;
      }
      current = current.parentElement;
    }
    return Array.from(doc.querySelectorAll('.filter-wrap, .filter-panel, .filters-wrap'))
      .map((el) => el.__vue__)
      .find((vm) => vm?._data && Object.prototype.hasOwnProperty.call(vm._data, 'checkedFilters')) || null;
  };
  const panel = doc.querySelector('.filter-panel');
  if (!panel) return JSON.stringify({ clicked: false, panelClosed: true, reason: 'panel_not_open' });
  const exactButton = Array.from(panel.querySelectorAll('button, a, .btn, span, div'))
    .filter(visible)
    .filter((el) => ['\u786e\u5b9a', '\u786e\u8ba4', '\u5b8c\u6210'].includes(text(el)))
    .map((el) => {
      const rect = el.getBoundingClientRect();
      return { el, area: rect.width * rect.height, top: rect.top, left: rect.left };
    })
    .filter((item) => item.area > 100 && item.area < 20000)
    .sort((a, b) => (b.top - a.top) || (b.left - a.left) || (a.area - b.area))[0]?.el || null;
  if (exactButton) {
    clickElement(exactButton);
    await delay(900);
    if (!doc.querySelector('.filter-panel')) {
      return JSON.stringify({ clicked: true, method: 'dom_exact_button', panelClosed: true });
    }
  }
  const rootVm = findFilterRootVue(panel);
  if (rootVm?.confirm) {
    try {
      rootVm.confirm();
      await delay(1200);
      return JSON.stringify({
        clicked: true,
        method: 'vue_confirm',
        panelClosed: !doc.querySelector('.filter-panel'),
        checkedFilters: rootVm._data?.checkedFilters || {}
      });
    } catch (error) {
      return JSON.stringify({
        clicked: Boolean(exactButton),
        method: exactButton ? 'dom_exact_button' : 'none',
        panelClosed: !doc.querySelector('.filter-panel'),
        reason: String(error?.message || error),
        checkedFilters: rootVm._data?.checkedFilters || {}
      });
    }
  }
  return JSON.stringify({
    clicked: Boolean(exactButton),
    method: exactButton ? 'dom_exact_button' : 'none',
    panelClosed: !doc.querySelector('.filter-panel'),
    reason: exactButton ? '' : 'confirm_button_not_found'
  });
})()
""".strip()
        return self._evaluate_json(websocket_url, expression, timeout=10)

    def _recommend_age_slider_state(self, websocket_url: str) -> dict[str, Any]:
        expression = r"""
(() => {
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const frameRect = frame ? frame.getBoundingClientRect() : { left: 0, top: 0 };
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const age = doc.querySelector('.filter-item.age');
  const rail = age?.querySelector('.vue-slider-rail');
  const railRect = rail?.getBoundingClientRect();
  const dots = Array.from(age?.querySelectorAll('.vue-slider > .vue-slider-dot, .vue-slider-rail > .vue-slider-dot') || [])
    .slice(0, 2)
    .map((el) => {
      const rect = el.getBoundingClientRect();
      const label = text(el.querySelector('.vue-slider-dot-tooltip-text')) || text(el);
      const match = label.match(/\d{1,2}/);
      const ariaValueNow = el.getAttribute('aria-valuenow') || el.querySelector('[aria-valuenow]')?.getAttribute('aria-valuenow') || '';
      const ariaValueText = el.getAttribute('aria-valuetext') || el.querySelector('[aria-valuetext]')?.getAttribute('aria-valuetext') || '';
      const rawText = text(el);
      const tooltip = text(el.querySelector('.vue-slider-dot-tooltip-text'));
      const combined = `${label} ${ariaValueNow} ${ariaValueText} ${rawText} ${tooltip}`;
      const x = frameRect.left + rect.left + rect.width / 2;
      const railRight = railRect ? frameRect.left + railRect.right : 0;
      const nearRightEdge = railRight ? Math.abs(x - railRight) <= Math.max(6, rect.width + 2) : false;
      const isUnlimited = combined.includes('不限') || combined.includes('涓嶉檺') || (!match && nearRightEdge);
      return {
        x,
        y: frameRect.top + rect.top + rect.height / 2,
        label,
        value: match ? Number(match[0]) : null,
        isUnlimited,
        ariaValueNow,
        ariaValueText,
        text: rawText,
        tooltip
      };
    });
  return JSON.stringify({
    frameUrl: frame?.src || '',
    rail: railRect ? {
      x: frameRect.left + railRect.left,
      y: frameRect.top + railRect.top + railRect.height / 2,
      w: railRect.width,
      left: frameRect.left + railRect.left,
      right: frameRect.left + railRect.right,
      top: frameRect.top + railRect.top,
      bottom: frameRect.top + railRect.bottom
    } : null,
    dots
  });
})()
""".strip()
        return self._evaluate_json(websocket_url, expression)

    def _chat_job_filter_state(self, websocket_url: str) -> dict[str, Any]:
        expression = r"""
(() => {
  const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const viewport = { w: innerWidth, h: innerHeight };

  function isVisible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && rect.bottom >= 0
      && rect.right >= 0
      && rect.top <= viewport.h
      && rect.left <= viewport.w
      && style.display !== 'none'
      && style.visibility !== 'hidden'
      && Number(style.opacity || 1) > 0.05;
  }

  function center(rect) {
    return {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height
    };
  }

  const trigger = document.querySelector('.job-select .ui-dropmenu-label')
    || document.querySelector('.chat-job .ui-dropmenu-label')
    || document.querySelector('.dropmenu-label.chat-select-job')?.closest('.ui-dropmenu-label')
    || null;
  const selected = norm(
    document.querySelector('.dropmenu-label.chat-select-job')?.innerText
    || trigger?.innerText
    || ''
  );
  const openedRoot = Array.from(document.querySelectorAll('.job-select .ui-dropmenu, .chat-job .ui-dropmenu, .ui-dropmenu-visible'))
    .find((el) => isVisible(el) && el.classList.contains('ui-dropmenu-visible'));
  const optionNodes = Array.from(document.querySelectorAll(
    '.job-select .ui-dropmenu-list li, .chat-job .ui-dropmenu-list li, .ui-dropmenu-visible .ui-dropmenu-list li'
  ));
  const seen = new Set();
  const options = [];
  for (const el of optionNodes) {
    if (!isVisible(el)) continue;
    const text = norm(el.innerText || el.textContent || '');
    if (!text || seen.has(text)) continue;
    seen.add(text);
    const rect = el.getBoundingClientRect();
    options.push({ text, ...center(rect) });
  }

  return JSON.stringify({
    selected,
    opened: Boolean(openedRoot),
    trigger: trigger && isVisible(trigger) ? center(trigger.getBoundingClientRect()) : null,
    options
  });
})()
""".strip()
        return self._evaluate_json(websocket_url, expression)

    def _recommend_job_filter_state(self, websocket_url: str) -> dict[str, Any]:
        expression = r"""
(() => {
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const win = doc.defaultView || window;
  const frameRect = frame ? frame.getBoundingClientRect() : { left: 0, top: 0 };
  const norm = (value) => String(value || '').replace(/\s+/g, ' ').trim();

  function isVisible(el) {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = win.getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && rect.bottom >= 0
      && rect.right >= 0
      && style.display !== 'none'
      && style.visibility !== 'hidden'
      && Number(style.opacity || 1) > 0.05;
  }

  function center(el) {
    const rect = el.getBoundingClientRect();
    return {
      x: frameRect.left + rect.left + rect.width / 2,
      y: frameRect.top + rect.top + rect.height / 2,
      left: frameRect.left + rect.left,
      top: frameRect.top + rect.top,
      width: rect.width,
      height: rect.height
    };
  }

  const text = (el) => norm(el?.innerText || el?.textContent || '');
  const triggerSelectors = [
    '.job-select .ui-dropmenu-label',
    '.recommend-job-select .ui-dropmenu-label',
    '.filter-job-select .ui-dropmenu-label',
    '.ui-dropmenu-label',
    '[class*="job"] [class*="drop"]',
    '[class*="position"] [class*="drop"]'
  ];
  const triggers = triggerSelectors
    .flatMap((selector) => Array.from(doc.querySelectorAll(selector)))
    .filter((el) => isVisible(el) && text(el))
    .sort((a, b) => {
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return (br.top < 160 ? 1 : 0) - (ar.top < 160 ? 1 : 0) || ar.top - br.top;
    });
  const trigger = triggers[0] || null;
  const selected = text(doc.querySelector('.dropmenu-label.chat-select-job'))
    || text(trigger)
    || '';

  const optionNodes = Array.from(doc.querySelectorAll(
    '.ui-dropmenu-visible .ui-dropmenu-list li, .ui-dropmenu-visible li, .job-select li, .recommend-job-select li, [class*="dropmenu"] li'
  ));
  const seen = new Set();
  const options = [];
  for (const el of optionNodes) {
    if (!isVisible(el)) continue;
    const value = text(el);
    if (!value || seen.has(value)) continue;
    seen.add(value);
    options.push({ text: value, ...center(el) });
  }

  return JSON.stringify({
    selected,
    opened: options.length > 0,
    trigger: trigger && isVisible(trigger) ? center(trigger) : null,
    options,
    frameUrl: frame?.src || ''
  });
})()
""".strip()
        return self._evaluate_json(websocket_url, expression)

    def _recommend_results_signature(self, websocket_url: str) -> dict[str, Any]:
        expression = r"""
(() => {
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const win = doc.defaultView || window;
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const visible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = win.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const cards = Array.from(doc.querySelectorAll('li.card-item, [data-geekid], [data-geek-id], .geek-card, .recommend-card, .candidate-card'))
    .filter((el) => visible(el) && text(el).length > 20);
  const firstText = cards.slice(0, 5).map((el) => text(el).slice(0, 120)).join('|');
  return JSON.stringify({
    cardCount: cards.length,
    firstText,
    frameUrl: frame?.src || '',
    url: location.href,
    signature: [frame?.src || location.href, cards.length, firstText].join('::')
  });
})()
""".strip()
        return self._evaluate_json(websocket_url, expression)

    def _recommend_greet_button_point(self, websocket_url: str, name: str) -> dict[str, Any]:
        escaped_name = json.dumps(name, ensure_ascii=False)
        expression = f"""
(async () => {{
  const targetName = {escaped_name};
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame && frame.contentDocument ? frame.contentDocument : document;
  const frameRect = frame ? frame.getBoundingClientRect() : {{ left: 0, top: 0 }};
  const scroller = doc.scrollingElement || doc.body;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = doc.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }};
  const lines = (el) => text(el).split('\\n').map((line) => line.trim()).filter(Boolean);
  const hasTargetName = (el) => lines(el).includes(targetName);
  const statusFrom = (raw) => {{
    if (raw.includes('缁х画娌熼€?)) return '缁х画娌熼€?;
    if (raw.includes('宸叉墦鎷涘懠')) return '宸叉墦鎷涘懠';
    if (raw.includes('鎵撴嫑鍛?)) return '鎵撴嫑鍛?;
    return '';
  }};
  const candidateSelectors = [
    '.candidate-card-wrap',
    '.similar-geek-wrap .geek-card',
    '.card-inner',
    'li.card-item'
  ];
  const findCards = () => {{
    const seen = new Set();
    const cards = [];
    for (const selector of candidateSelectors) {{
      for (const el of Array.from(doc.querySelectorAll(selector))) {{
        if (!isVisible(el) || !hasTargetName(el) || seen.has(el)) continue;
        seen.add(el);
        cards.push(el);
      }}
    }}
    return cards.sort((a, b) => text(a).length - text(b).length);
  }};
  const findButton = (card) => Array.from(card.querySelectorAll('button.btn-greet, button, a, span, div'))
    .filter((el) => isVisible(el) && text(el) === '鎵撴嫑鍛?)
    .sort((a, b) => {{
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return (ar.width * ar.height) - (br.width * br.height);
    }})[0] || null;

  scroller.scrollTo(0, 0);
  await sleep(250);
  for (let step = 0; step < 18; step += 1) {{
    const cards = findCards();
    if (cards.length) {{
      let fallback = null;
      for (const card of cards) {{
        card.scrollIntoView({{ block: 'center', inline: 'center' }});
        await sleep(250);
        const raw = text(card);
        const status = statusFrom(raw);
        const button = findButton(card);
        fallback = fallback || {{ raw, status }};
        if (!button) continue;
        button.scrollIntoView({{ block: 'center', inline: 'center' }});
        await sleep(300);
        const rect = button.getBoundingClientRect();
        return JSON.stringify({{
          found: true,
          name: targetName,
          status,
          rawPreview: raw.slice(0, 240),
          button: {{
            x: frameRect.left + rect.left + rect.width / 2,
            y: frameRect.top + rect.top + rect.height / 2,
            left: frameRect.left + rect.left,
            top: frameRect.top + rect.top,
            width: rect.width,
            height: rect.height
          }}
        }});
      }}
      return JSON.stringify({{
        found: true,
        name: targetName,
        status: fallback?.status || '',
        button: null,
        rawPreview: (fallback?.raw || '').slice(0, 240)
      }});
    }}
    if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 5) break;
    scroller.scrollTo(0, Math.min(scroller.scrollTop + Math.floor(scroller.clientHeight * 0.85), scroller.scrollHeight));
    await sleep(400);
  }}
  return JSON.stringify({{ found: false, name: targetName, reason: 'candidate_not_found' }});
}})()
""".strip()
        return self._evaluate_json(websocket_url, expression, timeout=30)

    def _dispatch_mouse_click(self, websocket_url: str, x: float, y: float) -> None:
        ws = create_connection(websocket_url, timeout=10)
        try:
            def send_event(params: dict[str, Any]) -> None:
                self._message_id += 1
                ws.send(json.dumps({"id": self._message_id, "method": "Input.dispatchMouseEvent", "params": params}))
                time.sleep(0.05)

            for event_type, buttons in (("mouseMoved", 0), ("mousePressed", 1), ("mouseReleased", 0)):
                params: dict[str, Any] = {
                    "type": event_type,
                    "x": x,
                    "y": y,
                    "button": "left",
                    "clickCount": 1,
                }
                if buttons:
                    params["buttons"] = buttons
                send_event(params)
        finally:
            ws.close()

    def _dispatch_mouse_drag(self, websocket_url: str, start_x: float, start_y: float, end_x: float, end_y: float) -> None:
        ws = create_connection(websocket_url, timeout=10)
        try:
            def send_event(params: dict[str, Any]) -> None:
                self._message_id += 1
                ws.send(json.dumps({"id": self._message_id, "method": "Input.dispatchMouseEvent", "params": params}))
                time.sleep(0.05)

            send_event({"type": "mouseMoved", "x": start_x, "y": start_y})
            send_event({"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "buttons": 1, "clickCount": 1})
            for step in range(1, 9):
                x = start_x + ((end_x - start_x) * step / 8)
                y = start_y + ((end_y - start_y) * step / 8)
                send_event({"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1})
            send_event({"type": "mouseReleased", "x": end_x, "y": end_y, "button": "left", "clickCount": 1})
        finally:
            ws.close()

    def _dispatch_mouse_drag_precise(self, websocket_url: str, start_x: float, start_y: float, end_x: float, end_y: float) -> None:
        ws = create_connection(websocket_url, timeout=10)
        try:
            def send_event(params: dict[str, Any], delay_seconds: float = 0.035) -> None:
                self._message_id += 1
                ws.send(json.dumps({"id": self._message_id, "method": "Input.dispatchMouseEvent", "params": params}))
                time.sleep(delay_seconds)

            send_event({"type": "mouseMoved", "x": start_x, "y": start_y})
            send_event({"type": "mousePressed", "x": start_x, "y": start_y, "button": "left", "buttons": 1, "clickCount": 1}, 0.08)
            for step in range(1, 17):
                ratio = step / 16
                x = start_x + ((end_x - start_x) * ratio)
                y = start_y + ((end_y - start_y) * ratio)
                send_event({"type": "mouseMoved", "x": x, "y": y, "button": "left", "buttons": 1})
            send_event({"type": "mouseReleased", "x": end_x, "y": end_y, "button": "left", "buttons": 0, "clickCount": 1}, 0.08)
        finally:
            ws.close()

    def _dispatch_escape_key(self, websocket_url: str) -> None:
        ws = create_connection(websocket_url, timeout=10)
        try:
            for event_type in ("keyDown", "keyUp"):
                self._message_id += 1
                ws.send(
                    json.dumps(
                        {
                            "id": self._message_id,
                            "method": "Input.dispatchKeyEvent",
                            "params": {
                                "type": event_type,
                                "key": "Escape",
                                "code": "Escape",
                                "windowsVirtualKeyCode": 27,
                                "nativeVirtualKeyCode": 27,
                            },
                        }
                    )
                )
                time.sleep(0.05)
        finally:
            ws.close()

    def capture_recommend_candidates(
        self,
        max_count: int = 20,
        url_contains: str | None = "/web/chat/recommend",
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            payload = json.loads((self.mock_dir / "recommend_candidates.json").read_text(encoding="utf-8"))
            payload["candidates"] = payload.get("candidates", [])[:max_count]
            payload["count"] = len(payload["candidates"])
            return payload

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        expression = build_capture_recommend_candidates_expression_v2(max_count=max_count)
        return self._evaluate_json(websocket_url, expression)

    def wait_for_recommend_results_stable(
        self,
        url_contains: str | None = "/web/chat/recommend",
        timeout_seconds: float = 20,
        stable_seconds: float = 1.5,
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            payload = self.capture_recommend_candidates(max_count=20, url_contains=url_contains)
            return {
                "stable": True,
                "elapsedSeconds": 0,
                "cardCount": payload.get("count", 0),
                "signature": "mock",
                "mock": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        start = time.time()
        stable_since: float | None = None
        previous_signature = ""
        latest: dict[str, Any] = {}
        while time.time() - start <= timeout_seconds:
            latest = self._recommend_results_signature(websocket_url)
            signature = str(latest.get("signature") or "")
            if signature and signature == previous_signature:
                if stable_since is None:
                    stable_since = time.time()
                if time.time() - stable_since >= stable_seconds:
                    latest["stable"] = True
                    latest["elapsedSeconds"] = round(time.time() - start, 2)
                    return latest
            else:
                stable_since = None
                previous_signature = signature
            time.sleep(0.5)

        latest["stable"] = False
        latest["elapsedSeconds"] = round(time.time() - start, 2)
        latest.setdefault("reason", "timeout")
        return latest

    def capture_recommend_resume_cards(
        self,
        max_scrolls: int = 12,
        url_contains: str | None = "/web/chat/recommend",
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            payload = json.loads((self.mock_dir / "recommend_candidates.json").read_text(encoding="utf-8"))
            cards = []
            for item in payload.get("candidates", []):
                raw_text = item.get("detailTextPreview", "")
                cards.append(
                    {
                        "name": item.get("candidateName", ""),
                        "summary": " ".join(
                            part
                            for part in (item.get("experience", ""), item.get("education", ""))
                            if part
                        ),
                        "rawText": raw_text,
                    }
                )
            return {"count": len(cards), "cards": cards, "mock": True}

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        return self._evaluate_json(
            websocket_url,
            build_capture_recommend_resume_cards_expression(max_scrolls=max_scrolls),
            timeout=max(10, max_scrolls),
        )

    def open_recommend_candidate(
        self,
        candidate_id: str | None = None,
        card_index: int | None = None,
        url_contains: str | None = "/web/chat/recommend",
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            self._mock_current_candidate_id = candidate_id or f"candidate-{card_index or 0}"
            return {
                "opened": True,
                "candidateId": candidate_id or f"candidate-{card_index or 0}",
                "cardIndex": card_index or 0,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        escaped_candidate_id = json.dumps(candidate_id or "", ensure_ascii=False)
        index = 0 if card_index is None else card_index
        expression = f"""
(async () => {{
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame && frame.contentDocument ? frame.contentDocument : document;
  const win = doc.defaultView || window;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = win.getComputedStyle(el);
    const viewportHeight = win.innerHeight || doc.documentElement.clientHeight || 0;
    const viewportWidth = win.innerWidth || doc.documentElement.clientWidth || 0;
    return rect.width > 0
      && rect.height > 0
      && rect.bottom >= -200
      && rect.top <= viewportHeight + 2000
      && rect.right >= 0
      && rect.left <= viewportWidth
      && style.display !== 'none'
      && style.visibility !== 'hidden';
  }};
  const isSimilarRecommendItem = (el) => {{
    const item = el?.matches?.('li.card-item') ? el : el?.closest?.('li.card-item');
    if (!item) return false;
    const titleText = text(item.querySelector('div.title, .title'));
    const raw = text(item);
    const hasSimilarTitle = (titleText.includes('\u4e3a\u4f60\u63a8\u8350') && titleText.includes('\u76f8\u4f3c'))
      || (raw.includes('\u4e3a\u4f60\u63a8\u8350') && raw.includes('\u76f8\u4f3c'));
    return hasSimilarTitle && item.querySelectorAll('div.geek-card, .geek-card').length > 0;
  }};
  const isInSimilarRecommendItem = (el) => {{
    const item = el?.closest?.('li.card-item');
    return Boolean(item && isSimilarRecommendItem(item));
  }};
  const isMainCandidateCard = (el) => {{
    const raw = text(el);
    if (raw.length <= 20) return false;
    if (isSimilarRecommendItem(el) || isInSimilarRecommendItem(el)) return false;
    const titleText = text(el.querySelector?.('div.title, .title'));
    return !(titleText.includes('\u4e3a\u4f60\u63a8\u8350') && titleText.includes('\u76f8\u4f3c'));
  }};
  const primaryCards = Array.from(doc.querySelectorAll('li.card-item'))
    .filter((el) => isVisible(el) && isMainCandidateCard(el));
  const cards = (primaryCards.length ? primaryCards : Array.from(doc.querySelectorAll('[data-geekid], [data-geek-id], .geek-card, .recommend-card, .candidate-card')))
    .filter((el) => isVisible(el) && isMainCandidateCard(el))
    .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  const target = cards.find((el) => {{
    const id = el.getAttribute('data-geekid') || el.getAttribute('data-geek-id') || el.getAttribute('data-id') || '';
    return {escaped_candidate_id} && id === {escaped_candidate_id};
  }}) || cards[{index}];
  if (!target) {{
    return JSON.stringify({{ opened: false, reason: 'not_found', cardIndex: {index} }});
  }}
  target.scrollIntoView({{ block: 'center', inline: 'center' }});
  await sleep(250);
  const clickable = target.querySelector('.card-inner, .geek-info, .info-primary, .name, [class*="content"]') || target;
  clickable.click();
  await sleep(900);
  const frameRect = frame ? frame.getBoundingClientRect() : {{ left: 0, top: 0 }};
  const rect = target.getBoundingClientRect();
  const point = {{
    x: frameRect.left + rect.left + Math.min(Math.max(40, rect.width * 0.35), rect.width - 20),
    y: frameRect.top + rect.top + Math.min(Math.max(40, rect.height * 0.5), rect.height - 20),
    left: frameRect.left + rect.left,
    top: frameRect.top + rect.top,
    width: rect.width,
    height: rect.height
  }};
  return JSON.stringify({{
    opened: true,
    domClicked: true,
    candidateId: target.getAttribute('data-geekid') || target.getAttribute('data-geek-id') || target.getAttribute('data-id') || '',
    cardIndex: cards.indexOf(target),
    point
  }});
}})()
""".strip()
        payload = self._evaluate_json(websocket_url, expression, timeout=10)
        if payload.get("domClicked"):
            detail = self._evaluate_json(websocket_url, build_recommend_detail_open_state_expression_v2(), timeout=10)
            payload["detailOpen"] = detail.get("detailOpen", False)
            payload["detailState"] = detail
            payload["opened"] = bool(detail.get("detailOpen"))
            if payload["opened"]:
                return payload
        point = payload.get("point")
        if point:
            self._dispatch_mouse_click(websocket_url, point["x"], point["y"])
            time.sleep(1.2)
            detail = self._evaluate_json(websocket_url, build_recommend_detail_open_state_expression_v2(), timeout=10)
            payload["detailOpen"] = detail.get("detailOpen", False)
            payload["detailState"] = detail
            payload["opened"] = bool(detail.get("detailOpen"))
        return payload

    def capture_candidate_resume(
        self,
        url_contains: str | None = "/web/chat/recommend",
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            if self._mock_current_candidate_id:
                candidates = json.loads((self.mock_dir / "recommend_candidates.json").read_text(encoding="utf-8"))
                matched = next(
                    (
                        item for item in candidates.get("candidates", [])
                        if str(item.get("candidateId") or "") == self._mock_current_candidate_id
                    ),
                    None,
                )
                if matched is not None:
                    return {
                        "name": matched.get("candidateName", ""),
                        "candidateName": matched.get("candidateName", ""),
                        "expectedTitle": matched.get("expectedTitle", ""),
                        "expectedCity": matched.get("expectedCity", ""),
                        "yearsOfExperience": matched.get("experience", ""),
                        "education": matched.get("education", ""),
                        "skills": matched.get("tags", []),
                        "workHighlights": [matched.get("detailTextPreview", "")],
                        "rawTextPreview": matched.get("detailTextPreview", ""),
                        "sourceUrl": "mock",
                        "detailOpen": True,
                    }
            payload = json.loads((self.mock_dir / "candidate_resume.json").read_text(encoding="utf-8"))
            payload.setdefault("detailOpen", True)
            return payload

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        deadline = time.time() + 16
        latest: dict[str, Any] = {}
        while time.time() < deadline:
            latest = self._evaluate_json(websocket_url, build_capture_recommend_detail_resume_expression_v3())
            preview = str(latest.get("rawTextPreview") or "")
            if latest.get("detailOpen"):
                canvas_ready = self._wait_recommend_resume_canvas_ready(websocket_url, timeout_seconds=8)
                latest["canvasReady"] = canvas_ready
                if canvas_ready.get("ready"):
                    latest = self._augment_resume_with_canvas_ocr(websocket_url, latest)
                    return latest
                if self._resume_detail_preview_ready(preview):
                    return latest
            time.sleep(0.5)
        if latest.get("detailOpen"):
            latest["canvasReady"] = self._wait_recommend_resume_canvas_ready(websocket_url, timeout_seconds=3)
            if latest["canvasReady"].get("ready"):
                latest = self._augment_resume_with_canvas_ocr(websocket_url, latest)
            elif self._resume_detail_preview_ready(str(latest.get("rawTextPreview") or "")):
                latest.setdefault("captureStatus", "preview_only_canvas_not_ready")
            else:
                latest["captureStatus"] = "canvas_not_ready"
                latest.setdefault("ocrStatus", "skipped_canvas_not_ready")
        return latest

    @staticmethod
    def _resume_detail_preview_ready(preview: str) -> bool:
        raw = str(preview or "").strip()
        if not raw:
            return False
        loading_markers = ("\u6b63\u5728\u52a0\u8f7d", "姝ｅ湪鍔犺浇", "loading")
        action_lines = {
            "\u6536\u85cf",
            "\u8f6c\u53d1",
            "\u4e3e\u62a5",
            "\u4e0d\u5408\u9002",
            "\u6253\u62db\u547c",
            "\u7ee7\u7eed\u6c9f\u901a",
        }
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if any(any(marker.lower() in line.lower() for marker in loading_markers) for line in lines):
            return False
        useful_lines = [
            line
            for line in lines
            if line not in action_lines and not any(marker.lower() in line.lower() for marker in loading_markers)
        ]
        useful_text = "\n".join(useful_lines).strip()
        if len(useful_text) < 80 or len(useful_lines) < 4:
            return False
        resume_markers = (
            "\u671f\u671b",
            "\u4f18\u52bf",
            "\u7ecf\u5386\u6982\u89c8",
            "\u5de5\u4f5c\u7ecf\u5386",
            "\u6559\u80b2\u7ecf\u5386",
            "\u8fd0\u8425",
            "\u672c\u79d1",
            "\u5927\u4e13",
        )
        return any(marker in useful_text for marker in resume_markers)

    def _wait_recommend_resume_canvas_ready(self, websocket_url: str, timeout_seconds: float = 8) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        last: dict[str, Any] = {}
        stable_hits = 0
        previous_signature = ""
        while time.time() < deadline:
            try:
                last = self._evaluate_json(
                    websocket_url,
                    build_recommend_resume_canvas_ready_expression(),
                    timeout=5,
                )
            except Exception as exc:
                last = {"ready": False, "error": str(exc)}
            signature = str(last.get("signature") or "")
            if last.get("ready") and signature and signature == previous_signature:
                stable_hits += 1
            else:
                stable_hits = 0
            if last.get("ready") and stable_hits >= 1:
                return {**last, "stable": True}
            previous_signature = signature
            time.sleep(0.45)
        return {**last, "stable": False, "timeout": True}

    def _augment_resume_with_canvas_ocr(self, websocket_url: str, resume: dict[str, Any]) -> dict[str, Any]:
        if not resume.get("detailOpen"):
            return resume
        ocr = self._capture_recommend_canvas_ocr(websocket_url)
        resume = {**resume, "ocr": ocr, "ocrStatus": ocr.get("status", "")}
        text = str(ocr.get("text") or "").strip()
        if not text:
            return resume
        raw = str(resume.get("rawTextPreview") or "").strip()
        merged = "\n\n".join(part for part in [raw, "OCR绠€鍘嗗叏鏂嘰n" + text] if part)
        return {
            **resume,
            "ocrText": text,
            "ocrSections": ocr.get("sections", {}),
            "rawTextPreview": merged[:12000],
            "resumeSource": ocr.get("resumeSource") or "detail+canvas_ocr",
        }

    def _capture_recommend_canvas_ocr(self, websocket_url: str) -> dict[str, Any]:
        tesseract = self._find_tesseract()
        if not tesseract:
            return {"status": "ocr_tesseract_not_found"}

        try:
            scrolled = self._evaluate_json(
                websocket_url,
                build_capture_recommend_canvas_scrolled_data_urls_expression(max_segments=6),
                timeout=15,
            )
        except Exception as exc:
            scrolled = {"found": False, "error": str(exc), "reason": "scrolled_capture_failed"}

        segments = [item for item in scrolled.get("segments", []) if isinstance(item, dict)]
        if scrolled.get("found") and segments:
            ocr_segments = []
            texts = []
            errors = []
            for segment in segments:
                segment_result = self._ocr_canvas_data_url_segment(segment, tesseract=tesseract)
                ocr_segments.append(segment_result)
                segment_text = str(segment_result.get("text") or "").strip()
                if segment_text:
                    texts.append(segment_text)
                if segment_result.get("status") != "ok":
                    errors.append(segment_result)
            merged_text = self._merge_ocr_segment_texts(texts)
            if merged_text:
                status = "ok" if not errors else "partial"
                return {
                    "status": status,
                    "text": merged_text,
                    "sections": self._extract_ocr_sections(merged_text),
                    "segments": ocr_segments,
                    "segmentCount": len(ocr_segments),
                    "successfulSegmentCount": len([item for item in ocr_segments if item.get("status") == "ok"]),
                    "scrollContainer": scrolled.get("scrollContainer", {}),
                    "resumeSource": "detail+canvas_ocr_scrolled",
                    "tesseract": tesseract,
                }

        fallback = self._capture_recommend_canvas_ocr_single(websocket_url, tesseract=tesseract)
        if scrolled.get("reason") or scrolled.get("error"):
            fallback["scrolledFallbackReason"] = scrolled.get("reason") or scrolled.get("error")
        return fallback

    def _capture_recommend_canvas_ocr_single(self, websocket_url: str, tesseract: str | None = None) -> dict[str, Any]:
        try:
            canvas = self._evaluate_json(websocket_url, build_capture_recommend_canvas_data_url_expression(), timeout=10)
        except Exception as exc:
            return {"status": "ocr_canvas_capture_failed", "error": str(exc)}
        if not canvas.get("found"):
            return {"status": "ocr_canvas_not_found"}
        if not canvas.get("readable", True):
            return {"status": "ocr_canvas_unreadable", "error": str(canvas.get("error") or "")}
        data_url = str(canvas.get("dataUrl") or "")
        if "," not in data_url:
            return {"status": "ocr_canvas_empty"}

        tesseract = tesseract or self._find_tesseract()
        if not tesseract:
            return {"status": "ocr_tesseract_not_found"}

        result = self._ocr_canvas_data_url_segment(canvas, tesseract=tesseract)
        if result.get("status") != "ok":
            return {
                **result,
                "tesseract": tesseract,
            }
        text = str(result.get("text") or "")
        return {
            "status": "ok",
            "text": text,
            "sections": self._extract_ocr_sections(text),
            "stderr": result.get("stderr", ""),
            "tesseract": tesseract,
            "canvas": {key: canvas.get(key) for key in ("width", "height", "cssWidth", "cssHeight")},
            "resumeSource": "detail+canvas_ocr",
        }

    def _ocr_canvas_data_url_segment(self, canvas: dict[str, Any], tesseract: str) -> dict[str, Any]:
        data_url = str(canvas.get("dataUrl") or "")
        if "," not in data_url:
            return {
                "status": "ocr_canvas_empty",
                "scrollTop": canvas.get("scrollTop"),
                "canvas": {key: canvas.get(key) for key in ("width", "height", "cssWidth", "cssHeight")},
            }
        try:
            image_bytes = base64.b64decode(data_url.split(",", 1)[1])
            completed = subprocess.run(
                [tesseract, "stdin", "stdout", "-l", "chi_sim+eng", "--psm", "6"],
                input=image_bytes,
                capture_output=True,
                timeout=60,
            )
        except Exception as exc:
            return {
                "status": "ocr_failed",
                "error": str(exc),
                "scrollTop": canvas.get("scrollTop"),
                "canvas": {key: canvas.get(key) for key in ("width", "height", "cssWidth", "cssHeight")},
            }

        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        text = self._clean_ocr_text(stdout)
        if completed.returncode != 0:
            return {
                "status": "ocr_failed",
                "returnCode": completed.returncode,
                "error": stderr[-1000:],
                "scrollTop": canvas.get("scrollTop"),
                "canvas": {key: canvas.get(key) for key in ("width", "height", "cssWidth", "cssHeight")},
            }
        if not text:
            return {
                "status": "ocr_empty",
                "stderr": stderr[-1000:],
                "scrollTop": canvas.get("scrollTop"),
                "canvas": {key: canvas.get(key) for key in ("width", "height", "cssWidth", "cssHeight")},
            }
        return {
            "status": "ok",
            "text": text,
            "stderr": stderr[-1000:],
            "scrollTop": canvas.get("scrollTop"),
            "canvas": {key: canvas.get(key) for key in ("width", "height", "cssWidth", "cssHeight")},
        }

    def _find_tesseract(self) -> str:
        default_path = Path(r"D:\tesseract-ocr\tesseract.EXE")
        if default_path.exists():
            return str(default_path)
        return shutil.which("tesseract") or ""

    @staticmethod
    def _clean_ocr_text(value: str) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        for _ in range(4):
            text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
        text = re.sub(r"(?<=[\u4e00-\u9fff])\s+([锛屻€傦紱锛氥€侊級])", r"\1", text)
        text = re.sub(r"([锛圿)\s+(?=[\u4e00-\u9fff])", r"\1", text)
        text = re.sub(r"[ \t]+", " ", text)
        lines = [line.strip() for line in text.split("\n")]
        return "\n".join(line for line in lines if line).strip()

    @staticmethod
    def _merge_ocr_segment_texts(values: list[str]) -> str:
        merged: list[str] = []
        seen: set[str] = set()
        repeated_headings = {
            "\u5de5\u4f5c\u7ecf\u5386",
            "\u9879\u76ee\u7ecf\u5386",
            "\u6559\u80b2\u7ecf\u5386",
            "\u6280\u80fd\u8bc1\u4e66",
            "\u4e2a\u4eba\u4f18\u52bf",
            "\u6c42\u804c\u671f\u671b",
        }
        punctuation_pattern = r"[\u3001\u3002\uff0c\uff1b\uff1a\uff08\uff09\u3010\u3011\uff5c\uff0f,.;:|/\\\-—_()\[\]\"'“”‘’]+"
        for value in values:
            for raw_line in str(value or "").split("\n"):
                line = raw_line.strip()
                if not line:
                    continue
                comparable = re.sub(r"\s+", "", line).lower()
                comparable = re.sub(punctuation_pattern, "", comparable)
                if not comparable:
                    continue
                if comparable in seen:
                    continue
                if line in repeated_headings and line in merged:
                    continue
                seen.add(comparable)
                merged.append(line)
        return "\n".join(merged).strip()

    @staticmethod
    def _extract_ocr_sections(text: str) -> dict[str, str]:
        headings = [
            "\u5de5\u4f5c\u7ecf\u5386",
            "\u9879\u76ee\u7ecf\u5386",
            "\u6559\u80b2\u7ecf\u5386",
            "\u6280\u80fd\u8bc1\u4e66",
            "\u4e2a\u4eba\u4f18\u52bf",
            "\u6c42\u804c\u671f\u671b",
        ]
        positions = []
        for heading in headings:
            match = re.search(re.escape(heading), text)
            if match:
                positions.append((match.start(), heading))
        positions.sort()
        sections: dict[str, str] = {}
        for index, (start, heading) in enumerate(positions):
            end = positions[index + 1][0] if index + 1 < len(positions) else len(text)
            sections[heading] = text[start:end].strip()
        return sections

    def dismiss_recommend_popup_cards(
        self,
        url_contains: str | None = "/web/chat/recommend",
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {"skipped": False, "dismissed": False, "method": "mock", "reason": "mock_noop"}

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        payload = self._evaluate_json(
            websocket_url,
            build_dismiss_recommend_popup_cards_expression(),
            timeout=8,
        )
        time.sleep(0.4)
        # double-check: run again to catch any remaining embedded recommendation blocks
        if payload.get("skipped") or payload.get("dismissed"):
            time.sleep(0.4)
            self._evaluate_json(
                websocket_url,
                build_dismiss_recommend_popup_cards_expression(),
                timeout=8,
            )
        return payload

    def click_recommend_detail_greet(
        self,
        url_contains: str | None = "/web/chat/recommend",
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "greeted": True,
                "clicked": True,
                "status": "mock_greeted",
                "mock": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        # Dismiss any popup cards before attempting greet
        pre_dismiss = self.dismiss_recommend_popup_cards(url_contains=url_contains)
        time.sleep(0.3)

        payload = self._evaluate_json(websocket_url, build_click_recommend_detail_greet_expression_v2(), timeout=10)
        point = payload.get("button")
        if payload.get("clicked") and (not point or "x" not in point or "y" not in point):
            time.sleep(1.5)
            # Dismiss popups that may have appeared after the click
            post_dismiss = self.dismiss_recommend_popup_cards(url_contains=url_contains)
            verified = self._evaluate_json(websocket_url, build_recommend_detail_greet_state_expression_v2(), timeout=10)
            return {
                "greeted": bool(verified.get("greeted")),
                "clicked": True,
                "status": verified.get("status", ""),
                "before": payload,
                "after": verified,
                "preDismiss": pre_dismiss,
                "postDismiss": post_dismiss,
            }
        if not point:
            return {**payload, "preDismiss": pre_dismiss}
        self._dispatch_mouse_click(websocket_url, point["x"], point["y"])
        time.sleep(1.5)
        # Dismiss popups that may have appeared after click
        post_dismiss = self.dismiss_recommend_popup_cards(url_contains=url_contains)
        time.sleep(0.3)
        verified = self._evaluate_json(websocket_url, build_recommend_detail_greet_state_expression_v2(), timeout=10)
        return {
            "greeted": bool(verified.get("greeted")),
            "clicked": True,
            "status": verified.get("status", ""),
            "before": payload,
            "after": verified,
            "preDismiss": pre_dismiss,
            "postDismiss": post_dismiss,
        }

    def click_recommend_card_greet(
        self,
        candidate_id: str | None = None,
        card_index: int | None = None,
        url_contains: str | None = "/web/chat/recommend",
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {"greeted": True, "clicked": True, "mock": True}

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        # Dismiss popup cards before attempting greet
        pre_dismiss = self.dismiss_recommend_popup_cards(url_contains=url_contains)
        time.sleep(0.3)

        payload = self._evaluate_json(
            websocket_url,
            build_click_recommend_card_greet_expression(
                escaped_candidate_id=json.dumps(candidate_id or "", ensure_ascii=False),
                card_index=0 if card_index is None else card_index,
            ),
            timeout=10,
        )
        point = payload.get("button") or {}
        if payload.get("clicked") and point.get("x") is not None and point.get("y") is not None:
            self._dispatch_mouse_click(websocket_url, point["x"], point["y"])
            time.sleep(1.5)
            # Dismiss popups that may appear after clicking greet
            post_dismiss = self.dismiss_recommend_popup_cards(url_contains=url_contains)
            time.sleep(0.3)
            verified = self._evaluate_json(
                websocket_url,
                build_capture_recommend_candidates_expression_v2(max_count=max((card_index or 0) + 1, 1)),
                timeout=10,
            )
            status = ""
            for item in verified.get("candidates", []):
                if int(item.get("cardIndex") or 0) == int(card_index or 0):
                    status = str(item.get("greetStatus") or "")
                    break
            # Also verify by checking if card greet button text changed
            btn_verified = self._evaluate_json(
                websocket_url,
                build_recommend_detail_greet_state_expression_v2(),
                timeout=10,
            )
            return {
                **payload,
                "afterStatus": status,
                "greeted": (
                    self._is_recommend_greeted_status(status)
                    or bool(btn_verified.get("greeted"))
                ),
                "preDismiss": pre_dismiss,
                "postDismiss": post_dismiss,
                "buttonVerify": btn_verified,
            }
        return {**payload, "preDismiss": pre_dismiss}

    @staticmethod
    def _is_recommend_greeted_status(status: str) -> bool:
        value = str(status or "").strip()
        if not value:
            return False
        # Labels that indicate greeting was NOT sent yet
        pending_labels = [
            "\u6253\u62db\u547c",      # 打招呼
            "\u7acb\u5373\u6c9f\u901a",  # 立即沟通
            "\u8054\u7cfb",            # 联系
        ]
        # Labels that indicate greeting WAS sent
        greeted_labels = [
            "\u5df2\u6253\u62db\u547c",  # 已打招呼
            "\u7ee7\u7eed\u6c9f\u901a",  # 继续沟通
            "\u5df2\u6c9f\u901a",       # 已沟通
            "\u6c9f\u901a\u4e2d",       # 沟通中
            "\u5df2\u8054\u7cfb",       # 已联系
        ]
        if any(label in value for label in greeted_labels):
            return True
        return not any(label and label in value for label in pending_labels)

    def close_recommend_detail(self, url_contains: str | None = "/web/chat/recommend") -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "closed": True,
                "selector": ".boss-popup__close",
                "mock": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        before = self._evaluate_json(websocket_url, build_recommend_detail_open_state_expression_v2(), timeout=10)
        if not before.get("detailOpen"):
            return {"closed": True, "alreadyClosed": True, "before": before}

        self._dispatch_escape_key(websocket_url)
        after: dict[str, Any] = {}
        deadline = time.time() + 2.0
        while time.time() < deadline:
            time.sleep(0.3)
            after = self._evaluate_json(websocket_url, build_recommend_detail_open_state_expression_v2(), timeout=10)
            if not after.get("detailOpen"):
                return {"closed": True, "verifiedClosed": True, "escapeFirst": True, "before": before, "after": after}

        payload = self._evaluate_json(websocket_url, build_close_recommend_detail_expression_v2(), timeout=10)
        if payload.get("closed") and payload.get("method") == "dom_click":
            deadline = time.time() + 3.0
            while time.time() < deadline:
                time.sleep(0.3)
                after = self._evaluate_json(websocket_url, build_recommend_detail_open_state_expression_v2(), timeout=10)
                if not after.get("detailOpen"):
                    return {**payload, "verifiedClosed": True, "before": before, "after": after}
            self._dispatch_escape_key(websocket_url)
            deadline = time.time() + 3.0
            while time.time() < deadline:
                time.sleep(0.3)
                after = self._evaluate_json(websocket_url, build_recommend_detail_open_state_expression_v2(), timeout=10)
                if not after.get("detailOpen"):
                    return {**payload, "verifiedClosed": True, "escapeFallback": True, "before": before, "after": after}
            return {**payload, "verifiedClosed": False, "before": before, "after": after}
        point = payload.get("button")
        if not point:
            return {**payload, "before": before}
        return {**payload, "verifiedClosed": False, "before": before, "after": after}

    def greet_candidate(
        self,
        message: str,
        send: bool = False,
        url_contains: str | None = "/web/chat/recommend",
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "prepared": bool(message.strip()),
                "sent": bool(send and message.strip()),
                "body": message,
                "mock": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        escaped_message = json.dumps(message, ensure_ascii=False)
        escaped_send = "true" if send else "false"
        expression = f"""
(() => {{
  const text = (el) => el ? (el.innerText || '').trim() : '';
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }};
  const contactButton = Array.from(document.querySelectorAll('button, a, .btn'))
    .find((el) => isVisible(el) && /鎵撴嫑鍛紎绔嬪嵆娌熼€殀娌熼€殀鑱旂郴/.test(text(el)));
  if (!contactButton) {{
    return JSON.stringify({{ prepared: false, sent: false, reason: 'contact_button_not_found', body: {escaped_message} }});
  }}
  contactButton.click();
  const input = Array.from(document.querySelectorAll('textarea, [contenteditable="true"], .chat-input, .boss-chat-editor-input'))
    .find((el) => isVisible(el));
  if (!input) {{
    return JSON.stringify({{ prepared: false, sent: false, reason: 'input_not_found', body: {escaped_message} }});
  }}
  input.focus();
  if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {{
    input.value = {escaped_message};
  }} else {{
    input.innerHTML = '';
    input.textContent = {escaped_message};
  }}
  input.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: {escaped_message} }}));
  if (!{escaped_send}) {{
    return JSON.stringify({{ prepared: true, sent: false, body: {escaped_message} }});
  }}
  const sendButton = Array.from(document.querySelectorAll('button, a, .btn'))
    .find((el) => isVisible(el) && /鍙戦€亅纭畾|鎵撴嫑鍛紎绔嬪嵆娌熼€?.test(text(el)));
  if (!sendButton) {{
    return JSON.stringify({{ prepared: true, sent: false, reason: 'send_button_not_found', body: {escaped_message} }});
  }}
  sendButton.click();
  return JSON.stringify({{ prepared: true, sent: true, body: {escaped_message} }});
}})()
""".strip()
        return self._evaluate_json(websocket_url, expression)

    def greet_recommend_candidates_by_name(
        self,
        names: list[str],
        url_contains: str | None = "/web/chat/recommend",
    ) -> dict[str, Any]:
        if self.mock_dir is not None:
            return {
                "results": [
                    {"name": name, "greeted": True, "status": "mock_greeted"}
                    for name in names
                ],
                "mock": True,
            }

        pages = self.list_pages()
        page = self._select_page(pages, url_contains=url_contains)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            raise ValueError("Selected page does not have webSocketDebuggerUrl")

        results: list[dict[str, Any]] = []
        for name in names:
            point = self._recommend_greet_button_point(websocket_url, name)
            if not point.get("found"):
                results.append({"name": name, "greeted": False, "reason": point.get("reason", "not_found")})
                continue
            if point.get("status") and self._is_recommend_greeted_status(point.get("status", "")):
                results.append({"name": name, "greeted": True, "status": point.get("status"), "alreadyGreeted": True})
                continue
            if not point.get("button"):
                results.append({"name": name, "greeted": False, "reason": "button_not_found", "status": point.get("status", "")})
                continue
            button = point["button"]
            self._dispatch_mouse_click(websocket_url, button["x"], button["y"])
            time.sleep(1.0)
            verified = self._recommend_greet_button_point(websocket_url, name)
            status = verified.get("status", "")
            results.append(
                {
                    "name": name,
                    "greeted": self._is_recommend_greeted_status(status),
                    "status": status,
                    "clicked": True,
                    "button": button,
                }
            )
        return {"results": results}

    def _get_json(self, path: str, fixture_name: str) -> Any:
        if self.mock_dir is not None:
            return json.loads((self.mock_dir / fixture_name).read_text(encoding="utf-8"))

        with urlopen(urljoin(self.endpoint + "/", path.lstrip("/")), timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def _select_page(
        self,
        pages: list[dict[str, Any]],
        url_contains: str | None = None,
    ) -> dict[str, Any]:
        if url_contains:
            for page in pages:
                if url_contains in page.get("url", ""):
                    return page
        if not pages:
            raise ValueError("No pages returned from Chrome debugging endpoint")
        return pages[0]

    def _send_cdp_command(self, ws: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._message_id += 1
        ws.send(json.dumps({"id": self._message_id, "method": method, "params": params}))
        while True:
            message = json.loads(ws.recv())
            if message.get("id") == self._message_id:
                return message

    def _evaluate_json(self, websocket_url: str, expression: str, timeout: float = 5) -> dict[str, Any]:
        ws = create_connection(websocket_url, timeout=timeout)
        try:
            response = self._send_cdp_command(
                ws,
                method="Runtime.evaluate",
                params={"expression": expression, "returnByValue": True, "awaitPromise": True},
            )
        finally:
            ws.close()

        runtime_result = response.get("result", {}).get("result", {})
        if "exceptionDetails" in response.get("result", {}) or "value" not in runtime_result:
            raise RuntimeError(f"Chrome Runtime.evaluate failed: {response}")
        result = runtime_result["value"]
        if isinstance(result, dict):
            return result
        return json.loads(result)


def build_chrome_launch_command(
    chrome_path: Path,
    user_data_dir: Path,
    port: int,
) -> list[str]:
    return [
        str(chrome_path),
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
    ]


def launch_chrome(
    chrome_path: Path,
    user_data_dir: Path,
    port: int,
) -> list[str]:
    command = build_chrome_launch_command(
        chrome_path=chrome_path,
        user_data_dir=user_data_dir,
        port=port,
    )
    user_data_dir.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(command)
    return command


def build_download_click_probe_expression() -> str:
    return """
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && style.visibility !== 'hidden'
      && style.display !== 'none'
      && style.pointerEvents !== 'none';
  };

  const useNode = Array.from(document.querySelectorAll('use')).find(
    (node) => node.getAttribute('xlink:href') === '#icon-attacthment-download'
      && isVisible(node.ownerSVGElement || node)
  );
  const svg = useNode ? (useNode.ownerSVGElement || useNode.closest('svg')) : null;
  const target = svg ? (svg.closest('.popover.icon-content.popover-bottom') || svg.closest('button') || svg.parentElement) : null;
  if (!target || !isVisible(target)) {
    return JSON.stringify({ found: false });
  }

  target.scrollIntoView({ block: 'center', inline: 'center' });
  const rect = target.getBoundingClientRect();
  return JSON.stringify({
    found: true,
    x: rect.left + (rect.width / 2),
    y: rect.top + (rect.height / 2)
  });
})()
""".strip()


def build_attachment_preview_probe_expression() -> str:
    return """
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0
      && rect.height > 0
      && style.visibility !== 'hidden'
      && style.display !== 'none';
  };

  const overlay = Array.from(document.querySelectorAll('.resume-common-dialog.search-resume, .dialog-resume-full'))
    .find((el) => isVisible(el));
  if (overlay) {
    return JSON.stringify({ found: true, alreadyOpen: true });
  }

  const previewButton = Array.from(document.querySelectorAll('.card-btn')).find(
    (node) => (node.innerText || '').trim() === '鐐瑰嚮棰勮闄勪欢绠€鍘?
  );
  if (!previewButton) {
    return JSON.stringify({ found: false });
  }

  previewButton.scrollIntoView({ block: 'center' });
  previewButton.click();
  return JSON.stringify({ found: true, alreadyOpen: false });
})()
""".strip()


def build_apply_recommend_filters_expression(
    escaped_job_title: str,
    escaped_city: str,
    escaped_filters: str,
    escaped_auto_confirm: str = "true",
    operation_delay_seconds: float = 0,
) -> str:
    action_delay_ms = max(0, int(float(operation_delay_seconds or 0) * 1000))
    return f"""
(async () => {{
  const jobTitle = {escaped_job_title};
  const city = {escaped_city};
  const filters = {escaped_filters};
  const autoConfirm = {escaped_auto_confirm};
  const actionDelayMs = {action_delay_ms};
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const rootDocument = frame?.contentDocument || document;
  const rootWindow = rootDocument.defaultView || window;
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const optionClickDelayMs = actionDelayMs || 1000;
  const waitAfterOptionClick = async () => {{
    await delay(optionClickDelayMs);
  }};
  const text = (el) => el ? (el.innerText || el.textContent || el.value || '').trim() : '';
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = rootWindow.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }};
  const visibleNodes = (selector) => Array.from(rootDocument.querySelectorAll(selector)).filter(isVisible);
  const clickElement = (el) => {{
    const rect = el.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    for (const type of ['mouseover', 'mousemove', 'mousedown', 'mouseup', 'click']) {{
      el.dispatchEvent(new rootWindow.MouseEvent(type, {{
        bubbles: true,
        cancelable: true,
        view: rootWindow,
        clientX: x,
        clientY: y,
        button: 0,
        buttons: type === 'mousedown' ? 1 : 0
      }}));
    }}
    if (typeof el.click === 'function') {{
      el.click();
    }}
  }};
  const dismissLastFilterPrompt = async () => {{
    const panel = rootDocument.querySelector('.filter-panel') || rootDocument;
    const panelText = text(panel);
    if (!panelText.includes('\\u662f\\u5426\\u5e94\\u7528\\u4e0a\\u6b21\\u7684\\u7b5b\\u9009\\u6761\\u4ef6')) {{
      return {{ dismissed: false, reason: 'prompt_not_found' }};
    }}
    const cancelButton = visibleNodes('button, a, span, .btn, div')
      .filter((el) => text(el) === '\\u53d6\\u6d88')
      .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width)[0];
    if (!cancelButton) return {{ dismissed: false, reason: 'cancel_not_found' }};
    clickElement(cancelButton);
      await delay(actionDelayMs || 1000);
    return {{ dismissed: true }};
  }};
  const openFilterPanel = async () => {{
    if (rootDocument.querySelector('.filter-panel')) {{
      await dismissLastFilterPrompt();
      return {{ opened: true, alreadyOpen: true }};
    }}
    const filterTrigger = visibleNodes('.recommend-filter, .filter-label-wrap, .filter-label, button, a, span, label, li, div')
      .filter((el) => text(el).includes('\u7b5b\u9009'))
      .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width)[0];
    if (!filterTrigger) return {{ opened: false, reason: 'filter_button_not_found' }};
    await delay(1000);
    clickElement(filterTrigger);
    await delay(actionDelayMs || 1000);
    await dismissLastFilterPrompt();
    return {{ opened: Boolean(rootDocument.querySelector('.filter-panel')), alreadyOpen: false }};
  }};
  const splitFilterValues = (value) => {{
    if (Array.isArray(value)) return value.map((item) => String(item).trim()).filter(Boolean);
    return String(value || '')
      .split(new RegExp('[,，、;；|/]|(?:\\\\s+or\\\\s+)|或', 'i'))
      .map((item) => item.trim())
      .filter(Boolean);
  }};
  const normalizeComparable = (value) => String(value || '')
    .replace(/\s+/g, '')
    .replace(/[\\[\\]銆愩€戯紙锛?)]/g, '')
    .toLowerCase();
  const canonicalFilterLabel = (key) => {{
    const aliasMap = {{
      education: '\u5b66\u5386\u8981\u6c42',
      degree: '\u5b66\u5386\u8981\u6c42',
      salary: '\u85aa\u8d44\u5f85\u9047',
      experience: '\u7ecf\u9a8c\u8981\u6c42',
      active: '\u6d3b\u8dc3\u5ea6',
      '\u5b66\u5386': '\u5b66\u5386\u8981\u6c42',
      '\u5b66\u5386\u8981\u6c42': '\u5b66\u5386\u8981\u6c42',
      '\u85aa\u8d44': '\u85aa\u8d44\u5f85\u9047',
      '\u85aa\u8d44\u5f85\u9047': '\u85aa\u8d44\u5f85\u9047',
      '\u85aa\u8d44\u5f85\u9047[\u5355\u9009]': '\u85aa\u8d44\u5f85\u9047',
      '\u7ecf\u9a8c': '\u7ecf\u9a8c\u8981\u6c42',
      '\u5de5\u4f5c\u7ecf\u9a8c': '\u7ecf\u9a8c\u8981\u6c42',
      '\u7ecf\u9a8c\u8981\u6c42': '\u7ecf\u9a8c\u8981\u6c42',
      '\u6d3b\u8dc3': '\u6d3b\u8dc3\u5ea6',
      '\u6d3b\u8dc3\u5ea6': '\u6d3b\u8dc3\u5ea6'
    }};
    const raw = String(key || '').trim();
    return aliasMap[raw] || aliasMap[raw.toLowerCase()] || raw;
  }};
  const normalizeFilterLabel = (value) => String(value || '')
    .replace(/\s+/g, '')
    .replace(/[\\[\\]銆愩€戯紙锛?)]/g, '');
  const findFilterItem = (key) => {{
    const aliases = {{
      education: '瀛﹀巻瑕佹眰',
      degree: '瀛﹀巻瑕佹眰',
      activity: '\u6d3b\u8dc3\u5ea6',
      active: '\u6d3b\u8dc3\u5ea6',
      gender: '鎬у埆',
      status: '姹傝亴鎰忓悜',
      salary: '钖祫寰呴亣',
      experience: '缁忛獙瑕佹眰',
      school: '闄㈡牎',
      major: '涓撲笟',
      keyword: '\u725b\u4eba\u5173\u952e\u8bcd'
    }};
    const normalizedKey = aliases[String(key || '').trim().toLowerCase()] || String(key || '').trim();
    const comparableKey = normalizeFilterLabel(normalizedKey);
    if (!normalizedKey) return null;
    return visibleNodes('.filter-item, .condition-item, .filter-wrap')
      .filter((el) => text(el).includes(normalizedKey) || normalizeFilterLabel(text(el)).includes(comparableKey))
      .sort((a, b) => a.getBoundingClientRect().height - b.getBoundingClientRect().height)[0] || null;
  }};
  const findFilterItemV2 = (key) => {{
    const aliasMap = {{
      '\u5b66\u5386': '\u5b66\u5386\u8981\u6c42',
      '\u5b66\u5386\u8981\u6c42': '\u5b66\u5386\u8981\u6c42',
      '\u5e74\u9f84': '\u5e74\u9f84',
      '\u6d3b\u8dc3': '\u6d3b\u8dc3\u5ea6',
      '\u6d3b\u8dc3\u5ea6': '\u6d3b\u8dc3\u5ea6',
      '\u8fd1\u671f\u6ca1\u6709\u770b\u8fc7': '\u8fd1\u671f\u6ca1\u6709\u770b\u8fc7',
      '\u6c42\u804c\u72b6\u6001': '\u6c42\u804c\u610f\u5411',
      '\u6c42\u804c\u610f\u5411': '\u6c42\u804c\u610f\u5411',
      '\u85aa\u8d44': '\u85aa\u8d44\u5f85\u9047',
      '\u85aa\u8d44\u5f85\u9047': '\u85aa\u8d44\u5f85\u9047',
      '\u85aa\u8d44\u5f85\u9047[\u5355\u9009]': '\u85aa\u8d44\u5f85\u9047',
      '\u7ecf\u9a8c': '\u7ecf\u9a8c\u8981\u6c42',
      '\u7ecf\u9a8c\u8981\u6c42': '\u7ecf\u9a8c\u8981\u6c42',
      '\u9662\u6821': '\u9662\u6821',
      '\u5b66\u6821': '\u9662\u6821',
      '\u4e13\u4e1a': '\u4e13\u4e1a',
      '\u5173\u952e\u8bcd': '\u725b\u4eba\u5173\u952e\u8bcd',
      '\u725b\u4eba\u5173\u952e\u8bcd': '\u725b\u4eba\u5173\u952e\u8bcd'
    }};
    const normalize = (value) => String(value || '')
      .replace(/\s+/g, '')
      .replace(/[\[\]銆愩€戯紙锛?)]/g, '')
      .toLowerCase();
    const raw = String(key || '').trim();
    const wanted = normalize(aliasMap[raw] || aliasMap[raw.toLowerCase()] || raw);
    if (!wanted) return null;
    const groups = visibleNodes('.filter-item, .condition-item').map((el) => {{
      const labelNode = el.querySelector('.name, .filter-name, .label') || el.firstElementChild || el;
      const label = text(labelNode);
      return {{
        el,
        label,
        normalizedLabel: normalize(label),
        normalizedText: normalize(text(el))
      }};
    }}).filter((item) =>
      item.normalizedLabel.includes(wanted)
      || wanted.includes(item.normalizedLabel)
      || item.normalizedText.includes(wanted)
    );
    groups.sort((a, b) => {{
      const exact = Number(b.normalizedLabel === wanted) - Number(a.normalizedLabel === wanted);
      if (exact) return exact;
      const starts = Number(b.normalizedLabel.startsWith(wanted) || wanted.startsWith(b.normalizedLabel))
        - Number(a.normalizedLabel.startsWith(wanted) || wanted.startsWith(a.normalizedLabel));
      if (starts) return starts;
      return a.el.getBoundingClientRect().height - b.el.getBoundingClientRect().height;
    }});
    return groups[0]?.el || null;
  }};
  const findOption = (scope, value) => {{
    const selectors = '.option, .default, .operate-btn, button, a, span, label, li';
    const normalizedValue = String(value || '').trim().toLowerCase();
    const candidates = Array.from((scope || rootDocument).querySelectorAll(selectors))
      .filter(isVisible)
      .filter((el) => {{
        const nodeText = text(el);
        const normalizedText = nodeText.toLowerCase();
        return nodeText === value || nodeText.includes(value) || normalizedText === normalizedValue || normalizedText.includes(normalizedValue);
      }})
      .sort((a, b) => {{
        const textDelta = Number(text(a) !== value) - Number(text(b) !== value);
        if (textDelta) return textDelta;
        const rectA = a.getBoundingClientRect();
        const rectB = b.getBoundingClientRect();
        return (rectA.width * rectA.height) - (rectB.width * rectB.height);
      }});
    return candidates[0] || null;
  }};
  const clickOptionAndReadSelected = async (scope, value) => {{
    const target = findOption(scope, value) || findOption(rootDocument.querySelector('.filter-panel') || rootDocument, value);
    if (!target) {{
      return {{ clicked: false, selected: [], reason: 'option_not_found' }};
    }}
    const clickTarget = target.closest('button, a, label, li, .option, .default, .operate-btn') || target;
    clickTarget.scrollIntoView?.({{ block: 'center', inline: 'center' }});
    await waitAfterOptionClick();
    clickElement(clickTarget);
    await waitAfterOptionClick();
    const group = scope || clickTarget.closest('.filter-item, .condition-item, .filter-wrap') || rootDocument;
    let selected = selectedTextsInGroup(group);
    let vueForced = null;
    if (!valuesVerifiedBySelected([value], selected)) {{
      vueForced = forceOptionViaVue(group, value);
      if (vueForced.applied) {{
        await waitAfterOptionClick();
        selected = selectedTextsInGroup(group);
      }}
    }}
    return {{
      clicked: true,
      selected,
      vueForced,
      targetText: text(clickTarget) || text(target),
      groupText: text(group).slice(0, 300)
    }};
  }};
  const forceOptionViaVue = (scope, value) => {{
    const group = scope || rootDocument;
    const target = findOption(group, value);
    const optionVm = target?.__vue__;
    const checkbox = target?.closest('.check-box') || group.querySelector?.('.check-box');
    const checkboxVm = checkbox?.__vue__;
    const parentVm = checkboxVm?.$parent || optionVm?.$parent?.$parent || optionVm?.$parent;
    const filterRootVm = findFilterRootVue(target || group);
    const optionItem = optionVm?._props?.optionItem
      || (checkboxVm?._props?.option?.options || []).find((item) => normalizeComparable(item?.name) === normalizeComparable(value));
    const paramName = checkboxVm?._props?.option?.paramName || optionVm?.$parent?._props?.option?.paramName || '';
    const code = optionItem?.code;
    if (!target || !optionItem || !paramName || typeof code === 'undefined' || (!parentVm && !filterRootVm)) {{
      return {{
        applied: false,
        reason: 'vue_option_context_not_found',
        hasTarget: Boolean(target),
        hasOptionItem: Boolean(optionItem),
        paramName,
        hasParent: Boolean(parentVm),
        hasFilterRoot: Boolean(filterRootVm)
      }};
    }}
    const current = {{
      ...(filterRootVm?._data?.checkedFilters || {{}}),
      ...(parentVm?._props?.value || parentVm?.value || {{}})
    }};
    const multiple = checkboxVm?._props?.multiple !== false;
    const existing = Array.isArray(current[paramName]) ? current[paramName] : [];
    current[paramName] = multiple ? [...new Set([...existing, code])] : code;
    try {{
      if (filterRootVm?._data) {{
        filterRootVm._data.checkedFilters = {{ ...(filterRootVm._data.checkedFilters || {{}}), ...current }};
        filterRootVm.$set?.(filterRootVm._data.checkedFilters, paramName, current[paramName]);
        filterRootVm.$forceUpdate?.();
      }}
      parentVm?.$emit?.('change', current);
      try {{ parentVm?.change?.(current); }} catch (error) {{}}
      optionVm?.$forceUpdate?.();
      checkboxVm?.$forceUpdate?.();
      parentVm?.$forceUpdate?.();
      return {{
        applied: true,
        method: 'vue_filter_value_change',
        paramName,
        code,
        optionName: optionItem.name,
        multiple,
        checkedFilters: filterRootVm?._data?.checkedFilters || current
      }};
    }} catch (error) {{
      return {{ applied: false, reason: String(error?.message || error), paramName, code, optionName: optionItem.name }};
    }}
  }};
  const findFilterRootVue = (start) => {{
    let current = start;
    while (current) {{
      if (current.__vue__?._data && Object.prototype.hasOwnProperty.call(current.__vue__._data, 'checkedFilters')) {{
        return current.__vue__;
      }}
      current = current.parentElement;
    }}
    return visibleNodes('.filter-wrap, .filter-panel, .filters-wrap')
      .map((el) => el.__vue__)
      .find((vm) => vm?._data && Object.prototype.hasOwnProperty.call(vm._data, 'checkedFilters')) || null;
  }};
  const selectedTextsInGroup = (scope) => {{
    if (!scope) return [];
    const selectedSelectors = [
      '.selected',
      '.active',
      '.checked',
      '.is-selected',
      '.is-active',
      '[aria-selected="true"]',
      '[aria-checked="true"]',
      'input:checked'
    ];
    return selectedSelectors
      .flatMap((selector) => Array.from(scope.querySelectorAll(selector)))
      .filter(isVisible)
      .map((node) => text(node.closest('label, li, button, a, .option, .default, .operate-btn') || node))
      .filter(Boolean)
      .filter((item, index, list) => list.indexOf(item) === index)
      .slice(0, 20);
  }};
  const valuesVerifiedBySelected = (values, selectedTexts) => {{
    const normalizedSelected = (selectedTexts || []).map(normalizeComparable);
    return values.every((item) => {{
      const wanted = normalizeComparable(item);
      return normalizedSelected.some((actual) => actual === wanted || actual.includes(wanted) || wanted.includes(actual));
    }});
  }};
  const matchingValuesBySelected = (values, selectedTexts) => {{
    const normalizedSelected = (selectedTexts || []).map(normalizeComparable);
    return (values || []).filter((item) => {{
      const wanted = normalizeComparable(item);
      return normalizedSelected.some((actual) => actual === wanted || actual.includes(wanted) || wanted.includes(actual));
    }});
  }};
  const scanPanelCapabilities = () => {{
    const groups = visibleNodes('.filter-item, .condition-item, .filter-wrap')
      .map((el) => {{
        const labelNode = el.querySelector('.name, .filter-name, .label') || el.firstElementChild || el;
        const options = Array.from(el.querySelectorAll('.option, .default, .operate-btn, button, a, span, label, li'))
          .filter(isVisible)
          .map((node) => text(node))
          .filter(Boolean)
          .filter((item, index, list) => list.indexOf(item) === index)
          .slice(0, 40);
        const selected = selectedTextsInGroup(el);
        const inputCount = Array.from(el.querySelectorAll('input, textarea, [contenteditable="true"]')).filter(isVisible).length;
        const sliderCount = Array.from(el.querySelectorAll('.vue-slider-rail, .boss-slider__runway, .el-slider__runway, [role="slider"], [class*="slider"]')).filter(isVisible).length;
        return {{
          label: text(labelNode),
          text: text(el).slice(0, 300),
          options,
          selected,
          controlType: sliderCount ? 'slider' : (inputCount ? 'input' : 'options')
        }};
      }})
      .filter((item) => item.label || item.text || item.options.length);
    const pageTags = visibleNodes('.filter-label, .filter-label-wrap, .recommend-filter, .filter-selected, .selected-filter, .condition-selected')
      .map(text)
      .filter(Boolean)
      .slice(0, 30);
    return {{ panelOpen: Boolean(rootDocument.querySelector('.filter-panel')), groups, pageTags }};
  }};
  const similarPanelItems = (key, value, panelScan) => {{
    const wantedKey = normalizeComparable(canonicalFilterLabel(key));
    const wantedValues = splitFilterValues(value).map(normalizeComparable);
    const groups = (panelScan?.groups || [])
      .map((group) => {{
        const label = normalizeComparable(group.label);
        const body = normalizeComparable(group.text);
        const keyScore = Number(label.includes(wantedKey) || wantedKey.includes(label))
          + Number(body.includes(wantedKey) || wantedKey.includes(body));
        const optionScore = (group.options || []).filter((option) => {{
          const normalized = normalizeComparable(option);
          return wantedValues.some((wanted) => normalized.includes(wanted) || wanted.includes(normalized));
        }}).length;
        return {{ label: group.label, options: group.options, controlType: group.controlType, score: keyScore + optionScore }};
      }})
      .filter((group) => group.score > 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 5);
    const options = groups.flatMap((group) => group.options || [])
      .filter((item, index, list) => item && list.indexOf(item) === index)
      .slice(0, 20);
    return {{ groups, options }};
  }};
  const setElementValue = (target, value) => {{
    target.focus();
    if (target.isContentEditable) {{
      target.innerHTML = '';
      target.textContent = value;
    }} else {{
      target.value = value;
    }}
    target.dispatchEvent(new InputEvent('input', {{ bubbles: true, inputType: 'insertText', data: value }}));
    target.dispatchEvent(new Event('change', {{ bubbles: true }}));
  }};
  const setInput = (labels, value) => {{
    if (!value) return {{ applied: false, reason: 'empty_value' }};
    const inputs = visibleNodes('input, textarea, [contenteditable="true"]');
    const target = inputs.find((el) => {{
      const placeholder = el.getAttribute('placeholder') || '';
      const aria = el.getAttribute('aria-label') || '';
      const parentText = text(el.closest('.form-item, .filter-item, .condition-item, li, div'));
      return labels.some((label) => placeholder.includes(label) || aria.includes(label) || parentText.includes(label));
    }});
    if (!target) return {{ applied: false, reason: 'input_not_found' }};
    setElementValue(target, value);
    return {{ applied: true }};
  }};
  const parseAgeRange = (rawValue) => {{
    const value = String(rawValue || '').trim();
    const match = value.match(/(\\d{{1,2}})\\D+(\\d{{1,2}})/);
    if (!match) return null;
    return {{ min: Number(match[1]), max: Number(match[2]) }};
  }};
  const isAgeFilterKey = (key) => ['\u5e74\u9f84', '\u5e74\u7eaa', 'age', '骞撮緞', '骞寸邯'].some((label) => String(key).toLowerCase().includes(label.toLowerCase()));
  const nearestText = (el) => text(el.closest('.form-item, .filter-item, .condition-item, .dropdown, .select, li, div'));
  const dispatchMouse = (target, type, x, y, buttons = 1) => {{
    target.dispatchEvent(new rootWindow.MouseEvent(type, {{
      bubbles: true,
      cancelable: true,
      view: rootWindow,
      clientX: x,
      clientY: y,
      button: 0,
      buttons
    }}));
  }};
  const dragHandle = (handle, x, y) => {{
    const rect = handle.getBoundingClientRect();
    const startX = rect.left + rect.width / 2;
    const startY = rect.top + rect.height / 2;
    dispatchMouse(handle, 'mouseover', startX, startY, 0);
    dispatchMouse(handle, 'mousemove', startX, startY, 0);
    dispatchMouse(handle, 'mousedown', startX, startY, 1);
    const steps = 8;
    for (let step = 1; step <= steps; step += 1) {{
      const nextX = startX + ((x - startX) * step / steps);
      dispatchMouse(rootDocument, 'mousemove', nextX, y, 1);
    }}
    dispatchMouse(rootDocument, 'mouseup', x, y, 0);
  }};
  const applyAgeSlider = (range) => {{
    const ageItem = visibleNodes('.filter-item.age, .filter-item')
      .find((el) => text(el).includes('\u5e74\u9f84') || text(el).includes('\u5c81'));
    const scope = ageItem || rootDocument;
    const vueHandles = Array.from(scope.querySelectorAll('.vue-slider > .vue-slider-dot, .vue-slider-rail > .vue-slider-dot'))
      .filter(isVisible);
    const handles = (vueHandles.length >= 2 ? vueHandles : Array.from(scope.querySelectorAll('.boss-slider__button, .el-slider__button, .slider-handle, [role="slider"], [class*="slider"][class*="button"], [class*="slider"][class*="handle"]')))
      .filter(isVisible);
    const track = Array.from(scope.querySelectorAll('.vue-slider-rail, .boss-slider__runway, .boss-slider, .el-slider__runway, .slider-track, .slider, [class*="slider"]'))
      .filter(isVisible)
      .map((el) => ({{ el, rect: el.getBoundingClientRect() }}))
      .filter((item) => item.rect.width > 80 && item.rect.height <= 60)
      .sort((a, b) => b.rect.width - a.rect.width)[0];
    if (handles.length < 2 || !track) {{
      return {{ applied: false, reason: 'age_slider_not_found', handleCount: handles.length }};
    }}

    const sortedHandles = handles
      .map((el) => {{
        const rect = el.getBoundingClientRect();
        const tooltip = el.querySelector('.vue-slider-dot-tooltip-text');
        const label = Number(((tooltip ? text(tooltip) : text(el)).match(/\\d{{1,2}}/) || [])[0]);
        return {{ el, rect, label }};
      }})
      .sort((a, b) => a.rect.left - b.rect.left);
    const sliderHandles = sortedHandles.slice(0, 2);

    const numericHandles = sliderHandles.filter((item) => Number.isFinite(item.label));
    let pixelsPerYear = 0;
    if (numericHandles.length >= 2 && numericHandles[0].label !== numericHandles[1].label) {{
      pixelsPerYear = (numericHandles[1].rect.left - numericHandles[0].rect.left) / (numericHandles[1].label - numericHandles[0].label);
    }}

    const minAge = 16;
    const maxAge = 46;
    const clamp = (value) => Math.max(minAge, Math.min(maxAge, value));
    const toX = (age, handle) => {{
      if (pixelsPerYear && Number.isFinite(handle.label)) {{
        return handle.rect.left + handle.rect.width / 2 + (age - handle.label) * pixelsPerYear;
      }}
      const ratio = (clamp(age) - minAge) / (maxAge - minAge);
      return track.rect.left + ratio * track.rect.width;
    }};
    const y = track.rect.top + track.rect.height / 2;
    dragHandle(sliderHandles[0].el, toX(range.min, sliderHandles[0]), y);
    dragHandle(sliderHandles[1].el, toX(range.max, sliderHandles[1]), y);
    return {{
      applied: true,
      method: 'slider',
      min: range.min,
      max: range.max,
      calibratedFrom: sliderHandles.map((item) => Number.isFinite(item.label) ? item.label : text(item.el)),
      fallbackScale: pixelsPerYear ? null : [minAge, maxAge],
      handleCount: handles.length
    }};
  }};
  const applyAgeInputs = (range) => {{
    const inputs = visibleNodes('input, textarea, [contenteditable="true"]');
    const ageInputs = inputs.filter((el) => {{
      const placeholder = el.getAttribute('placeholder') || '';
      const aria = el.getAttribute('aria-label') || '';
      const parentText = nearestText(el);
      return /骞撮緞|宀亅鏈€灏弢鏈€澶min|max|寮€濮媩缁撴潫/.test(`${{placeholder}} ${{aria}} ${{parentText}}`);
    }});
    const targets = ageInputs.length >= 2 ? ageInputs.slice(0, 2) : inputs.slice(-2);
    if (targets.length < 2) return {{ applied: false, reason: 'age_inputs_not_found' }};
    setElementValue(targets[0], String(range.min));
    setElementValue(targets[1], String(range.max));
    return {{ applied: true, method: 'inputs', min: range.min, max: range.max }};
  }};
  const applyAgeFilter = async (rawValue) => {{
    const range = parseAgeRange(rawValue);
    if (!range) return {{ applied: false, reason: 'invalid_age_range' }};
    const filterTrigger = visibleNodes('.recommend-filter, .filter-label-wrap, .filter-label, button, a, span, label, li, div')
      .filter((el) => text(el).includes('\u7b5b\u9009'))
      .sort((a, b) => a.getBoundingClientRect().width - b.getBoundingClientRect().width)[0];
    if (!rootDocument.querySelector('.filter-panel') && filterTrigger) {{
      clickElement(filterTrigger);
    }}
    await delay(actionDelayMs || 1000);
    const ageTrigger = visibleNodes('.filter-item.age, .filter-item, button, a, span, label, li, div')
      .find((el) => text(el) === '骞撮緞' || text(el).startsWith('骞撮緞\\n') || text(el).includes('骞撮緞'));
    if (!ageTrigger) return {{ applied: false, reason: 'age_trigger_not_found', filterOpened: Boolean(rootDocument.querySelector('.filter-panel')) }};

    const sliderResult = applyAgeSlider(range);
    const inputResult = sliderResult.applied ? null : applyAgeInputs(range);

    const result = sliderResult.applied ? sliderResult : inputResult;
    await delay(actionDelayMs || 1000);
    return {{
      ...result,
      min: range.min,
      max: range.max,
      triggerText: text(ageTrigger),
      confirmed: false
    }};
  }};
  const captureFilterEcho = () => {{
    const panel = rootDocument.querySelector('.filter-panel') || rootDocument;
    const selectedSelectors = [
      '.selected',
      '.active',
      '.checked',
      '.is-selected',
      '.is-active',
      '[aria-selected="true"]',
      '[aria-checked="true"]',
      'input:checked'
    ];
    const selectedText = selectedSelectors
      .flatMap((selector) => Array.from(panel.querySelectorAll(selector)))
      .filter(isVisible)
      .map((el) => text(el.closest('label, li, button, a, .option, .default, .operate-btn, .filter-item, .condition-item') || el))
      .filter(Boolean)
      .slice(0, 80);
    const summaryText = visibleNodes('.filter-label, .filter-label-wrap, .recommend-filter, .filter-selected, .selected-filter, .condition-selected')
      .map(text)
      .filter(Boolean)
      .slice(0, 20);
    return {{
      panelOpen: Boolean(rootDocument.querySelector('.filter-panel')),
      selectedText,
      summaryText,
      combinedText: [...selectedText, ...summaryText].join('\\n')
    }};
  }};
  const confirmFilterPanel = async () => {{
    const panel = rootDocument.querySelector('.filter-panel');
    if (!panel) return {{ clicked: false, reason: 'panel_not_open' }};
    const exactConfirmButton = Array.from(panel.querySelectorAll('button, a, .btn, span, div'))
      .filter(isVisible)
      .filter((el) => ['\u786e\u5b9a', '\u786e\u8ba4', '\u5b8c\u6210'].includes(text(el)))
      .map((el) => {{
        const rect = el.getBoundingClientRect();
        return {{ el, area: rect.width * rect.height, top: rect.top, left: rect.left }};
      }})
      .filter((item) => item.area > 100 && item.area < 20000)
      .sort((a, b) => (b.top - a.top) || (b.left - a.left) || (a.area - b.area))[0]?.el || null;
    if (exactConfirmButton) {{
      clickElement(exactConfirmButton);
      await delay(actionDelayMs || 1000);
      if (!rootDocument.querySelector('.filter-panel')) {{
        return {{ clicked: true, method: 'dom_exact_button', panelClosed: true }};
      }}
    }}
    const rootVm = findFilterRootVue(panel);
    if (rootVm?.confirm) {{
      try {{
        rootVm.confirm();
        await delay(actionDelayMs || 1000);
        return {{
          clicked: true,
          method: 'vue_confirm',
          panelClosed: !rootDocument.querySelector('.filter-panel'),
          checkedFilters: rootVm._data?.checkedFilters || {{}}
        }};
      }} catch (error) {{
        return {{
          clicked: Boolean(exactConfirmButton),
          method: exactConfirmButton ? 'dom_exact_button' : 'none',
          panelClosed: false,
          reason: String(error?.message || error)
        }};
      }}
    }}
    return {{
      clicked: Boolean(exactConfirmButton),
      method: exactConfirmButton ? 'dom_exact_button' : 'none',
      panelClosed: !rootDocument.querySelector('.filter-panel')
    }};
  }};
  const valueVerified = (key, value, result, echo) => {{
    if (!result?.applied) return false;
    if (isAgeFilterKey(key)) {{
      const combined = String(echo?.combinedText || '').toLowerCase().replace(/\\s+/g, '');
      const range = parseAgeRange(value);
      return Boolean(range && combined.includes(String(range.min)) && combined.includes(String(range.max)));
    }}
    const values = splitFilterValues(value);
    if (Array.isArray(result.selectedAfterClick) && valuesVerifiedBySelected(values, result.selectedAfterClick)) return true;
    if (Array.isArray(result.selectedBeforeConfirm) && valuesVerifiedBySelected(values, result.selectedBeforeConfirm)) return true;
    if (Array.isArray(result.selectedAfterConfirm) && valuesVerifiedBySelected(values, result.selectedAfterConfirm)) return true;
    return false;
  }};
  const applied = {{
    jobTitle: setInput(['鑱屼綅', '宀椾綅', '鎼滅储鐗涗汉'], jobTitle),
    city: setInput(['鍩庡競', '鍦扮偣', '鏈熸湜鍩庡競'], city)
  }};
  const appliedFilters = [];
  const missingFilters = [];
  const specialFilters = {{}};
  const filterEntries = Object.entries(filters || {{}});
  const filterTasks = filterEntries.map(([key, value]) => ({{
    rawKey: key,
    rawValue: value,
    normalizedKey: canonicalFilterLabel(key),
    controlType: 'unknown',
    status: 'pending',
    evidence: [],
    similarGroups: [],
    similarOptions: []
  }}));
  if (filterEntries.length > 0) {{
    await openFilterPanel();
  }}
  const panelScanBefore = filterEntries.length > 0 ? scanPanelCapabilities() : {{ panelOpen: false, groups: [], pageTags: [] }};
  for (const [key, value] of Object.entries(filters || {{}})) {{
    const task = filterTasks.find((item) => item.rawKey === key);
    if (isAgeFilterKey(key)) {{
      const result = await applyAgeFilter(value);
      specialFilters[key] = result;
      if (task) {{
        task.controlType = result.method || 'slider';
        task.status = result.applied ? 'clicked' : 'missing';
        task.evidence = result.applied ? ['age_control_changed'] : [];
        const similar = similarPanelItems(key, value, panelScanBefore);
        task.similarGroups = similar.groups;
        task.similarOptions = similar.options;
      }}
      if (result.applied) {{
        appliedFilters.push(key);
      }} else {{
        missingFilters.push(key);
      }}
      continue;
    }}
    const values = splitFilterValues(value);
    const filterItem = findFilterItemV2(key) || findFilterItem(key);
    const similar = similarPanelItems(key, value, panelScanBefore);
    const clickedValues = [];
    const missingValues = [];
    const selectedSnapshots = [];
    for (const item of values) {{
      const selectedBefore = selectedTextsInGroup(filterItem || rootDocument);
      const firstClick = valuesVerifiedBySelected([item], selectedBefore)
        ? {{ clicked: true, selected: selectedBefore, alreadySelected: true }}
        : await clickOptionAndReadSelected(filterItem, item);
      if (firstClick.clicked) {{
        let latestClick = firstClick;
        let retried = false;
        if (firstClick.alreadySelected) {{
          await waitAfterOptionClick();
        }}
        if (!firstClick.alreadySelected && !valuesVerifiedBySelected([item], firstClick.selected)) {{
          retried = true;
          latestClick = await clickOptionAndReadSelected(filterItem, item);
        }}
        clickedValues.push(item);
        selectedSnapshots.push({{
          value: item,
          selected: latestClick.selected,
          firstSelected: firstClick.selected,
          alreadySelected: Boolean(firstClick.alreadySelected),
          retried,
          vueForced: latestClick.vueForced || firstClick.vueForced || null,
          targetText: latestClick.targetText || firstClick.targetText || '',
          groupText: latestClick.groupText || firstClick.groupText || ''
        }});
      }} else {{
        missingValues.push(item);
        await waitAfterOptionClick();
      }}
      await waitAfterOptionClick();
    }}
    const selectedAfterClick = selectedTextsInGroup(filterItem || rootDocument);
    specialFilters[key] = {{
      applied: clickedValues.length > 0,
      method: 'panel_options',
      values,
      clickedValues,
      missingValues,
      selectedAfterClick,
      selectedSnapshots,
      scoped: Boolean(filterItem),
      groupText: filterItem ? text(filterItem).slice(0, 300) : '',
      similarGroups: similar.groups,
      similarOptions: similar.options
    }};
    if (task) {{
      task.controlType = 'options';
      task.status = clickedValues.length === values.length ? 'clicked' : (clickedValues.length > 0 ? 'partial' : 'missing');
      task.evidence = clickedValues.length > 0 ? ['option_clicked'] : [];
      task.similarGroups = similar.groups;
      task.similarOptions = similar.options;
      task.expectedValues = values;
      task.clickedValues = clickedValues;
      task.missingValues = missingValues;
      task.selectedAfterClick = selectedAfterClick;
      task.selectedSnapshots = selectedSnapshots;
    }}
    if (clickedValues.length > 0) {{
      appliedFilters.push(key);
    }} else {{
      missingFilters.push(key);
    }}
  }}
  const echoBeforeConfirm = captureFilterEcho();
  for (const [key] of Object.entries(filters || {{}})) {{
    const result = specialFilters[key];
    if (result?.method === 'panel_options') {{
      const filterItem = findFilterItemV2(key) || findFilterItem(key);
      result.selectedBeforeConfirm = selectedTextsInGroup(filterItem || rootDocument);
    }}
  }}
  const confirmButton = autoConfirm && filterEntries.length > 0 && appliedFilters.length > 0
    ? visibleNodes('button, a, .btn, span')
        .find((el) => /\u786e\u5b9a|\u786e\u8ba4|\u5b8c\u6210|\u7b5b\u9009/.test(text(el)))
    : null;
  if (confirmButton) {{
    clickElement(confirmButton);
    await delay(actionDelayMs || 1000);
  }}
  if (autoConfirm && rootDocument.querySelector('.filter-panel')) {{
    const exactConfirmButton = visibleNodes('.filter-panel button, .filter-panel a, .filter-panel .btn, .filter-panel span, .filter-panel div')
      .filter((el) => ['\u786e\u5b9a', '\u786e\u8ba4', '\u5b8c\u6210'].includes(text(el)))
      .sort((a, b) => {{
        const ar = a.getBoundingClientRect();
        const br = b.getBoundingClientRect();
        return (br.top - ar.top) || ((ar.width * ar.height) - (br.width * br.height));
      }})[0];
    if (exactConfirmButton) {{
      clickElement(exactConfirmButton);
      await delay(actionDelayMs || 1000);
    }}
  }}
  const confirmResult = autoConfirm && filterEntries.length > 0 && appliedFilters.length > 0
    ? await confirmFilterPanel()
    : {{ clicked: Boolean(confirmButton), reason: 'auto_confirm_disabled_or_no_filters' }};
  const echoAfterConfirm = captureFilterEcho();
  const panelScanAfter = scanPanelCapabilities();
  for (const [key] of Object.entries(filters || {{}})) {{
    const result = specialFilters[key];
    if (result?.method === 'panel_options') {{
      const filterItem = findFilterItemV2(key) || findFilterItem(key);
      result.selectedAfterConfirm = selectedTextsInGroup(filterItem || rootDocument);
    }}
  }}
  const verification = {{}};
  const verifiedAppliedFilters = [];
  const unverifiedFilters = [];
  for (const [key, value] of Object.entries(filters || {{}})) {{
    const result = specialFilters[key];
    const verifiedBeforeConfirm = valueVerified(key, value, result, echoBeforeConfirm);
    const verifiedAfterConfirm = valueVerified(key, value, result, echoAfterConfirm);
    const verified = verifiedBeforeConfirm || verifiedAfterConfirm;
    const expectedValues = splitFilterValues(value);
    const verifiedValues = isAgeFilterKey(key)
      ? (verified ? expectedValues : [])
      : Array.from(new Set([
          ...matchingValuesBySelected(expectedValues, result?.selectedAfterClick || []),
          ...matchingValuesBySelected(expectedValues, result?.selectedBeforeConfirm || []),
          ...matchingValuesBySelected(expectedValues, result?.selectedAfterConfirm || [])
        ]));
    const missingValues = expectedValues.filter((item) => !verifiedValues.includes(item));
    const task = filterTasks.find((item) => item.rawKey === key);
    const evidence = [];
    if (valuesVerifiedBySelected(splitFilterValues(value), result?.selectedAfterClick || [])) evidence.push('group_selected_after_click');
    if (valuesVerifiedBySelected(splitFilterValues(value), result?.selectedBeforeConfirm || [])) evidence.push('group_selected_before_confirm');
    if (valuesVerifiedBySelected(splitFilterValues(value), result?.selectedAfterConfirm || [])) evidence.push('group_selected_after_confirm');
    if (verifiedBeforeConfirm && isAgeFilterKey(key)) evidence.push('age_echo_before_confirm');
    if (verifiedAfterConfirm && isAgeFilterKey(key)) evidence.push('age_echo_after_confirm');
    if (verifiedValues.length > 0) verifiedAppliedFilters.push(key);
    if (task) {{
      task.status = verified ? 'verified' : (verifiedValues.length > 0 ? 'partial' : (result?.applied ? 'unverified' : 'missing'));
      task.evidence = [...new Set([...(task.evidence || []), ...evidence])];
      task.actual = result?.actual || result?.clickedValues || [];
      task.expectedValues = expectedValues;
      task.verifiedValues = verifiedValues;
      task.missingValues = missingValues;
      task.verified = verified;
    }}
    verification[key] = {{
      applied: Boolean(result?.applied),
      verified,
      expected: value,
      expectedValues,
      verifiedValues,
      missingValues,
      actual: result?.actual || result?.clickedValues || [],
      selectedAfterClick: result?.selectedAfterClick || [],
      selectedBeforeConfirm: result?.selectedBeforeConfirm || [],
      selectedAfterConfirm: result?.selectedAfterConfirm || [],
      selectedSnapshots: result?.selectedSnapshots || [],
      evidence,
      similarGroups: result?.similarGroups || [],
      similarOptions: result?.similarOptions || [],
      beforeConfirmText: echoBeforeConfirm.combinedText,
      afterConfirmText: echoAfterConfirm.combinedText
    }};
    if (!verified) unverifiedFilters.push(key);
  }}
  const shouldSearch = applied.jobTitle.applied || applied.city.applied || appliedFilters.length > 0;
  const searchButton = shouldSearch && filterEntries.length === 0
    ? visibleNodes('button, a, .btn')
        .find((el) => isVisible(el) && /\u641c\u7d22|\u786e\u5b9a|\u786e\u8ba4|\u7b5b\u9009|\u5b8c\u6210/.test(text(el)))
    : null;
  if (searchButton) clickElement(searchButton);
  await delay(actionDelayMs || 1000);
  return JSON.stringify({{
    searched: Boolean(searchButton),
    skippedSearch: !shouldSearch,
    jobTitle,
    city,
    filters,
    applied,
    specialFilters,
    filterTasks,
    panelScanBefore,
    panelScanAfter,
    clickedFilters: appliedFilters,
    appliedFilters: verifiedAppliedFilters,
    missingFilters,
    verification,
    unverifiedFilters,
    needsConfirm: appliedFilters.length > 0,
    confirmed: Boolean(confirmResult.clicked),
    confirmResult,
    url: location.href,
    frameUrl: frame?.src || ''
  }});
}})()
""".strip()


def build_capture_recommend_candidates_expression(max_count: int) -> str:
    return f"""
(() => {{
  const text = (el) => el ? (el.innerText || '').trim() : '';
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }};
  const firstLineMatching = (lines, patterns) => lines.find((line) => patterns.some((pattern) => pattern.test(line))) || '';
  const parseCard = (el, index) => {{
    const raw = text(el);
    const lines = raw.split('\\n').map((line) => line.trim()).filter(Boolean);
    const tags = Array.from(el.querySelectorAll('.tag, .label, .badge, [class*="tag"], [class*="label"]'))
      .map(text)
      .filter(Boolean)
      .slice(0, 12);
    const candidateName = text(el.querySelector('.name, .geek-name, [class*="name"]')) || lines[0] || '';
    return {{
      candidateName,
      expectedTitle: firstLineMatching(lines, [/鏈熸湜/, /姹傝亴/, /宀椾綅/, /杩愯惀/, /缁忕悊/, /涓撳憳/]),
      expectedCity: firstLineMatching(lines, [/娣卞湷|骞垮窞|涓婃捣|鍖椾含|鏉窞|鎴愰兘|涓滆帪|浣涘北/]),
      experience: firstLineMatching(lines, [/缁忛獙|骞?]),
      education: firstLineMatching(lines, [/鏈|澶т笓|纭曞＋|鐮旂┒鐢焲鍗氬＋|瀛﹀巻/]),
      tags,
      activeStatus: firstLineMatching(lines, [/鍦ㄧ嚎|娲昏穬|鍒氬垰|浠婃棩|鏈懆|杩戞湡/]),
      detailTextPreview: raw.slice(0, 500),
      cardIndex: index,
      candidateId: el.getAttribute('data-geekid') || el.getAttribute('data-geek-id') || el.getAttribute('data-id') || String(index)
    }};
  }};
  const cards = Array.from(document.querySelectorAll('[data-geekid], [data-geek-id], [data-id], .geek-card, .recommend-card, .candidate-card, .card'))
    .filter((el) => isVisible(el) && text(el).length > 20);
  const seen = new Set();
  const candidates = [];
  cards.forEach((card) => {{
    const item = parseCard(card, candidates.length);
    const key = item.candidateId + ':' + item.candidateName + ':' + item.detailTextPreview.slice(0, 30);
    if (!seen.has(key) && item.candidateName) {{
      seen.add(key);
      candidates.push(item);
    }}
  }});
  return JSON.stringify({{
    title: document.title,
    url: location.href,
    count: candidates.slice(0, {max_count}).length,
    candidates: candidates.slice(0, {max_count})
  }});
}})()
""".strip()


def build_capture_recommend_candidates_expression_v2(max_count: int) -> str:
    return f"""
(() => {{
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame && frame.contentDocument ? frame.contentDocument : document;
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = (doc.defaultView || window).getComputedStyle(el);
    const viewportHeight = (doc.defaultView || window).innerHeight || document.documentElement.clientHeight || 0;
    const viewportWidth = (doc.defaultView || window).innerWidth || document.documentElement.clientWidth || 0;
    return rect.width > 0
      && rect.height > 0
      && rect.bottom >= -200
      && rect.top <= viewportHeight + 2000
      && rect.right >= 0
      && rect.left <= viewportWidth
      && style.display !== 'none'
      && style.visibility !== 'hidden';
  }};
  const firstLineMatching = (lines, patterns) => lines.find((line) => patterns.some((pattern) => pattern.test(line))) || '';
  const parseCard = (el, index) => {{
    const raw = text(el);
    const lines = raw.split('\\n').map((line) => line.trim()).filter(Boolean);
    const tags = Array.from(el.querySelectorAll('.tag, .label, .badge, [class*="tag"], [class*="label"]'))
      .map(text)
      .filter(Boolean)
      .slice(0, 12);
    const salary = firstLineMatching(lines, [/\\d+\\s*-\\s*\\d+\\s*[kK]/, /闈㈣/]);
    const candidateName = text(el.querySelector('.name, .geek-name, [class*="name"]'))
      || (salary && lines[1])
      || lines.find((line, idx) => idx > 0 && line.length <= 12 && !/\\d+\\s*-\\s*\\d+\\s*[kK]/.test(line))
      || lines[0]
      || '';
    return {{
      candidateName,
      salary,
      expectedTitle: firstLineMatching(lines, [/鏈熸湜/, /姹傝亴/, /宀椾綅/, /鑱屼綅/, /杩愯惀/, /缁忕悊/, /涓撳憳/]),
      expectedCity: firstLineMatching(lines, [/娣卞湷|骞垮窞|涓婃捣|鍖椾含|鏉窞|鎴愰兘|涓滆帪|浣涘北/]),
      experience: firstLineMatching(lines, [/\u7ecf\u9a8c|\\d+\u5e74/]),
      education: firstLineMatching(lines, [/鏈|澶т笓|纭曞＋|鐮旂┒鐢焲鍗氬＋|楂樹腑|涓笓|瀛﹀巻/]),
      age: firstLineMatching(lines, [/\\d+\\s*\u5c81/]),
      tags,
      activeStatus: firstLineMatching(lines, [/鍦ㄧ嚎|娲昏穬|鍒氬垰|浠婃棩|鏈懆|杩戞湡/]),
      detailTextPreview: raw.slice(0, 800),
      cardIndex: index,
      candidateId: el.getAttribute('data-geekid') || el.getAttribute('data-geek-id') || el.getAttribute('data-id') || String(index)
    }};
  }};
  const isMainCandidateCard = (el) => {{
    const raw = text(el);
    if (raw.length <= 20) return false;
    const item = el?.matches?.('li.card-item') ? el : el?.closest?.('li.card-item');
    const titleText = text((item || el).querySelector?.('div.title, .title'));
    const itemText = text(item || el);
    const hasSimilarRecommendTitle = (titleText.includes('\u4e3a\u4f60\u63a8\u8350') && titleText.includes('\u76f8\u4f3c'))
      || (itemText.includes('\u4e3a\u4f60\u63a8\u8350') && itemText.includes('\u76f8\u4f3c'));
    const isSimilarRecommendItem = Boolean(
      item && hasSimilarRecommendTitle && item.querySelectorAll('div.geek-card, .geek-card').length > 0
    );
    return !isSimilarRecommendItem;
  }};
  const primaryCards = Array.from(doc.querySelectorAll('li.card-item')).filter((el) => isVisible(el) && isMainCandidateCard(el));
  const cards = (primaryCards.length ? primaryCards : Array.from(doc.querySelectorAll('[data-geekid], [data-geek-id], .geek-card, .recommend-card, .candidate-card')))
    .filter((el) => isVisible(el) && isMainCandidateCard(el))
    .sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  const seen = new Set();
  const candidates = [];
  cards.forEach((card) => {{
    const item = parseCard(card, candidates.length);
    const key = item.candidateId + ':' + item.candidateName + ':' + item.detailTextPreview.slice(0, 60);
    if (!seen.has(key) && item.candidateName) {{
      seen.add(key);
      candidates.push(item);
    }}
  }});
  return JSON.stringify({{
    title: document.title,
    url: location.href,
    frameUrl: frame?.src || '',
    count: candidates.slice(0, {max_count}).length,
    candidates: candidates.slice(0, {max_count})
  }});
}})()
""".strip()


def build_capture_recommend_resume_cards_expression(max_scrolls: int) -> str:
    return f"""
(async () => {{
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame && frame.contentDocument ? frame.contentDocument : document;
  const scroller = doc.scrollingElement || doc.body;
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const hasSalaryLine = (line) => /(?:\\d+-\\d+K|闈㈣)/.test(line || '');
  const looksLikeSectionTitle = (line) => /涓轰綘鎺ㄨ崘|褰撳墠鑱屼綅|鐗涗汉/.test(line || '');
  const degreeLine = (lines) => lines.find((line) =>
    line.includes('宀?) && ['鏈', '澶т笓', '纭曞＋', '鍗氬＋', '鐮旂┒鐢?].some((degree) => line.includes(degree))
  ) || '';
  const parseCard = (el, index) => {{
    const raw = text(el);
    const lines = raw.split('\\n').map((line) => line.trim()).filter(Boolean);
    let name = lines.find((line, idx) => idx > 0 && hasSalaryLine(lines[idx - 1]) && !looksLikeSectionTitle(line)) || lines[0] || '';
    return {{
      index,
      name,
      summary: degreeLine(lines),
      rawText: raw,
    }};
  }};
  const seen = new Map();
  function collect() {{
    const cards = Array.from(doc.querySelectorAll('li.card-item'));
    cards.forEach((card, index) => {{
      const item = parseCard(card, index);
      if (!item.rawText || item.rawText.length < 20) return;
      if (item.name.includes('褰撳墠鑱屼綅')) return;
      const key = [item.name, item.summary, item.rawText.slice(0, 120)].join('::');
      if (!seen.has(key)) seen.set(key, item);
    }});
  }}

  scroller.scrollTo(0, 0);
  await sleep(500);
  const scrollStates = [];
  for (let step = 0; step < {max_scrolls}; step += 1) {{
    collect();
    scrollStates.push({{
      scrollTop: scroller.scrollTop,
      scrollHeight: scroller.scrollHeight,
      clientHeight: scroller.clientHeight,
    }});
    if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 5) break;
    scroller.scrollTo(0, Math.min(scroller.scrollTop + Math.floor(scroller.clientHeight * 0.85), scroller.scrollHeight));
    await sleep(700);
  }}
  collect();
  return JSON.stringify({{
    title: document.title,
    url: location.href,
    frameUrl: frame?.src || '',
    count: seen.size,
    cards: Array.from(seen.values()),
    scrollStates,
  }});
}})()
""".strip()


def build_capture_candidate_resume_expression() -> str:
    return """
(() => {
  const text = (el) => el ? (el.innerText || '').trim() : '';
  const allText = document.body ? (document.body.innerText || '').trim() : '';
  const lines = allText.split('\\n').map((line) => line.trim()).filter(Boolean);
  const firstLineMatching = (patterns) => lines.find((line) => patterns.some((pattern) => pattern.test(line))) || '';
  const collect = (patterns) => lines.filter((line) => patterns.some((pattern) => pattern.test(line))).slice(0, 8);
  const name = text(document.querySelector('.name, .geek-name, .resume-name, [class*="name"]')) || lines[0] || '';
  const tags = Array.from(document.querySelectorAll('.tag, .label, .badge, [class*="tag"], [class*="label"]'))
    .map(text)
    .filter(Boolean)
    .slice(0, 20);
  return JSON.stringify({
    name,
    candidateName: name,
    expectedTitle: firstLineMatching([/鏈熸湜.*(鑱屼綅|宀椾綅|宸ヤ綔)/, /姹傝亴鎰忓悜/, /杩愯惀/, /缁忕悊/, /涓撳憳/]),
    expectedCity: firstLineMatching([/鏈熸湜.*鍩庡競/, /娣卞湷|骞垮窞|涓婃捣|鍖椾含|鏉窞|鎴愰兘|涓滆帪|浣涘北/]),
    yearsOfExperience: firstLineMatching([/缁忛獙/, /\\d+骞?]),
    education: firstLineMatching([/鏈|澶т笓|纭曞＋|鐮旂┒鐢焲鍗氬＋|瀛﹀巻/]),
    currentTitle: firstLineMatching([/褰撳墠鑱屼綅|鐜颁换|鐩墠|鍦ㄨ亴/]),
    skills: tags,
    workHighlights: collect([/澶╃尗|娣樺疂|浜笢|鎶栭煶|灏忕孩涔缇庡|涓姢|鍒嗛攢|娓犻亾|GMV|gmv|閿€鍞|澧為暱|鍥㈤槦|绠＄悊|鎶曟斁|鎺ㄥ箍/]),
    rawTextPreview: allText.slice(0, 2000),
    sourceUrl: location.href
  });
})()
""".strip()


def build_capture_recommend_detail_resume_expression() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const recommendFrame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const rootDocument = recommendFrame?.contentDocument || document;
  const overlay = Array.from(rootDocument.querySelectorAll(
    '.resume-common-dialog, .dialog-resume-full, .search-resume, [class*="resume"][class*="dialog"], [class*="resume"][class*="detail"]'
  )).filter(isVisible).sort((a, b) => text(b).length - text(a).length)[0];
  const root = overlay || rootDocument.body || document.body;
  const allText = root ? text(root) : '';
  const lines = allText.split('\n').map((line) => line.trim()).filter(Boolean);
  const firstLineMatching = (patterns) => lines.find((line) => patterns.some((pattern) => pattern.test(line))) || '';
  const collect = (patterns) => lines.filter((line) => patterns.some((pattern) => pattern.test(line))).slice(0, 8);
  const name = text(root.querySelector?.('.name, .geek-name, .resume-name, [class*="name"]')) || lines[0] || '';
  const tags = Array.from(root.querySelectorAll?.('.tag, .label, .badge, [class*="tag"], [class*="label"]') || [])
    .map(text)
    .filter(Boolean)
    .slice(0, 20);
  return JSON.stringify({
    name,
    candidateName: name,
    expectedTitle: firstLineMatching([/鏈熸湜.*(鑱屼綅|宀椾綅|宸ヤ綔)/, /姹傝亴鎰忓悜/, /杩愯惀/, /缁忕悊/, /涓撳憳/]),
    expectedCity: firstLineMatching([/鏈熸湜.*鍩庡競/, /娣卞湷|骞垮窞|涓婃捣|鍖椾含|鏉窞|鎴愰兘|涓滆帪|浣涘北/]),
    yearsOfExperience: firstLineMatching([/缁忛獙/, /\d+骞?]),
    education: firstLineMatching([/鏈|澶т笓|纭曞＋|鐮旂┒鐢焲鍗氬＋|瀛﹀巻/]),
    currentTitle: firstLineMatching([/褰撳墠鑱屼綅|鐜颁换|鐩墠|鍦ㄨ亴/]),
    skills: tags,
    workHighlights: collect([/澶╃尗|娣樺疂|浜笢|鎶栭煶|灏忕孩涔缇庡|涓姢|鍒嗛攢|娓犻亾|GMV|gmv|閿€鍞|澧為暱|鍥㈤槦|绠＄悊|鎶曟斁|鎺ㄥ箍/]),
    rawTextPreview: allText.slice(0, 6000),
    sourceUrl: rootDocument.location?.href || location.href,
    detailOpen: Boolean(overlay)
  });
})()
""".strip()


def build_capture_recommend_detail_resume_expression_v3() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const frame = Array.from(document.querySelectorAll('iframe[src*="/web/frame/recommend"]')).find(isVisible);
  const doc = frame?.contentDocument || document;
  const overlay = Array.from(doc.querySelectorAll(
    '.dialog-wrap.active, .boss-popup__wrapper, .boss-dialog__body, .resume-common-dialog, .search-resume, .new-resume-online-main-ui, .lib-standard-resume, .resume-layout-wrap, .resume-right-side, .resume-detail-wrap'
  )).filter(isVisible).sort((a, b) => text(b).length - text(a).length)[0];
  const rightSide = doc.querySelector('.resume-right-side, .resume-simple-box, .resume-item-detail');
  const centerSide = doc.querySelector('.resume-center-side, .resume-detail-wrap');
  const hasDetail = Boolean(overlay || (rightSide && isVisible(rightSide)));
  if (!hasDetail) {
    return JSON.stringify({
      name: '',
      candidateName: '',
      expectedTitle: '',
      expectedCity: '',
      yearsOfExperience: '',
      education: '',
      currentTitle: '',
      skills: [],
      workHighlights: [],
      rawTextPreview: '',
      sourceUrl: doc.location?.href || location.href,
      detailOpen: false,
      reason: 'detail_not_open'
    });
  }
  const root = (rightSide && isVisible(rightSide) && text(rightSide).length > 40 ? rightSide : overlay);
  const actionLine = /^(鏀惰棌|杞彂|涓炬姤|涓嶅悎閫倈鎵撴嫑鍛紎绔嬪嵆娌熼€殀鑱旂郴|璇㈤棶Ta)$/;
  const sectionStop = /^(鍚堜綔涓撲韩|鍚屼簨娌熼€氳繘搴鎴戠殑娌熼€氳繘搴鍏朵粬鍚嶄紒澶у巶缁忓巻鐗涗汉|鎺ㄨ崘鐗涗汉)$/;
  const cleanLines = (value) => {
    const lines = String(value || '').split('\n').map((line) => line.trim()).filter(Boolean);
    const cleaned = [];
    for (const line of lines) {
      if (sectionStop.test(line)) break;
      if (actionLine.test(line)) continue;
      cleaned.push(line);
    }
    return cleaned.join('\n');
  };
  const rootText = cleanLines(text(root));
  const centerText = centerSide && isVisible(centerSide) ? cleanLines(text(centerSide)) : '';
  const centerLooksLikeOtherCandidates = /鍏朵粬鍚嶄紒澶у巶缁忓巻鐗涗汉|鎺ㄨ崘鐗涗汉/.test(centerText);
  const centerLooksLikeCurrentResume =
    /鏈熸湜鑱屼綅|宸ヤ綔缁忓巻|鏁欒偛缁忓巻|椤圭洰缁忓巻|涓汉浼樺娍|姹傝亴鎰忓悜/.test(centerText)
    && !centerLooksLikeOtherCandidates;
  const currentCardText = (() => {
    const rootLines = rootText.split('\n').map((line) => line.trim()).filter(Boolean);
    const signalLines = rootLines.filter((line) =>
      line.length >= 3
      && !/^(缁忓巻姒傝|鏀惰棌|杞彂|涓炬姤|涓嶅悎閫倈鎵撴嫑鍛紎绔嬪嵆娌熼€殀鑱旂郴|璇㈤棶Ta)$/.test(line)
      && !/^\d{4}[.\-\/\u5e74]/.test(line)
      && !/^\d+\u5e74/.test(line)
    );
    const cards = Array.from(doc.querySelectorAll('.card-item, .candidate-card-wrap, .card-inner'))
      .filter(isVisible)
      .map((el) => {
        const value = cleanLines(text(el));
        const score = signalLines.reduce((sum, signal) => sum + (value.includes(signal) ? 1 : 0), 0);
        const rect = el.getBoundingClientRect();
        return { value, score, top: rect.top };
      })
      .filter((item) => item.value && item.value.length > 40 && !/\u4e3a\u4f60\u63a8\u8350/.test(item.value));
    cards.sort((a, b) => b.score - a.score || Math.abs(a.top) - Math.abs(b.top));
    const matched = cards.find((item) => item.score > 0) || cards[0];
    return matched ? matched.value : '';
  })();
  const combinedText = [
    centerLooksLikeCurrentResume ? centerText : '',
    currentCardText,
    rootText,
  ].filter(Boolean).join('\n');
  const lines = combinedText.split('\n').map((line) => line.trim()).filter(Boolean);
  const firstLineMatching = (patterns) => lines.find((line) => patterns.some((pattern) => pattern.test(line))) || '';
  const collect = (patterns) => lines.filter((line) => patterns.some((pattern) => pattern.test(line))).slice(0, 12);
  const badName = /^(鏀惰棌|宸叉敹钘弢杞彂|涓炬姤|涓嶅悎閫倈鎵撴嫑鍛紎缁忓巻姒傝|鎺ㄨ崘|鏈€鏂皘娣卞湷)$/;
  const name = text(root.querySelector?.('.name, .geek-name, .resume-name'))
    || lines.find((line) => line && /[鍏堢敓濂冲＋]$/.test(line) && !badName.test(line) && !/鍏徃|澶у|瀛﹂櫌|瀛︽牎|\d|K|k|宀亅骞磡鏈熸湜|浼樺娍|娲昏穬/.test(line))
    || '';
  const tags = Array.from(root.querySelectorAll?.('.tag, .label, .badge, [class*="tag"], [class*="label"]') || [])
    .map(text)
    .filter(Boolean)
    .slice(0, 20);
  return JSON.stringify({
    name,
    candidateName: name,
    expectedTitle: firstLineMatching([/\u671f\u671b.*(\u804c\u4f4d|\u5c97\u4f4d|\u5de5\u4f5c)/, /\u6c42\u804c\u610f\u5411/, /\u8fd0\u8425/, /\u7ecf\u7406/, /\u4e13\u5458/]),
    expectedCity: firstLineMatching([/\u671f\u671b.*\u57ce\u5e02/, /^(娣卞湷|骞垮窞|涓婃捣|鍖椾含|鏉窞|鎴愰兘|涓滆帪|浣涘北)$/]),
    yearsOfExperience: firstLineMatching([/\u7ecf\u9a8c/, /\d+\u5e74/]),
    education: firstLineMatching([/\u672c\u79d1|\u5927\u4e13|\u7855\u58eb|\u7814\u7a76\u751f|\u535a\u58eb|\u5b66\u5386/]),
    currentTitle: firstLineMatching([/\u5f53\u524d\u804c\u4f4d|\u73b0\u4efb|\u76ee\u524d|\u5728\u804c/]),
    skills: tags,
    workHighlights: collect([/\u5929\u732b|\u6dd8\u5b9d|\u4eac\u4e1c|\u6296\u97f3|\u5c0f\u7ea2\u4e66|\u7f8e\u5986|\u4e2a\u62a4|\u5206\u9500|\u6e20\u9053|GMV|gmv|\u9500\u552e\u989d|\u589e\u957f|\u56e2\u961f|\u7ba1\u7406|\u6295\u653e|\u63a8\u5e7f|\u6d3b\u52a8/]),
    rawTextPreview: combinedText.slice(0, 6000),
    sourceUrl: doc.location?.href || location.href,
    detailOpen: true
  });
})()
""".strip()


def build_capture_recommend_canvas_data_url_expression() -> str:
    return r"""
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const frame = Array.from(document.querySelectorAll('iframe[src*="/web/frame/recommend"]')).find(isVisible);
  const doc = frame?.contentDocument || document;
  const resumeFrame = Array.from(doc.querySelectorAll('iframe[src*="/web/frame/c-resume"]')).find(isVisible);
  const resumeDoc = resumeFrame?.contentDocument;
  const canvas = resumeDoc?.querySelector('canvas#resume') || resumeDoc?.querySelector('canvas');
  if (!canvas) {
    return JSON.stringify({
      found: false,
      frameFound: Boolean(resumeFrame),
      frameSrc: resumeFrame?.getAttribute('src') || ''
    });
  }
  try {
    const rect = canvas.getBoundingClientRect();
    return JSON.stringify({
      found: true,
      readable: true,
      width: canvas.width,
      height: canvas.height,
      cssWidth: rect.width,
      cssHeight: rect.height,
      dataUrl: canvas.toDataURL('image/png')
    });
  } catch (error) {
    return JSON.stringify({
      found: true,
      readable: false,
      error: String(error)
    });
  }
})()
""".strip()


def build_capture_recommend_canvas_scrolled_data_urls_expression(max_segments: int = 6) -> str:
    return f"""
(async () => {{
  const maxSegments = {int(max_segments)};
  const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }};
  const frame = Array.from(document.querySelectorAll('iframe[src*="/web/frame/recommend"]')).find(isVisible);
  const doc = frame?.contentDocument || document;
  let resumeFrame = null;
  let resumeDoc = null;
  let canvas = null;
  for (let attempt = 0; attempt < 24; attempt += 1) {{
    resumeFrame = Array.from(doc.querySelectorAll('iframe[src*="/web/frame/c-resume"]')).find(isVisible);
    resumeDoc = resumeFrame?.contentDocument;
    canvas = resumeDoc?.querySelector('canvas#resume') || resumeDoc?.querySelector('canvas');
    const rootText = String(doc.querySelector('.resume-detail-wrap')?.innerText || doc.body?.innerText || '');
    const loading = rootText.includes('\u6b63\u5728\u52a0\u8f7d') || rootText.toLowerCase().includes('loading');
    if (canvas && canvas.width > 100 && canvas.height > 100 && !loading) break;
    await delay(350);
  }}
  const scrollableScore = (el) => el ? Math.max(0, (el.scrollHeight || 0) - (el.clientHeight || 0)) : -1;
  const parentScrollCandidates = Array.from(doc.querySelectorAll(
    '.iframe-resume-detail, .resume-detail-chat, .resume-detail, .resume-detail-wrap, .resume-layout-wrap, .dialog-wrap.active, .boss-dialog__body, .resume-center-side, .resume-item-detail, div'
  ))
    .filter(isVisible)
    .filter((el) => !resumeFrame || el.contains(resumeFrame))
    .filter((el) => scrollableScore(el) > 40)
    .sort((a, b) => scrollableScore(b) - scrollableScore(a));
  const parentScrollContainer = parentScrollCandidates[0] || null;
  const resumeScrollCandidates = resumeDoc
    ? [
        ...Array.from(resumeDoc.querySelectorAll('.resume-detail-wrap, .resume-layout-wrap, .resume-center-side, .resume-item-detail, .lib-standard-resume, main, section, article, div'))
          .filter(isVisible),
        resumeDoc.scrollingElement,
        resumeDoc.documentElement,
        resumeDoc.body
      ].filter(Boolean).filter((el) => scrollableScore(el) > 40).sort((a, b) => scrollableScore(b) - scrollableScore(a))
    : [];
  const resumeScrollContainer = resumeScrollCandidates[0] || null;
  const scrollContainer = scrollableScore(parentScrollContainer) >= scrollableScore(resumeScrollContainer)
    ? parentScrollContainer
    : resumeScrollContainer;
  const scrollWindow = scrollContainer?.ownerDocument?.defaultView || resumeDoc?.defaultView || doc.defaultView || window;
  if (!canvas) {{
    return JSON.stringify({{
      found: false,
      frameFound: Boolean(resumeFrame),
      frameSrc: resumeFrame?.getAttribute('src') || ''
    }});
  }}
  const capture = (scrollTop) => {{
    try {{
      const rect = canvas.getBoundingClientRect();
      return {{
        status: 'captured',
        found: true,
        readable: true,
        scrollTop,
        width: canvas.width,
        height: canvas.height,
        cssWidth: rect.width,
        cssHeight: rect.height,
        dataUrl: canvas.toDataURL('image/png')
      }};
    }} catch (error) {{
      return {{
        status: 'capture_failed',
        found: true,
        readable: false,
        scrollTop,
        error: String(error)
      }};
    }}
  }};
  if (!scrollContainer) {{
    return JSON.stringify({{
      found: true,
      reason: 'scroll_container_not_found',
      segments: [capture(null)],
      frameSrc: resumeFrame?.getAttribute('src') || ''
    }});
  }}
  const originalScrollTop = scrollContainer.scrollTop;
  const maxScrollTop = Math.max(0, scrollContainer.scrollHeight - scrollContainer.clientHeight);
  const step = Math.max(160, Math.floor(scrollContainer.clientHeight * 0.75));
  const positions = [];
  for (let top = 0; top <= maxScrollTop && positions.length < maxSegments; top += step) {{
    positions.push(Math.min(maxScrollTop, top));
  }}
  if (positions.length === 0) positions.push(0);
  if (positions[positions.length - 1] !== maxScrollTop && positions.length < maxSegments) {{
    positions.push(maxScrollTop);
  }}
  const uniquePositions = [...new Set(positions)];
  const segments = [];
  for (const top of uniquePositions) {{
    scrollContainer.scrollTop = top;
    scrollContainer.dispatchEvent(new Event('scroll', {{ bubbles: true }}));
    scrollWindow?.dispatchEvent?.(new Event('scroll'));
    if (typeof scrollWindow?.scrollTo === 'function' && scrollContainer === (resumeDoc?.scrollingElement || resumeDoc?.documentElement || resumeDoc?.body)) {{
      scrollWindow.scrollTo(0, top);
    }}
    await delay(1000);
    segments.push(capture(scrollContainer.scrollTop));
  }}
  scrollContainer.scrollTop = originalScrollTop;
  scrollContainer.dispatchEvent(new Event('scroll', {{ bubbles: true }}));
  if (typeof scrollWindow?.scrollTo === 'function' && scrollContainer === (resumeDoc?.scrollingElement || resumeDoc?.documentElement || resumeDoc?.body)) {{
    scrollWindow.scrollTo(0, originalScrollTop);
  }}
  await delay(300);
  if (Math.abs((scrollContainer.scrollTop || 0) - originalScrollTop) > 2) {{
    scrollContainer.scrollTop = originalScrollTop;
    scrollContainer.dispatchEvent(new Event('scroll', {{ bubbles: true }}));
    await delay(300);
  }}
  return JSON.stringify({{
    found: true,
    frameSrc: resumeFrame?.getAttribute('src') || '',
    scrollContainer: {{
      selector: scrollContainer.matches?.('.iframe-resume-detail') ? '.iframe-resume-detail'
        : scrollContainer.matches?.('.resume-detail-chat') ? '.resume-detail-chat'
        : scrollContainer.matches?.('.resume-detail-wrap') ? '.resume-detail-wrap'
        : '',
      document: scrollContainer.ownerDocument === resumeDoc ? 'resumeFrame' : 'parent',
      originalScrollTop,
      restoredScrollTop: scrollContainer.scrollTop,
      scrollHeight: scrollContainer.scrollHeight,
      clientHeight: scrollContainer.clientHeight,
      maxScrollTop,
      step,
      positions: uniquePositions
    }},
    segments
  }});
}})()
""".strip()


def build_recommend_resume_canvas_ready_expression() -> str:
    return r"""
(() => {
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const frame = Array.from(document.querySelectorAll('iframe[src*="/web/frame/recommend"]')).find(isVisible);
  const doc = frame?.contentDocument || document;
  const root = doc.querySelector('.resume-detail-wrap, .resume-layout-wrap, .dialog-wrap.active') || doc.body;
  const rootText = String(root?.innerText || root?.textContent || '').trim();
  const resumeFrame = Array.from(doc.querySelectorAll('iframe[src*="/web/frame/c-resume"]')).find(isVisible);
  const resumeDoc = resumeFrame?.contentDocument;
  const canvas = resumeDoc?.querySelector('canvas#resume') || resumeDoc?.querySelector('canvas');
  const loading = rootText.includes('\u6b63\u5728\u52a0\u8f7d') || rootText.toLowerCase().includes('loading');
  const rect = canvas ? canvas.getBoundingClientRect() : null;
  let dataUrlLength = 0;
  let signature = '';
  try {
    if (canvas && canvas.width > 0 && canvas.height > 0) {
      const dataUrl = canvas.toDataURL('image/png');
      dataUrlLength = dataUrl.length;
      signature = `${canvas.width}x${canvas.height}:${dataUrl.length}:${dataUrl.slice(-96)}`;
    }
  } catch (error) {
    return JSON.stringify({
      ready: false,
      reason: 'canvas_unreadable',
      error: String(error),
      frameFound: Boolean(resumeFrame),
      loading
    });
  }
  return JSON.stringify({
    ready: Boolean(canvas && canvas.width > 100 && canvas.height > 100 && dataUrlLength > 10000 && !loading),
    reason: !canvas ? 'canvas_not_found' : loading ? 'detail_loading' : dataUrlLength <= 10000 ? 'canvas_too_small' : '',
    frameFound: Boolean(resumeFrame),
    frameSrc: resumeFrame?.getAttribute('src') || '',
    canvasFound: Boolean(canvas),
    width: canvas?.width || 0,
    height: canvas?.height || 0,
    cssWidth: rect?.width || 0,
    cssHeight: rect?.height || 0,
    dataUrlLength,
    loading,
    textPreview: rootText.slice(0, 160),
    signature
  });
})()
""".strip()


def build_recommend_detail_greet_state_expression() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const overlay = Array.from(document.querySelectorAll(
    '.resume-common-dialog, .dialog-resume-full, .search-resume, [class*="resume"][class*="dialog"], [class*="resume"][class*="detail"]'
  )).filter(isVisible).sort((a, b) => text(b).length - text(a).length)[0] || document;
  const buttons = Array.from(overlay.querySelectorAll('button, a, .btn, span, div'))
    .filter((el) => isVisible(el) && text(el).length <= 20);
  const statusNode = buttons.find((el) => /宸叉墦鎷涘懠|缁х画娌熼€殀宸叉矡閫?.test(text(el)));
  const greetNode = buttons.find((el) => /鎵撴嫑鍛紎绔嬪嵆娌熼€殀娌熼€殀鑱旂郴/.test(text(el)));
  const status = text(statusNode || greetNode);
  return JSON.stringify({
    greeted: Boolean(statusNode) || (status && !/鎵撴嫑鍛紎绔嬪嵆娌熼€殀鑱旂郴/.test(status)),
    status,
    detailOpen: overlay !== document
  });
})()
""".strip()


def build_recommend_detail_open_state_expression() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const overlay = Array.from(document.querySelectorAll(
    '.resume-common-dialog, .dialog-resume-full, .search-resume, [class*="resume"][class*="dialog"], [class*="resume"][class*="detail"]'
  )).filter(isVisible).sort((a, b) => text(b).length - text(a).length)[0];
  return JSON.stringify({
    detailOpen: Boolean(overlay),
    textPreview: overlay ? text(overlay).slice(0, 240) : ''
  });
})()
""".strip()


def build_recommend_detail_open_state_expression_v2() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const overlay = Array.from(doc.querySelectorAll(
    '.dialog-wrap.active, .boss-popup__wrapper, .boss-dialog__body, .lib-standard-resume, .resume-layout-wrap, .resume-right-side, .resume-detail-wrap'
  )).filter(isVisible).sort((a, b) => text(b).length - text(a).length)[0];
  return JSON.stringify({
    detailOpen: Boolean(overlay),
    textPreview: overlay ? text(overlay).slice(0, 240) : '',
    capturedFromFrame: Boolean(frame)
  });
})()
""".strip()


def build_click_recommend_detail_greet_expression() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const overlay = Array.from(document.querySelectorAll(
    '.resume-common-dialog, .dialog-resume-full, .search-resume, [class*="resume"][class*="dialog"], [class*="resume"][class*="detail"]'
  )).filter(isVisible).sort((a, b) => text(b).length - text(a).length)[0] || document;
  const candidates = Array.from(overlay.querySelectorAll('button, a, .btn, span, div'))
    .filter((el) => isVisible(el) && /鎵撴嫑鍛紎绔嬪嵆娌熼€殀娌熼€殀鑱旂郴/.test(text(el)))
    .sort((a, b) => {
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return (ar.width * ar.height) - (br.width * br.height);
    });
  const button = candidates.find((el) => el.matches?.('button.btn-greet'))
    || candidates.find((el) => text(el) === '\u6253\u62db\u547c' && el.tagName === 'BUTTON')
    || candidates.find((el) => el.querySelector?.('button.btn-greet'))?.querySelector('button.btn-greet')
    || candidates[0];
  if (!button) {
    return JSON.stringify({ greeted: false, clicked: false, reason: 'greet_button_not_found', detailOpen: overlay !== document });
  }
  const rect = button.getBoundingClientRect();
  return JSON.stringify({
    greeted: false,
    clicked: false,
    status: text(button),
    button: {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height
    },
    detailOpen: overlay !== document
  });
})()
""".strip()


def build_recommend_detail_greet_state_expression_v2() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const roots = Array.from(doc.querySelectorAll(
    '.resume-right-side, .dialog-footer, .dialog-wrap.active, .lib-standard-resume, .resume-layout-wrap, .boss-dialog__body'
  )).filter(isVisible);
  const root = roots.find((el) => text(el).includes('\u6253\u62db\u547c')) || roots[0] || doc;
  const buttons = Array.from(root.querySelectorAll('button.btn-greet, .button-chat-wrap.button-chat, .resumeGreet, button, a, span, div'))
    .filter((el) => isVisible(el))
    .filter((el) => text(el).length <= 20 || /resumeGreet|button-chat-wrap|btn-greet/.test(String(el.className || '')))
    .sort((a, b) => {
      const exact = Number(text(b) === '\u6253\u62db\u547c') - Number(text(a) === '\u6253\u62db\u547c');
      if (exact) return exact;
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return (ar.width * ar.height) - (br.width * br.height);
    });
  const statusNode = buttons.find((el) => /^\s*(\u5df2\u6253\u62db\u547c|\u7ee7\u7eed\u6c9f\u901a|\u5df2\u6c9f\u901a|\u6c9f\u901a\u4e2d)\s*$/.test(text(el)));
  const greetNode = buttons.find((el) => /^\s*(\u6253\u62db\u547c|\u7acb\u5373\u6c9f\u901a|\u8054\u7cfb)\s*$/.test(text(el)));
  const status = text(statusNode || greetNode);
  return JSON.stringify({
    greeted: Boolean(statusNode) || (Boolean(status) && !/\u6253\u62db\u547c|\u7acb\u5373\u6c9f\u901a|\u8054\u7cfb/.test(status)),
    status,
    detailOpen: roots.length > 0,
    foundGreetButton: Boolean(greetNode)
  });
})()
""".strip()


def build_dismiss_recommend_popup_cards_expression() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const isSimilarRecommendItem = (item) => {
    if (!item || !item.matches?.('li.card-item')) return false;
    const title = item.querySelector('div.title, .title');
    const titleText = text(title);
    const raw = text(item);
    const geekCardCount = item.querySelectorAll('div.geek-card, .geek-card').length;
    return geekCardCount > 0
      && (
        (titleText.includes('\u4e3a\u4f60\u63a8\u8350') && titleText.includes('\u76f8\u4f3c'))
        || (raw.includes('\u4e3a\u4f60\u63a8\u8350') && raw.includes('\u76f8\u4f3c'))
      );
  };

  const similarItems = Array.from(doc.querySelectorAll('li.card-item'))
    .filter(isVisible)
    .filter(isSimilarRecommendItem);
  const item = similarItems[0] || null;
  if (!item) {
    return JSON.stringify({
      skipped: false,
      dismissed: false,
      reason: 'no_similar_recommend_card_item'
    });
  }
  const closeTargets = similarItems
    .map((similarItem) => Array.from(similarItem.querySelectorAll(
      'i.close.iboss-close, .close.iboss-close, i.close, [class*="close"]'
    )).find(isVisible))
    .filter(Boolean);
  if (closeTargets.length) {
    const closedTitles = [];
    closeTargets.forEach((closeEl) => {
      const similarItem = closeEl.closest('li.card-item');
      closedTitles.push(text(similarItem?.querySelector('div.title, .title')));
      closeEl.click();
    });
    return JSON.stringify({
      skipped: true,
      dismissed: true,
      method: 'similar_recommend_close_click',
      reason: 'similar_recommend_card_item_closed',
      closedCount: closeTargets.length,
      title: closedTitles[0] || text(item.querySelector('div.title, .title')),
      closedTitles,
      geekCardCount: item.querySelectorAll('div.geek-card, .geek-card').length
    });
  }
  return JSON.stringify({
    skipped: false,
    dismissed: false,
    method: 'similar_recommend_close_missing',
    reason: 'similar_recommend_close_button_not_found',
    title: text(item.querySelector('div.title, .title')),
    geekCardCount: item.querySelectorAll('div.geek-card, .geek-card').length
  });
})()
""".strip()


def build_click_recommend_card_greet_expression(escaped_candidate_id: str, card_index: int) -> str:
    return f"""
(() => {{
  const candidateId = {escaped_candidate_id};
  const cardIndex = {int(card_index)};
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {{
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  }};
  const isSimilarRecommendItem = (el) => {{
    const item = el?.matches?.('li.card-item') ? el : el?.closest?.('li.card-item');
    if (!item) return false;
    const titleText = text(item.querySelector('div.title, .title'));
    const raw = text(item);
    const hasSimilarTitle = (titleText.includes('\u4e3a\u4f60\u63a8\u8350') && titleText.includes('\u76f8\u4f3c'))
      || (raw.includes('\u4e3a\u4f60\u63a8\u8350') && raw.includes('\u76f8\u4f3c'));
    return hasSimilarTitle && item.querySelectorAll('div.geek-card, .geek-card').length > 0;
  }};
  const isMainCandidateCard = (el) => text(el).length > 20 && !isSimilarRecommendItem(el);
  const primaryCards = Array.from(doc.querySelectorAll('li.card-item')).filter((el) => isVisible(el) && isMainCandidateCard(el));
  const cards = (primaryCards.length
    ? primaryCards
    : Array.from(doc.querySelectorAll('[data-geekid], [data-id], .candidate-card-wrap, .geek-card, .card-inner, li, [class*="card"]')).filter((el) => isVisible(el) && isMainCandidateCard(el))
  );
  const target = cards.find((el) => candidateId && (
      el.getAttribute('data-geekid') === candidateId
      || el.getAttribute('data-id') === candidateId
      || String(el.dataset?.geekid || el.dataset?.id || '') === candidateId
    ))
    || cards[cardIndex]
    || null;
  if (!target) return JSON.stringify({{ clicked: false, greeted: false, reason: 'card_not_found', cardIndex }});
  target.scrollIntoView({{ block: 'center', inline: 'center' }});
  const buttons = Array.from(target.querySelectorAll('button.btn-greet, .btn-greet, .button-chat-wrap.button-chat, .button-list, .button-list-wrap, button, a, [role="button"], span, div'))
    .filter(isVisible)
    .filter((el) => text(el) === '\u6253\u62db\u547c' || /btn-greet|button-chat|button-list/.test(String(el.className || '')))
    .sort((a, b) => {{
      const exactButton = Number(b.matches?.('button.btn-greet')) - Number(a.matches?.('button.btn-greet'));
      if (exactButton) return exactButton;
      const exactText = Number(text(b) === '\u6253\u62db\u547c') - Number(text(a) === '\u6253\u62db\u547c');
      if (exactText) return exactText;
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return (ar.width * ar.height) - (br.width * br.height);
    }});
  const button = buttons[0];
  if (!button) return JSON.stringify({{ clicked: false, greeted: false, reason: 'card_greet_button_not_found', cardIndex, cardText: text(target).slice(0, 240) }});
  const real = button.querySelector?.('button.btn-greet') || button;
  const rect = real.getBoundingClientRect();
  real.click();
  return JSON.stringify({{
    clicked: true,
    greeted: false,
    method: 'card_dom_click',
    status: text(real),
    cardIndex,
    button: {{
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height
    }}
  }});
}})()
""".strip()


def build_click_recommend_detail_greet_expression_v2() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const roots = Array.from(doc.querySelectorAll(
    '.resume-right-side, .dialog-footer, .dialog-wrap.active, .lib-standard-resume, .resume-layout-wrap, .boss-dialog__body'
  )).filter(isVisible);
  const root = roots.find((el) => text(el).includes('\u6253\u62db\u547c')) || roots[0] || doc;
  const candidates = Array.from(root.querySelectorAll('button.btn-greet, .button-chat-wrap.button-chat, .resumeGreet, button, a, span, div'))
    .filter((el) => isVisible(el))
    .filter((el) => text(el) === '\u6253\u62db\u547c' || /\u7acb\u5373\u6c9f\u901a|\u8054\u7cfb/.test(text(el)) || /resumeGreet|button-chat-wrap|btn-greet/.test(String(el.className || '')))
    .sort((a, b) => {
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      return (ar.width * ar.height) - (br.width * br.height);
    });
  const button = candidates[0];
  if (!button) {
    return JSON.stringify({ greeted: false, clicked: false, reason: 'greet_button_not_found', detailOpen: roots.length > 0 });
  }
  const rect = button.getBoundingClientRect();
  button.click();
  return JSON.stringify({
    greeted: false,
    clicked: true,
    method: 'dom_click',
    status: text(button),
    selector: button.tagName.toLowerCase() + (button.className ? '.' + String(button.className).trim().replace(/\s+/g, '.') : ''),
    button: {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height
    },
    detailOpen: roots.length > 0
  });
})()
""".strip()


def build_close_recommend_detail_expression() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = window.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const selectors = [
    '.boss-popup__close',
    '.resume-custom-close',
    '.dialog-resume-full .close',
    '.resume-common-dialog .close',
    '[class*="close"]'
  ];
  const nodes = selectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)));
  const button = nodes.find(isVisible)
    || Array.from(document.querySelectorAll('button, a, span, div'))
      .find((el) => isVisible(el) && ['x', 'X', '鍏抽棴'].includes(text(el)));
  if (!button) {
    return JSON.stringify({ closed: false, reason: 'close_button_not_found' });
  }
  const rect = button.getBoundingClientRect();
  return JSON.stringify({
    closed: false,
    selector: button.className || button.tagName,
    button: {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height
    }
  });
})()
""".strip()


def build_close_recommend_detail_expression_v2() -> str:
    return r"""
(() => {
  const text = (el) => el ? String(el.innerText || el.textContent || '').trim() : '';
  const isVisible = (el) => {
    if (!el) return false;
    const rect = el.getBoundingClientRect();
    const style = el.ownerDocument.defaultView.getComputedStyle(el);
    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
  };
  const frame = document.querySelector('iframe[src*="/web/frame/recommend"]');
  const doc = frame?.contentDocument || document;
  const detailCandidates = Array.from(doc.querySelectorAll(
    '.dialog-wrap.active, .boss-popup__wrapper, .boss-dialog__body, .lib-standard-resume, .resume-layout-wrap, .resume-right-side, .resume-detail-wrap'
  ))
    .filter(isVisible)
    .filter((el) => {
      const value = text(el);
      return value.length > 40 && /(\u6253\u62db\u547c|\u5de5\u4f5c\u7ecf\u5386|\u6559\u80b2\u7ecf\u5386|\u7ecf\u5386\u6982\u89c8|\u671f\u671b\u804c\u4f4d)/.test(value);
    })
    .sort((a, b) => text(b).length - text(a).length);
  const detail = detailCandidates[0] || null;
  if (!detail) {
    return JSON.stringify({ closed: false, reason: 'detail_not_open' });
  }
  const root = detail.closest('.dialog-wrap.active, .boss-popup__wrapper, .boss-dialog__body, .resume-layout-wrap')
    || detail;
  const selectors = [
    '.close-btn',
    '.boss-popup__close',
    '.resume-custom-close',
    '.dialog-resume-full .close',
    '.resume-common-dialog .close'
  ];
  const nodes = selectors.flatMap((selector) => Array.from(root.querySelectorAll(selector)));
  const button = nodes.find(isVisible)
    || Array.from(root.querySelectorAll('button, a, span, div'))
      .find((el) => isVisible(el) && ['x', 'X', '\u00d7', '\u5173\u95ed'].includes(text(el)));
  if (!button) {
    return JSON.stringify({ closed: false, reason: 'close_button_not_found' });
  }
  const rect = button.getBoundingClientRect();
  const viewWidth = doc.defaultView?.innerWidth || doc.documentElement.clientWidth || 0;
  const isTopRight = rect.top <= 48 && rect.left >= Math.max(0, viewWidth - 180);
  if (isTopRight) {
    return JSON.stringify({
      closed: false,
      reason: 'unsafe_top_right_close_button',
      selector: button.className || button.tagName,
      button: {
        x: rect.left + rect.width / 2,
        y: rect.top + rect.height / 2,
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height
      }
    });
  }
  button.click();
  return JSON.stringify({
    closed: true,
    method: 'dom_click',
    selector: button.className || button.tagName,
    button: {
      x: rect.left + rect.width / 2,
      y: rect.top + rect.height / 2,
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height
    }
  });
})()
""".strip()


def build_capture_conversation_expression() -> str:
    return """
(() => {
  const text = (selector) => {
    const el = document.querySelector(selector);
    return el ? (el.innerText || '').trim() : '';
  };
  const visibleText = (el) => el ? (el.innerText || '').trim() : '';
  const firstNonEmpty = (values) => values.find(Boolean) || '';
  const sidebarText = visibleText(document.querySelector('.chat-conversation'));
  const isHrPage = location.href.includes('/web/chat/index');

  const messages = Array.from(document.querySelectorAll('.message-item')).map((el) => {
    const selfNode = el.querySelector('.item-myself');
    const friendNode = el.querySelector('.item-friend');
    const systemNode = el.querySelector('.item-system, .item-resume');
    const direction = selfNode ? 'outbound' : friendNode ? 'inbound' : 'system';
    const time = firstNonEmpty([
      visibleText(el.querySelector('.message-time .time')),
      visibleText(el.querySelector('.item-time .time')),
    ]);
    const status = firstNonEmpty([
      visibleText(el.querySelector('.status')),
      visibleText(el.querySelector('.message-status')),
    ]);
    let body = '';
    if (selfNode || friendNode) {
      body = firstNonEmpty([
        visibleText((selfNode || friendNode).querySelector('.text')),
        visibleText((selfNode || friendNode).querySelector('span')),
      ]);
    } else if (systemNode) {
      body = visibleText(systemNode);
    } else {
      body = visibleText(el);
    }
    body = body
      .split('\\n')
      .map((line) => line.trim())
      .filter((line) => line && line !== time && line !== status)
      .join('\\n');
    return {
      direction,
      time,
      status,
      text: body
    };
  }).filter((item) => item.text);

  const candidateName = isHrPage
    ? firstNonEmpty([
        text('.chat-conversation .name'),
        text('.chat-conversation .resume-name'),
        sidebarText.split('\\n').map((line) => line.trim()).find(Boolean) || '',
      ])
    : text('.name-text');

  const jobTitle = firstNonEmpty([
    text('.position-name'),
    (sidebarText.match(/娌熼€氳亴浣嶏細\\s*(.+?)\\s*(?:\\n|$)/) || [])[1] || '',
  ]);

  const salary = isHrPage
    ? firstNonEmpty([
        (sidebarText.match(/(\\d+\\s*-\\s*\\d+\\s*[kK])/i) || [])[1] || '',
        text('.salary'),
      ])
    : text('.salary');

  const city = isHrPage
    ? firstNonEmpty([
        (sidebarText.match(/鏈熸湜锛歕\s*([^路\\n]+?)\\s*路/) || [])[1] || '',
        text('.city'),
      ])
    : text('.city');

  return JSON.stringify({
    title: document.title,
    url: location.href,
    candidateName,
    company: text('.user-info .base-info > span'),
    role: text('.user-info .base-title'),
    jobTitle,
    salary,
    city,
    sidebarText,
    candidateProfileText: sidebarText,
    profileTimeline: {
      rawText: sidebarText,
      workExperiences: [],
      educations: []
    },
    messages
  });
})()
""".strip()


def classify_page(page: dict[str, Any]) -> dict[str, Any]:
    url = page.get("url", "")
    title = page.get("title", "")

    platform = "unknown"
    page_type = "unknown"

    if "zhipin.com" in url:
        platform = "boss"
        if "/chat" in url:
            page_type = "chat"
        elif "recommend" in url:
            page_type = "candidate"
        elif "job" in url:
            page_type = "job"

    return {
        "title": title,
        "url": url,
        "type": page.get("type", ""),
        "platform": platform,
        "pageType": page_type,
    }
