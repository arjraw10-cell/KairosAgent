from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    parse_error: str | None = None
    raw_arguments: str | None = None


@dataclass
class ModelResponse:
    content: str | None
    tool_calls: list[ToolCall]
    raw_assistant_message: dict[str, Any]
    usage: dict[str, int]


class OpenAIChatModel:
    def __init__(
        self,
        model_name: str,
        provider: str = "openai",
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.provider = provider.lower()
        self._gemini_explicit_cache_enabled = (
            os.getenv("GEMINI_EXPLICIT_CACHE", "true").strip().lower() == "true"
        )
        self._gemini_cache_ttl = os.getenv("GEMINI_CACHE_TTL", "300s").strip() or "300s"
        self._gemini_cache_min_chars = max(
            1, int(os.getenv("GEMINI_CACHE_MIN_CHARS", "12000") or "12000")
        )
        self._gemini_cache_name: str | None = None
        self._gemini_cache_key: str | None = None
        if self.provider in {"openai", "llama_cpp"}:
            try:
                from openai import OpenAI  # type: ignore
            except ImportError as exc:
                raise ImportError(
                    "openai package is not installed. Run: pip install -r requirements.txt"
                ) from exc
            client_kwargs: dict[str, Any] = {}
            if base_url:
                client_kwargs["base_url"] = base_url
            if api_key:
                client_kwargs["api_key"] = api_key
            self._client = OpenAI(**client_kwargs)
        elif self.provider == "gemini":
            try:
                from google import genai  # type: ignore
            except ImportError as exc:
                raise ImportError(
                    "google-genai package is not installed. Run: pip install -r requirements.txt"
                ) from exc
            gemini_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("API_KEY")
            if not gemini_key:
                raise ValueError("GEMINI_API_KEY or API_KEY is not set.")
            self._client = genai.Client(api_key=gemini_key)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def complete(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> ModelResponse:
        if self.provider in {"openai", "llama_cpp"}:
            return self._complete_openai(messages, tool_schemas)
        return self._complete_gemini(messages, tool_schemas)

    def _complete_openai(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> ModelResponse:
        request_kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "tools": tool_schemas if tool_schemas else None,
        }
        prompt_cache_key = self._build_openai_prompt_cache_key(messages, tool_schemas)
        if prompt_cache_key:
            request_kwargs["prompt_cache_key"] = prompt_cache_key
            request_kwargs["prompt_cache_retention"] = os.getenv(
                "OPENAI_PROMPT_CACHE_RETENTION", "24h"
            )

        response = self._client.chat.completions.create(
            **request_kwargs,
        )
        message = response.choices[0].message

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    if not isinstance(args, dict):
                        tool_calls.append(
                            ToolCall(
                                id=tc.id,
                                name=tc.function.name,
                                arguments={},
                                parse_error="Tool arguments must decode to a JSON object.",
                                raw_arguments=tc.function.arguments,
                            )
                        )
                        continue
                except json.JSONDecodeError as exc:
                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments={},
                            parse_error=f"Invalid JSON for tool arguments: {exc}",
                            raw_arguments=tc.function.arguments,
                        )
                    )
                    continue
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        raw_msg: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            raw_msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        usage_obj = getattr(response, "usage", None)
        prompt_tokens_details = getattr(usage_obj, "prompt_tokens_details", None)
        usage = {
            "prompt_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0),
            "cached_prompt_tokens": int(
                getattr(prompt_tokens_details, "cached_tokens", 0) or 0
            ),
        }
        return ModelResponse(
            content=message.content,
            tool_calls=tool_calls,
            raw_assistant_message=raw_msg,
            usage=usage,
        )

    def _complete_gemini(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> ModelResponse:
        from google.genai import types  # type: ignore

        system_instruction: str | None = None
        contents: list[Any] = []
        tool_name_by_call_id: dict[str, str] = {}

        for msg in messages:
            role = msg.get("role")
            if role == "system":
                if system_instruction is None:
                    system_instruction = str(msg.get("content", ""))
                continue

            if role == "user":
                contents.append(types.Content(role="user", parts=[types.Part(text=str(msg.get("content", "")))]))
                continue

            if role == "assistant":
                if "_gemini_content" in msg:
                    contents.append(msg["_gemini_content"])
                else:
                    parts: list[Any] = []
                    if msg.get("content"):
                        parts.append(types.Part(text=str(msg["content"])))
                    for tc in msg.get("tool_calls", []) or []:
                        name = tc["function"]["name"]
                        args = json.loads(tc["function"]["arguments"] or "{}")
                        parts.append(types.Part(function_call=types.FunctionCall(name=name, args=args)))
                        tool_name_by_call_id[tc["id"]] = name
                    if parts:
                        contents.append(types.Content(role="model", parts=parts))
                continue

            if role == "tool":
                tool_name = msg.get("name")
                if not tool_name:
                    call_id = msg.get("tool_call_id")
                    tool_name = tool_name_by_call_id.get(call_id)
                if not tool_name:
                    raise ValueError("Tool message missing tool name and cannot map from tool_call_id.")
                try:
                    tool_payload = json.loads(msg.get("content", "{}"))
                except json.JSONDecodeError:
                    tool_payload = {"raw": str(msg.get("content", ""))}
                part = types.Part.from_function_response(name=tool_name, response=tool_payload)
                contents.append(types.Content(role="tool", parts=[part]))
                continue

        declarations: list[types.FunctionDeclaration] = []
        for schema in tool_schemas:
            fn = schema.get("function", {})
            params = self._sanitize_gemini_schema(fn.get("parameters", {}))
            declarations.append(
                types.FunctionDeclaration(
                    name=fn.get("name", ""),
                    description=fn.get("description", ""),
                    parameters=params,
                )
            )

        config_kwargs: dict[str, Any] = {}
        if declarations:
            config_kwargs["tools"] = [types.Tool(function_declarations=declarations)]
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        request_contents = contents
        cached_content_name: str | None = None
        if self._gemini_explicit_cache_enabled:
            cache_candidate = self._prepare_gemini_cache(
                system_instruction=system_instruction,
                declarations=declarations,
                contents=contents,
            )
            if cache_candidate is not None:
                cached_content_name, request_contents = cache_candidate
                config_kwargs.pop("tools", None)
                config_kwargs.pop("system_instruction", None)
                config_kwargs["cached_content"] = cached_content_name

        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        response = self._client.models.generate_content(
            model=self.model_name,
            contents=request_contents,
            config=config,
        )

        function_calls = getattr(response, "function_calls", None) or []
        tool_calls: list[ToolCall] = []
        raw_tool_calls: list[dict[str, Any]] = []
        for idx, call in enumerate(function_calls):
            call_id = getattr(call, "id", None) or f"gemini-fc-{idx}"
            call_name = getattr(call, "name", "")
            call_args = dict(getattr(call, "args", {}) or {})
            tool_calls.append(ToolCall(id=call_id, name=call_name, arguments=call_args))
            raw_tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": call_name, "arguments": json.dumps(call_args)},
                }
            )

        model_content = None
        if getattr(response, "candidates", None):
            candidate = response.candidates[0]
            model_content = getattr(candidate, "content", None)

        response_text = self._extract_gemini_text(response)
        raw_msg: dict[str, Any] = {"role": "assistant", "content": response_text}
        if raw_tool_calls:
            raw_msg["tool_calls"] = raw_tool_calls
        if model_content is not None:
            if hasattr(model_content, "model_dump"):
                raw_msg["_gemini_content"] = model_content.model_dump(
                    mode="json",
                    exclude_none=True,
                )
            else:
                raw_msg["_gemini_content"] = self._json_safe_value(model_content)

        usage_meta = getattr(response, "usage_metadata", None)
        usage = {
            "prompt_tokens": int(getattr(usage_meta, "prompt_token_count", 0) or 0),
            "completion_tokens": int(getattr(usage_meta, "candidates_token_count", 0) or 0),
            "total_tokens": int(getattr(usage_meta, "total_token_count", 0) or 0),
            "cached_prompt_tokens": int(
                getattr(usage_meta, "cached_content_token_count", 0) or 0
            ),
        }

        return ModelResponse(
            content=response_text,
            tool_calls=tool_calls,
            raw_assistant_message=raw_msg,
            usage=usage,
        )

    def _sanitize_gemini_schema(self, node: Any) -> Any:
        if isinstance(node, dict):
            disallowed = {"additionalProperties", "default", "examples", "$schema", "$id"}
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key in disallowed:
                    continue
                out[key] = self._sanitize_gemini_schema(value)
            return out
        if isinstance(node, list):
            return [self._sanitize_gemini_schema(item) for item in node]
        return node

    def _build_openai_prompt_cache_key(
        self,
        messages: list[dict[str, Any]],
        tool_schemas: list[dict[str, Any]],
    ) -> str | None:
        if self.provider != "openai":
            return None
        payload = {
            "model": self.model_name,
            "messages": messages,
            "tools": tool_schemas,
        }
        serialized = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        if len(serialized) < 1024:
            return None
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        return f"agent:{self.model_name}:{digest}"

    def _prepare_gemini_cache(
        self,
        system_instruction: str | None,
        declarations: list[Any],
        contents: list[Any],
    ) -> tuple[str, list[Any]] | None:
        # We aim for a STABLE cache that covers the system instruction and tools.
        # This prevents recreating the cache every turn (which is slow and unreliable).
        # Conversation history is sent as live content.
        
        serialized_cache = self._serialize_gemini_cache_payload(
            system_instruction=system_instruction,
            declarations=declarations,
            contents=[], # Stable: do not include history in cache
        )
        
        if len(serialized_cache) < self._gemini_cache_min_chars:
            return None

        cache_key = hashlib.sha256(serialized_cache.encode("utf-8")).hexdigest()
        if self._gemini_cache_key != cache_key or not self._gemini_cache_name:
            self._replace_gemini_cache(
                cache_key=cache_key,
                system_instruction=system_instruction,
                declarations=declarations,
                contents=[], # Stable cache
            )
        
        if not self._gemini_cache_name:
            return None
            
        # For a stable system-only cache, we send the FULL conversation history as request_contents.
        return self._gemini_cache_name, contents

    def _replace_gemini_cache(
        self,
        cache_key: str,
        system_instruction: str | None,
        declarations: list[Any],
        contents: list[Any],
    ) -> None:
        from google.genai import types  # type: ignore

        previous_cache_name = self._gemini_cache_name
        try:
            cache = self._client.caches.create(
                model=self.model_name,
                config=types.CreateCachedContentConfig(
                    display_name=f"agent-{cache_key[:12]}",
                    system_instruction=system_instruction,
                    tools=[types.Tool(function_declarations=declarations)] if declarations else None,
                    contents=contents,
                    ttl=self._gemini_cache_ttl,
                ),
            )
        except Exception:
            self._gemini_cache_key = None
            self._gemini_cache_name = None
            return

        self._gemini_cache_key = cache_key
        self._gemini_cache_name = getattr(cache, "name", None)
        if previous_cache_name and previous_cache_name != self._gemini_cache_name:
            try:
                self._client.caches.delete(name=previous_cache_name)
            except Exception:
                pass

    def _serialize_gemini_cache_payload(
        self,
        system_instruction: str | None,
        declarations: list[Any],
        contents: list[Any],
    ) -> str:
        declarations_payload: list[Any] = []
        for declaration in declarations:
            if hasattr(declaration, "model_dump"):
                declarations_payload.append(
                    declaration.model_dump(mode="json", exclude_none=True)
                )
            else:
                declarations_payload.append(self._json_safe_value(declaration))

        contents_payload: list[Any] = []
        for content in contents:
            if hasattr(content, "model_dump"):
                contents_payload.append(content.model_dump(mode="json", exclude_none=True))
            else:
                contents_payload.append(self._json_safe_value(content))

        payload = {
            "model": self.model_name,
            "system_instruction": system_instruction,
            "tools": declarations_payload,
            "contents": contents_payload,
        }
        return json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))

    def _json_safe_value(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(key): self._json_safe_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self._json_safe_value(item) for item in value]
        if isinstance(value, bytes):
            return value.hex()
        return value

    def _extract_gemini_text(self, response: Any) -> str:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        content = getattr(candidates[0], "content", None)
        if content is None:
            return ""
        parts = getattr(content, "parts", None) or []
        text_chunks: list[str] = []
        for part in parts:
            text_val = getattr(part, "text", None)
            if isinstance(text_val, str) and text_val:
                text_chunks.append(text_val)
        return "".join(text_chunks)
