from __future__ import annotations

import json
import os
import re
import inspect
import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncGenerator, Optional, Callable

from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse
import uvicorn
from pydantic import BaseModel

from agent.core import Agent
from agent.model import OpenAIChatModel
from agent.tooling import ToolContext, ToolRegistry
from agent import tools as builtin_tools
from agent.tools.skill_ops import load_skills

# --- Data Models ---

class GatewayAddress(BaseModel):
    platform: str
    session: Optional[str] = None
    user_id: Optional[str] = None
    channel_id: Optional[str] = None
    metadata: dict[str, Any] = {}

class GatewayRequest(BaseModel):
    address: GatewayAddress
    text: str
    resume: bool = False
    mode: str = "personalized"

@dataclass(frozen=True)
class GatewayResponse:
    text: str
    session_name: str
    resumed: bool
    usage: dict[str, int]
    total_usage: dict[str, int]
    transcript_path: Path

@dataclass(frozen=True)
class GatewayConfig:
    workspace_dir: Path
    agent_home_dir: Path
    provider: str
    model_name: str
    max_steps: int = 55
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    mode: str = "personalized"

@dataclass
class _GatewaySession:
    agent: Agent
    context: ToolContext
    session_name: str
    transcript_path: Path
    resumed: bool = False

# --- Helpers ---

def sanitize_session_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return cleaned or "session"

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Path):
        return str(value)
    return value

def provider_label(provider: str) -> str:
    if provider == "llama_cpp":
        return "llama.cpp"
    return provider

# --- Gateway Implementation ---

class AgentGateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self._sessions: dict[str, _GatewaySession] = {}

    @classmethod
    def from_args(
        cls,
        *,
        workspace_dir: Path,
        agent_home_dir: Path,
        max_steps: int,
        mode: str = "personalized",
    ) -> AgentGateway:
        settings_path = agent_home_dir / "settings.json"
        
        provider = "gemini"
        model_name = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-lite-preview")
        base_url = None
        # Always prioritize the "API_KEY" env var for security
        api_key = os.getenv("API_KEY")
        
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
                provider = settings.get("provider", provider)
                model_name = settings.get("model", model_name)
                # base_url from settings is fine
                base_url = settings.get("base_url", base_url)
                # If API_KEY wasn't in env, maybe it's in settings (though user wants it in env)
                if not api_key:
                    api_key = settings.get("api_key")
            except Exception as e:
                print(f"Failed to load settings.json: {e}")

        return cls(
            GatewayConfig(
                workspace_dir=workspace_dir,
                agent_home_dir=agent_home_dir,
                provider=provider,
                model_name=model_name,
                max_steps=max_steps,
                base_url=base_url,
                api_key=api_key,
                mode=mode,
            )
        )

    async def handle(self, request: GatewayRequest, on_event: Optional[Callable[[str, Any], None]] = None) -> GatewayResponse:
        session = self._get_session(request.address, resume=request.resume, mode=request.mode)
        
        def internal_callback(etype, data):
            if etype == "tool_start":
                print(f"[{data['agent']}] Executing tool: {data['tool']} with args: {json.dumps(data['arguments'])}")
            elif etype == "tool_end":
                print(f"[{data['agent']}] Tool {data['tool']} completed.")
            
            # Save at every step
            self._save_session(session)
            
            if on_event:
                on_event(etype, data)

        reply = await session.agent.ask(request.text, on_event=internal_callback)
        self._save_session(session)
        return GatewayResponse(
            text=reply,
            session_name=session.session_name,
            resumed=session.resumed,
            usage=dict(session.agent.last_usage),
            total_usage=dict(session.agent.total_usage),
            transcript_path=session.transcript_path,
        )

    async def handle_stream(self, request: GatewayRequest) -> AsyncGenerator[str, None]:
        event_queue = asyncio.Queue()

        def queue_callback(etype, data):
            event_queue.put_nowait({"type": etype, "data": data})

        task = asyncio.create_task(self.handle(request, on_event=queue_callback))
        
        try:
            while not task.done() or not event_queue.empty():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                    yield json.dumps(json_safe(event))
                except asyncio.TimeoutError:
                    continue
        finally:
            if not task.done():
                # Client disconnected before task finished
                self.interrupt_session(request.address, mode=request.mode)
        
        resp = await task
        yield json.dumps({"type": "final_response", "data": json_safe(resp.__dict__)})

    def session_path(self, session_name: str) -> Path:
        return self.config.agent_home_dir / "chats" / sanitize_session_name(session_name) / "transcript.json"

    def next_session_name(self) -> str:
        now = datetime.now()
        return now.strftime("%Y-%m-%d_%H-%M-%S")

    def latest_session_name(self) -> str | None:
        chats_dir = self.config.agent_home_dir / "chats"
        if not chats_dir.exists():
            return None
        
        session_dirs = [d for d in chats_dir.iterdir() if d.is_dir() and (d / "transcript.json").exists()]
        if not session_dirs:
            json_files = list(chats_dir.glob("*.json"))
            if json_files:
                return max(json_files, key=lambda p: p.stat().st_mtime).stem
            return None
            
        latest_dir = max(session_dirs, key=lambda p: p.stat().st_mtime)
        return latest_dir.name

    async def close(self) -> None:
        for session in self._sessions.values():
            browser_state = session.context.runtime_state.get("browser")
            if browser_state:
                try:
                    if browser_state["browser"]:
                        await browser_state["browser"].close()
                    await browser_state["playwright"].stop()
                except Exception:
                    pass
        self._sessions.clear()

    def interrupt_session(self, address: GatewayAddress, mode: str | None = None) -> bool:
        effective_mode = mode or self.config.mode
        session_name = self._resolve_session_name(address, resume=False) # resume=False to get the exact one if defined
        session_key = f"{effective_mode}:{session_name}"
        session = self._sessions.get(session_key)
        if session:
            session.agent.interrupted = True
            return True
        return False

    def _get_session(self, address: GatewayAddress, resume: bool, mode: str | None = None) -> _GatewaySession:
        effective_mode = mode or self.config.mode
        session_name = self._resolve_session_name(address, resume=resume)
        session_key = f"{effective_mode}:{session_name}"
        
        existing = self._sessions.get(session_key)
        if existing is not None:
            return existing

        session = self._create_session(session_name, mode=effective_mode)
        if resume:
            session.resumed = self._load_session(session)
        self._sessions[session_key] = session
        return session

    def _resolve_session_name(self, address: GatewayAddress, resume: bool) -> str:
        if address.session:
            name = sanitize_session_name(address.session)
            # If explicit name given and resume requested, check if it actually exists
            if resume and not self.session_path(name).parent.exists():
                # Try to fuzzy match common date format errors (e.g. 2026-4-23 vs 2026-04-23)
                if "-" in name:
                    parts = name.split("-")
                    if len(parts) >= 3:
                        # Attempt padding
                        try:
                            y, m, remainder = parts[0], parts[1], "-".join(parts[2:])
                            padded = f"{y}-{int(m):02d}-{remainder}"
                            # Re-split remainder to pad day if needed
                            r_parts = remainder.split("_")
                            if len(r_parts) >= 2:
                                d, time_part = r_parts[0], "_".join(r_parts[1:])
                                padded = f"{y}-{int(m):02d}-{int(d):02d}_{time_part}"
                                if self.session_path(padded).parent.exists():
                                    return padded
                        except Exception:
                            pass
                raise FileNotFoundError(f"Transcript folder not found for session: {name}")
            return name
        if resume:
            latest = self.latest_session_name()
            if latest is not None:
                return latest
            raise FileNotFoundError("No saved chats were found to resume.")
        if address.platform == "cli":
            return self.next_session_name()
        parts = [address.platform]
        if address.channel_id:
            parts.append(address.channel_id)
        if address.user_id:
            parts.append(address.user_id)
        return sanitize_session_name("_".join(parts))

    def _create_session(self, session_name: str, mode: str) -> _GatewaySession:
        context = ToolContext(
            root_dir=self.config.workspace_dir,
            agent_home_dir=self.config.agent_home_dir,
            runtime_state={},
        )
        registry = ToolRegistry(context)
        context.runtime_state["registry"] = registry
        
        if mode == "personalized":
            registry.register_module(builtin_tools, origin="base")
        else:
            customization_tools = {"update_memory", "update_preferences", "update_identity", "reload_tools"}
            for _, member in inspect.getmembers(builtin_tools):
                if callable(member) and hasattr(member, "__tool_spec__"):
                    spec = getattr(member, "__tool_spec__")
                    if spec.name not in customization_tools or spec.name == "reload_tools":
                        registry.register(member, origin="base")

        load_skills(registry, self.config.agent_home_dir / "skills")

        model = OpenAIChatModel(
            model_name=self.config.model_name,
            provider=self.config.provider,
            base_url=self.config.base_url,
            api_key=self.config.api_key,
        )
        agent_name = "MainAgent"
        agent = Agent(model=model, registry=registry, max_steps=self.config.max_steps, mode=mode, name=agent_name)
        return _GatewaySession(
            agent=agent,
            context=context,
            session_name=session_name,
            transcript_path=self.session_path(session_name),
        )

    def _save_session(self, session: _GatewaySession) -> None:
        session.transcript_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_name": session.session_name,
            "provider": provider_label(self.config.provider),
            "model": self.config.model_name,
            "mode": session.agent.mode,
            "max_steps": self.config.max_steps,
            "updated_at": utc_now(),
            "resumed": session.resumed,
            "messages": session.agent.messages,
            "token_totals": session.agent.total_usage,
        }
        session.transcript_path.write_text(
            json.dumps(json_safe(payload), indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

    def _load_session(self, session: _GatewaySession) -> bool:
        if not session.transcript_path.exists():
            return False
        payload = json.loads(session.transcript_path.read_text(encoding="utf-8"))
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return False
            
        total_usage = payload.get("token_totals")
        session.agent.load_session(messages, total_usage)
        
        resume_prompt = {
            "role": "user", 
            "content": "[SYSTEM MESSAGE: This is a resumed execution of a previously saved chat session. The environment, workspace variables, or codebase state may have been modified or changed since your last turn. Do not blindly assume old file contents or state; actively re-verify with tools if starting new work.]"
        }
        session.agent.messages.append(resume_prompt)
        
        return True

# --- FastAPI App ---

app = FastAPI(title="Kairos Gateway")
gateway: Optional[AgentGateway] = None

@app.post("/handle")
async def handle(request: GatewayRequest):
    async def event_generator():
        async for event in gateway.handle_stream(request):
            yield {"data": event}
    return EventSourceResponse(event_generator())

@app.post("/interrupt")
async def interrupt(request: GatewayRequest):
    ok = gateway.interrupt_session(request.address, mode=request.mode)
    return {"ok": ok}

@app.get("/config")
async def get_config():
    return {
        "model": gateway.config.model_name,
        "provider": gateway.config.provider,
        "workspace": str(gateway.config.workspace_dir),
        "agent_home": str(gateway.config.agent_home_dir),
    }

@app.get("/sessions/latest")
async def latest_session():
    return {"session": gateway.latest_session_name()}

@app.get("/sessions/next")
async def next_session():
    return {"session": gateway.next_session_name()}

class ModelUpdateRequest(BaseModel):
    model: str

@app.post("/model")
async def update_model(request: ModelUpdateRequest):
    gateway.config.model_name = request.model
    settings_path = gateway.config.agent_home_dir / "settings.json"
    settings = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    settings["model"] = request.model
    settings_path.write_text(json.dumps(settings, indent=2))
    return {"ok": True, "model": request.model}

def run_gateway():
    global gateway
    from dotenv import load_dotenv
    
    agent_home_dir = Path(__file__).resolve().parents[1]
    load_dotenv(agent_home_dir / ".env")
    
    workspace_dir = Path(os.getenv("AGENT_WORKSPACE", ".")).resolve()
    
    gateway = AgentGateway.from_args(
        workspace_dir=workspace_dir,
        agent_home_dir=agent_home_dir,
        max_steps=55
    )
    
    print(f"[*] Kairos Gateway starting...")
    print(f"[*] Workspace: {gateway.config.workspace_dir}")
    print(f"[*] Provider:  {gateway.config.provider}")
    print(f"[*] Model:     {gateway.config.model_name}")
    
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

if __name__ == "__main__":
    run_gateway()
