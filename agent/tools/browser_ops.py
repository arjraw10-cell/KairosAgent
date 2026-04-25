from __future__ import annotations

import json
import os
import asyncio
from typing import Any, Optional
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Playwright
from playwright_stealth import Stealth

from agent.tooling import ToolContext, tool

# --- Internal Browser Management ---

async def _get_browser_state(context: ToolContext) -> dict[str, Any]:
    state = context.runtime_state.get("browser")
    if state is None or state.get("page").is_closed():
        # Cleanup old state if it exists but is broken
        if state:
            try:
                await state["playwright"].stop()
            except:
                pass

        p = await async_playwright().start()
        
        user_data_dir = os.getenv("CHROME_USER_DATA")
        executable_path = os.getenv("CHROME_EXECUTABLE")
        
        # User requested ALWAYS headed mode
        headless = False
        
        if user_data_dir:
            # Fix potential bell character (\a) or other escape issues from env vars
            user_data_dir = user_data_dir.replace('\a', '\\a').replace('\b', '\\b').replace('\f', '\\f').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t').replace('\v', '\\v')
            # Normalize path
            user_data_dir = os.path.normpath(user_data_dir)
            
            # Ensure the directory exists
            os.makedirs(user_data_dir, exist_ok=True)
            
            try:
                # Launch with a persistent context (allows using an existing Chrome profile)
                br_context = await p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    executable_path=executable_path,
                    headless=headless,
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720}
                )
                browser = None 
                page = br_context.pages[0] if br_context.pages else await br_context.new_page()
            except Exception as e:
                print(f"Warning: Failed to launch with persistent context: {e}. Falling back to standard launch.")
                browser = await p.chromium.launch(headless=headless, executable_path=executable_path)
                br_context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
                )
                page = await br_context.new_page()
        else:
            # Standard fresh launch
            browser = await p.chromium.launch(headless=headless)
            br_context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
            )
            page = await br_context.new_page()
        
        await Stealth().apply_stealth_async(page)
        
        state = {
            "playwright": p,
            "browser": browser,
            "context": br_context,
            "page": page
        }
        context.runtime_state["browser"] = state
    return state

async def _get_page(context: ToolContext) -> Page:
    state = await _get_browser_state(context)
    return state["page"]

# --- Tools ---

@tool(
    name="browser_navigate",
    description="Navigate the browser to a specific URL. Avoids 'networkidle' for better reliability.",
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to navigate to."}
        },
        "required": ["url"]
    }
)
async def browser_navigate(context: ToolContext, url: str) -> dict[str, Any]:
    page = await _get_page(context)
    response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    return {
        "url": page.url,
        "status": response.status if response else None,
        "title": await page.title()
    }

@tool(
    name="browser_click",
    description="Click an element on the page using a CSS selector.",
    input_schema={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS selector or Playwright selector (e.g. 'text=Login' or 'button.submit')."}
        },
        "required": ["selector"]
    }
)
async def browser_click(context: ToolContext, selector: str) -> dict[str, Any]:
    page = await _get_page(context)
    await page.wait_for_selector(selector, timeout=10000)
    await page.click(selector)
    return {"ok": True, "url": page.url}

@tool(
    name="browser_type",
    description="Type text into an input field designated by a selector.",
    input_schema={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "CSS/Playwright selector of the input field."},
            "text": {"type": "string", "description": "The text to type."},
            "press_enter": {"type": "boolean", "description": "Whether to press Enter after typing.", "default": False}
        },
        "required": ["selector", "text"]
    }
)
async def browser_type(context: ToolContext, selector: str, text: str, press_enter: bool = False) -> dict[str, Any]:
    page = await _get_page(context)
    await page.wait_for_selector(selector, timeout=10000)
    await page.fill(selector, "")
    await page.type(selector, text, delay=50)
    if press_enter:
        await page.keyboard.press("Enter")
    return {"ok": True}

@tool(
    name="browser_extract",
    description="Extract the text content or Markdown-like representation of the current page.",
    input_schema={
        "type": "object",
        "properties": {
            "format": {"type": "string", "enum": ["text", "markdown"], "default": "text"}
        }
    }
)
async def browser_extract(context: ToolContext, format: str = "text") -> dict[str, Any]:
    page = await _get_page(context)
    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")
    
    for script in soup(["script", "style"]):
        script.decompose()

    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned_text = "\n".join(lines)
    
    return {
        "url": page.url,
        "title": await page.title(),
        "content": cleaned_text[:20000]
    }

@tool(
    name="browser_snapshot",
    description="Get a structural snapshot of the page, including interactive elements (buttons, links, inputs).",
    input_schema={"type": "object", "properties": {}}
)
async def browser_snapshot(context: ToolContext) -> dict[str, Any]:
    page = await _get_page(context)
    
    elements = await page.evaluate("""
        () => {
            const results = [];
            const actionables = document.querySelectorAll('button, a, input, select, textarea, [role="button"]');
            actionables.forEach((el, index) => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).visibility !== 'hidden') {
                    results.push({
                        tag: el.tagName.toLowerCase(),
                        text: (el.innerText || el.value || el.placeholder || "").trim().substring(0, 50),
                        role: el.getAttribute('role') || '',
                        id: el.id,
                        className: el.className
                    });
                }
            });
            return results.slice(0, 100);
        }
    """)
    
    return {
        "url": page.url,
        "title": await page.title(),
        "interactive_elements": elements
    }
