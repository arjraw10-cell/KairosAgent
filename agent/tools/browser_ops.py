from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Callable

from agent.tooling import ToolContext, tool


class _BrowserWorker:
    def __init__(self, headless: bool) -> None:
        self._headless = headless
        self._tasks: queue.Queue[tuple[str, Callable[[], Any]]] = queue.Queue()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="browser-worker", daemon=True)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._startup_error: Exception | None = None
        self._thread.start()
        self._ready.wait()
        if self._startup_error is not None:
            raise self._startup_error

    def _run(self) -> None:
        try:
            try:
                from playwright.sync_api import sync_playwright  # type: ignore
            except ImportError as exc:
                raise ImportError(
                    "playwright is not installed. Run: pip install -r requirements.txt and playwright install chromium"
                ) from exc

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self._headless)
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
        except Exception as exc:  # noqa: BLE001
            self._startup_error = exc
            self._ready.set()
            return

        self._ready.set()
        while True:
            kind, payload = self._tasks.get()
            if kind == "stop":
                break

            try:
                result = payload()
            except Exception as exc:  # noqa: BLE001
                setattr(payload, "_result", ("error", exc))
            else:
                setattr(payload, "_result", ("ok", result))

        self._shutdown()

    def _shutdown(self) -> None:
        for obj in (self._page, self._context, self._browser):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception:
                pass
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass

    @property
    def page(self) -> Any:
        return self._page

    def call(self, fn: Callable[[Any], Any]) -> Any:
        done = threading.Event()

        def task() -> Any:
            try:
                return fn(self._page)
            finally:
                done.set()

        self._tasks.put(("call", task))
        done.wait()
        status, value = getattr(task, "_result")
        if status == "error":
            raise value
        return value

    def stop(self) -> None:
        self._tasks.put(("stop", lambda: None))
        self._thread.join(timeout=5)


def _browser_state(context: ToolContext, headless: bool = False) -> dict[str, Any]:
    state = context.runtime_state.setdefault("browser", {})
    worker = state.get("worker")
    if worker is not None:
        existing_headless = state.get("headless", False)
        if existing_headless != headless:
            raise ValueError(
                "Browser session already exists with a different headless setting. "
                "Close the current browser session before changing it."
            )
        return state

    worker = _BrowserWorker(headless=headless)
    state["worker"] = worker
    state["headless"] = headless
    state["history"] = []
    state["next_id"] = 1
    state["snapshots"] = {}
    state["next_snapshot_id"] = 1
    return state


def _record_browser_action(state: dict[str, Any], action: str, details: dict[str, Any]) -> None:
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "action": action,
        "details": details,
    }
    state["history"].append(entry)
    max_history = 100
    if len(state["history"]) > max_history:
        state["history"] = state["history"][-max_history:]


def _with_page(context: ToolContext, fn: Callable[[Any, dict[str, Any]], Any], headless: bool = False) -> Any:
    state = _browser_state(context, headless=headless)
    worker: _BrowserWorker = state["worker"]
    return worker.call(lambda page: fn(page, state))


def _ensure_agent_ids(page: Any, state: dict[str, Any], max_elements: int) -> list[dict[str, Any]]:
    result = page.evaluate(
        r"""
        ({maxElements, nextId}) => {
          const isVisible = (el) => {
            if (!el) return false;
            const style = window.getComputedStyle(el);
            if (!style) return false;
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
          };

          const selectors = [
            'a[href]', 'button', 'input', 'select', 'textarea',
            '[role="button"]', '[role="link"]', '[role="textbox"]',
            '[onclick]', '[tabindex]'
          ];
          const all = Array.from(document.querySelectorAll(selectors.join(',')));
          const seen = new Set();
          const out = [];
          let idCounter = nextId;

          for (const el of all) {
            if (out.length >= maxElements) break;
            if (!isVisible(el)) continue;
            if (seen.has(el)) continue;
            seen.add(el);

            let agentId = el.getAttribute('data-agent-id');
            if (!agentId) {
              agentId = `ae-${idCounter++}`;
              el.setAttribute('data-agent-id', agentId);
            }

            const rect = el.getBoundingClientRect();
            const text = (el.innerText || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 160);
            out.push({
              element_id: agentId,
              tag: (el.tagName || '').toLowerCase(),
              role: el.getAttribute('role') || '',
              type: el.getAttribute('type') || '',
              name: el.getAttribute('name') || '',
              placeholder: el.getAttribute('placeholder') || '',
              aria_label: el.getAttribute('aria-label') || '',
              text,
              x: Math.round(rect.x),
              y: Math.round(rect.y),
              width: Math.round(rect.width),
              height: Math.round(rect.height),
              disabled: !!el.disabled
            });
          }
          return {elements: out, nextId: idCounter};
        }
        """,
        {"maxElements": max_elements, "nextId": state["next_id"]},
    )
    state["next_id"] = int(result["nextId"])
    return result["elements"]


def _target_selector(element_id: str | None, selector: str | None) -> str:
    if element_id:
        return f"[data-agent-id='{element_id}']"
    if selector:
        return selector
    raise ValueError("Provide either element_id or selector.")


@tool(
    name="browser_navigate",
    description=(
        "Navigate browser to a URL. Use this before scanning or interacting with page elements."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "headless": {"type": "boolean", "default": False},
            "wait_until": {
                "type": "string",
                "enum": ["load", "domcontentloaded", "networkidle"],
                "default": "domcontentloaded",
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
)
def browser_navigate(
    context: ToolContext,
    url: str,
    headless: bool = False,
    wait_until: str = "domcontentloaded",
) -> dict[str, Any]:
    def _navigate(page: Any, state: dict[str, Any]) -> dict[str, Any]:
        page.goto(url, wait_until=wait_until, timeout=45000)
        result = {"url": page.url, "title": page.title()}
        _record_browser_action(state, "navigate", {"url": url, "wait_until": wait_until})
        return result

    return _with_page(context, _navigate, headless=headless)


@tool(
    name="browser_scan",
    description=(
        "Return a compact page summary plus interactable elements. Use this first to save context. "
        "If exact deep structure is needed, call browser_snapshot."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "max_elements": {"type": "integer", "default": 60, "minimum": 1, "maximum": 200},
            "max_text_chars": {"type": "integer", "default": 1400, "minimum": 200, "maximum": 8000},
        },
        "additionalProperties": False,
    },
)
def browser_scan(
    context: ToolContext,
    max_elements: int = 60,
    max_text_chars: int = 1400,
) -> dict[str, Any]:
    def _scan(page: Any, state: dict[str, Any]) -> dict[str, Any]:
        elements = _ensure_agent_ids(page, state, max_elements=max_elements)
        visible_text = page.evaluate(
            r"""
            (maxChars) => {
              const body = document.body;
              if (!body) return '';
              const text = (body.innerText || '').replace(/\s+/g, ' ').trim();
              return text.slice(0, maxChars);
            }
            """,
            max_text_chars,
        )

        summary = {
            "url": page.url,
            "title": page.title(),
            "interactable_count": len(elements),
            "visible_text_preview": visible_text,
            "snapshot_hint": "Call browser_snapshot if you need full exact page structure.",
        }
        _record_browser_action(
            state,
            "scan",
            {"url": page.url, "elements": len(elements), "text_preview_chars": len(visible_text)},
        )
        return {"summary": summary, "elements": elements}

    return _with_page(context, _scan)


@tool(
    name="browser_click",
    description="Click an interactable element using its element_id from browser_scan.",
    input_schema={
        "type": "object",
        "properties": {
            "element_id": {"type": "string"},
            "timeout_ms": {"type": "integer", "default": 10000, "minimum": 1000},
        },
        "required": ["element_id"],
        "additionalProperties": False,
    },
)
def browser_click(context: ToolContext, element_id: str, timeout_ms: int = 10000) -> dict[str, Any]:
    def _click(page: Any, state: dict[str, Any]) -> dict[str, Any]:
        locator = page.locator(f"[data-agent-id='{element_id}']")
        locator.first.click(timeout=timeout_ms)
        _record_browser_action(state, "click", {"element_id": element_id})
        return {"clicked": element_id, "url": page.url, "title": page.title()}

    return _with_page(context, _click)


@tool(
    name="browser_type",
    description="Type text into an input-like element using element_id from browser_scan.",
    input_schema={
        "type": "object",
        "properties": {
            "element_id": {"type": "string"},
            "text": {"type": "string"},
            "press_enter": {"type": "boolean", "default": False},
            "timeout_ms": {"type": "integer", "default": 10000, "minimum": 1000},
        },
        "required": ["element_id", "text"],
        "additionalProperties": False,
    },
)
def browser_type(
    context: ToolContext,
    element_id: str,
    text: str,
    press_enter: bool = False,
    timeout_ms: int = 10000,
) -> dict[str, Any]:
    def _type(page: Any, state: dict[str, Any]) -> dict[str, Any]:
        locator = page.locator(f"[data-agent-id='{element_id}']").first
        locator.fill(text, timeout=timeout_ms)
        if press_enter:
            locator.press("Enter", timeout=timeout_ms)
        _record_browser_action(
            state,
            "type",
            {"element_id": element_id, "text_len": len(text), "press_enter": press_enter},
        )
        return {"typed": element_id, "text_len": len(text), "url": page.url}

    return _with_page(context, _type)


@tool(
    name="browser_wait_for",
    description=(
        "Wait for page readiness using one condition: load_state, selector/element_id, text, or url_contains."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "load_state": {
                "type": "string",
                "enum": ["load", "domcontentloaded", "networkidle"],
            },
            "element_id": {"type": "string"},
            "selector": {"type": "string"},
            "text": {"type": "string"},
            "url_contains": {"type": "string"},
            "timeout_ms": {"type": "integer", "default": 15000, "minimum": 500},
        },
        "additionalProperties": False,
    },
)
def browser_wait_for(
    context: ToolContext,
    load_state: str | None = None,
    element_id: str | None = None,
    selector: str | None = None,
    text: str | None = None,
    url_contains: str | None = None,
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    provided = [bool(load_state), bool(element_id or selector), bool(text), bool(url_contains)]
    if sum(1 for x in provided if x) != 1:
        raise ValueError(
            "Provide exactly one wait condition: load_state, selector/element_id, text, or url_contains."
        )

    def _wait(page: Any, state: dict[str, Any]) -> dict[str, Any]:
        if load_state:
            page.wait_for_load_state(load_state, timeout=timeout_ms)
            condition = {"load_state": load_state}
        elif element_id or selector:
            query = _target_selector(element_id, selector)
            page.wait_for_selector(query, timeout=timeout_ms, state="visible")
            condition = {"selector": query}
        elif text:
            page.locator(f"text={text}").first.wait_for(state="visible", timeout=timeout_ms)
            condition = {"text": text}
        else:
            deadline = time.time() + (timeout_ms / 1000.0)
            while time.time() < deadline:
                if url_contains and url_contains in page.url:
                    break
                page.wait_for_timeout(200)
            if not url_contains or url_contains not in page.url:
                raise TimeoutError(f"Timed out waiting for url containing: {url_contains}")
            condition = {"url_contains": url_contains}

        _record_browser_action(state, "wait_for", {"condition": condition, "timeout_ms": timeout_ms})
        return {"ready": True, "condition": condition, "url": page.url, "title": page.title()}

    return _with_page(context, _wait)


@tool(
    name="browser_extract",
    description=(
        "Extract content from a target element by element_id or CSS selector. "
        "Modes: text, html (innerHTML), outer_html, attributes."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "element_id": {"type": "string"},
            "selector": {"type": "string"},
            "mode": {"type": "string", "enum": ["text", "html", "outer_html", "attributes"], "default": "text"},
            "attribute_names": {"type": "array", "items": {"type": "string"}},
            "max_chars": {"type": "integer", "default": 8000, "minimum": 100, "maximum": 100000},
            "timeout_ms": {"type": "integer", "default": 10000, "minimum": 500},
        },
        "additionalProperties": False,
    },
)
def browser_extract(
    context: ToolContext,
    element_id: str | None = None,
    selector: str | None = None,
    mode: str = "text",
    attribute_names: list[str] | None = None,
    max_chars: int = 8000,
    timeout_ms: int = 10000,
) -> dict[str, Any]:
    query = _target_selector(element_id, selector)

    def _extract(page: Any, state: dict[str, Any]) -> dict[str, Any]:
        locator = page.locator(query).first
        locator.wait_for(state="attached", timeout=timeout_ms)

        if mode == "text":
            value: Any = locator.inner_text(timeout=timeout_ms)
        elif mode == "html":
            value = locator.inner_html(timeout=timeout_ms)
        elif mode == "outer_html":
            value = locator.evaluate("el => el.outerHTML")
        else:
            names = attribute_names or []
            if names:
                value = {
                    name: locator.get_attribute(name, timeout=timeout_ms)
                    for name in names
                }
            else:
                value = locator.evaluate(
                    """el => {
                      const attrs = {};
                      for (const attr of el.attributes) attrs[attr.name] = attr.value;
                      return attrs;
                    }"""
                )

        output: Any = value
        truncated = False
        if isinstance(value, str):
            output = value[:max_chars]
            truncated = len(value) > max_chars

        _record_browser_action(
            state,
            "extract",
            {
                "query": query,
                "mode": mode,
                "truncated": truncated,
                "max_chars": max_chars,
            },
        )
        return {
            "query": query,
            "mode": mode,
            "value": output,
            "truncated": truncated,
            "url": page.url,
        }

    return _with_page(context, _extract)


@tool(
    name="browser_snapshot",
    description=(
        "Get detailed page structure snapshot. Use only when browser_scan is insufficient, to avoid context bloat."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "format": {"type": "string", "enum": ["accessibility", "html"], "default": "accessibility"},
            "max_chars": {"type": "integer", "default": 12000, "minimum": 500, "maximum": 100000},
        },
        "additionalProperties": False,
    },
)
def browser_snapshot(
    context: ToolContext,
    format: str = "accessibility",
    max_chars: int = 12000,
) -> dict[str, Any]:
    def _snapshot(page: Any, state: dict[str, Any]) -> dict[str, Any]:
        if format == "accessibility":
            snapshot_obj = page.accessibility.snapshot()
            raw = json.dumps(snapshot_obj, ensure_ascii=True)
        else:
            raw = page.content()

        snapshot_id = f"snap-{state['next_snapshot_id']}"
        state["next_snapshot_id"] += 1
        state["snapshots"][snapshot_id] = raw

        content = raw[:max_chars]
        is_truncated = len(raw) > max_chars
        _record_browser_action(
            state,
            "snapshot",
            {
                "snapshot_id": snapshot_id,
                "format": format,
                "full_chars": len(raw),
                "returned_chars": len(content),
            },
        )
        return {
            "snapshot_id": snapshot_id,
            "format": format,
            "content": content,
            "truncated": is_truncated,
            "full_length": len(raw),
        }

    return _with_page(context, _snapshot)


@tool(
    name="browser_get_snapshot",
    description="Retrieve a previously captured snapshot by snapshot_id.",
    input_schema={
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string"},
            "max_chars": {"type": "integer", "default": 12000, "minimum": 500, "maximum": 100000},
        },
        "required": ["snapshot_id"],
        "additionalProperties": False,
    },
)
def browser_get_snapshot(context: ToolContext, snapshot_id: str, max_chars: int = 12000) -> dict[str, Any]:
    state = _browser_state(context)
    if snapshot_id not in state["snapshots"]:
        raise ValueError(f"Unknown snapshot_id: {snapshot_id}")
    raw = state["snapshots"][snapshot_id]
    content = raw[:max_chars]
    return {
        "snapshot_id": snapshot_id,
        "content": content,
        "truncated": len(raw) > max_chars,
        "full_length": len(raw),
    }


@tool(
    name="browser_history",
    description="Return recent browser actions for anti-spiral awareness.",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    },
)
def browser_history(context: ToolContext, limit: int = 20) -> dict[str, Any]:
    state = _browser_state(context)
    history = state["history"][-limit:]
    return {"count": len(history), "items": history}


def close_browser_session(context: ToolContext) -> None:
    state = context.runtime_state.get("browser")
    if not state:
        return
    worker: _BrowserWorker | None = state.get("worker")
    if worker is not None:
        try:
            worker.stop()
        except Exception:
            pass
    context.runtime_state.pop("browser", None)
