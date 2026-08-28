"""Munin — Hermes memory provider plugin (github.com/bacnh85/hermes-plugins).

Activates via:
    hermes config set memory.provider munin
    hermes gateway restart

Install:
    python3 install_plugins.py munin        # from the repo root

Design: pure-stdlib REST client (client.py) — no Node/MCP subprocess, so the
"MCP stdio subprocess has exited" failure mode that plagued the munin-memory
MCP server cannot occur. All eight MCP tools are re-exposed as native memory
provider tools with identical semantics.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider, RecallStatus

from .client import DEFAULT_BASE_URL, DEFAULT_TIMEOUT, _MuninHTTP, _load_dotenv

logger = logging.getLogger(__name__)

# Trivial inputs that carry no semantic signal — skip prefetch on them.
_TRIVIAL_RE = (
    r"^(hi|hello|hey|yo|thanks|thank you|ok|okay|cool|nice|great|got it|"
    r"sure|yes|no|yep|nope|accepted|approved|confirmed)\s*[!.,:)?]*\s*$"
)


def _env(key: str, default: str = "") -> str:
    val = os.environ.get(key)
    if val:
        return val
    # Walk the usual .env locations (Hermes convention).
    hermes_home = Path(
        os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    )
    for path in (hermes_home / ".env.local", hermes_home / ".env"):
        loaded = _load_dotenv(str(path))
        if loaded.get(key):
            return loaded[key]
    return default


def _as_float(val: Any, default: float) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_search_payload(result: Any) -> List[Dict[str, Any]]:
    """Extract the memories list from a search/list/recent result envelope."""
    if isinstance(result, dict):
        data = result.get("data", result)
        if isinstance(data, dict):
            for key in ("memories", "results", "items"):
                if isinstance(data.get(key), list):
                    return data[key]
            if isinstance(data.get("context_core"), str):
                return data.get("memories", [])
        if isinstance(data, list):
            return data
    if isinstance(result, list):
        return result
    return []


def _fmt_memory(m: Dict[str, Any]) -> str:
    """One-line digest of a memory for prefetch injection."""
    title = m.get("title") or m.get("key") or "(untitled)"
    content = (m.get("content") or "").strip()
    snippet = " ".join(content.split())[:280]
    updated = (m.get("updatedAt") or m.get("updated_at") or "")[:10]
    tags = m.get("tags") or []
    tag_s = f" [{', '.join(tags)}]" if tags else ""
    date_s = f" ({updated})" if updated else ""
    line = f"- {title}{date_s}{tag_s}"
    if snippet:
        line += f": {snippet}"
    return line


class MuninMemoryProvider(MemoryProvider):
    """Munin Context Core as a Hermes memory backend.

    - prefetch(): 6-signal search keyed on the user message; injects the top
      matches as a compact digest block.
    - tools: the eight munin_* tools (store/retrieve/search/list/recent/
      share/versions/delete/rollback/diff/project_info), same semantics as
      the MCP server exposed.
    - sync_turn: no-op (writes are explicit tool calls; the Munin protocol
      keeps session transcripts out of long-term memory by design).
    """

    def __init__(self) -> None:
        self._http: Optional[_MuninHTTP] = None
        self._session_id = ""
        self._last_recall_count = 0
        self._queue: List[str] = []
        self._queue_lock = threading.Lock()

    # -- MemoryProvider contract -------------------------------------------

    @property
    def name(self) -> str:
        return "munin"

    def is_available(self) -> bool:
        try:
            http = self._get_http()
            caps = http.capabilities()
            return bool(caps)
        except Exception as e:
            self._unavailable_reason = str(e)
            return False

    def unavailable_reason(self) -> str:
        return getattr(self, "_unavailable_reason", "")

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = session_id
        # Warm the capabilities cache so the first turn is fast.
        try:
            self._get_http().capabilities()
        except Exception as e:
            logger.warning("Munin capabilities warmup failed: %s", e)

    def system_prompt_block(self) -> str:
        project = _env("MUNIN_PROJECT", "(unset)")
        return (
            "## Munin long-term memory\n"
            "Persistent cross-project memory is ACTIVE (Munin Context Core, "
            f"project {project}). Protocol: search BEFORE non-trivial work "
            "(munin_search_memories), store decisions/learnings AFTER "
            "(munin_store_memory). Keys are stable slugs — storing with the "
            "same key updates. Memories are E2EE and shared across this "
            "user's projects; never store secrets (API keys, passwords)."
        )

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        import re

        q = (query or "").strip()
        if not q or len(q) < 8 or re.match(_TRIVIAL_RE, q, re.IGNORECASE):
            self._last_recall_count = 0
            return ""
        try:
            result = self._get_http().search({
                "query": q,
                "topK": 5,
                "minScore": 0.35,
            })
        except Exception as e:
            logger.debug("Munin prefetch failed: %s", e)
            self._last_recall_count = 0
            return ""
        memories = _parse_search_payload(result)
        if not memories:
            self._last_recall_count = 0
            return ""
        self._last_recall_count = len(memories)
        lines = [_fmt_memory(m) for m in memories[:5]]
        return (
            "### Relevant Munin memories\n"
            "Long-term memory matches for this request (verify before relying):\n"
            + "\n".join(lines)
        )

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        with self._queue_lock:
            self._queue = [query]

    def recall_status(self) -> Optional[RecallStatus]:
        return RecallStatus(provider_label="Munin", count=self._last_recall_count, glyph="🗂️")

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        # Intentional no-op: Munin protocol is explicit writes via
        # munin_store_memory. Auto-persisting raw turns would pollute the
        # E2EE store with session noise (same stance as honcho's classifier
        # but stricter — nothing is retained).
        return None

    def shutdown(self) -> None:
        self._http = None
        with self._queue_lock:
            self._queue = []

    # -- tools ----------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        def schema(name: str, desc: str, props: Dict[str, Any], req: List[str]) -> Dict[str, Any]:
            return {
                "name": name,
                "description": desc,
                "parameters": {
                    "type": "object",
                    "properties": props,
                    "required": req,
                    "additionalProperties": True,
                },
            }

        key_prop = {"type": "string", "description": "Memory key (stable slug, e.g. msft-exit-plan-2026-08-26)"}
        return [
            schema(
                "munin_store_memory",
                "Store or update long-term memories in Munin Context Core. "
                "BATCH: pass memories:[{key,title,content,tags},...] (up to 50). "
                "Single: pass key/content/title/tags/validFrom/validUntil/pinned. "
                "Tags use key:value slugs (type:decision, domain:trading, status:active). "
                "Never store secrets.",
                {
                    "key": key_prop,
                    "title": {"type": "string", "description": "Human-readable title"},
                    "content": {"type": "string", "description": "Memory body (markdown ok)"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Slug tags, e.g. [\"type:decision\",\"domain:trading\"]"},
                    "pinned": {"type": "boolean", "description": "Pin to top of recalls"},
                    "memories": {"type": "array", "items": {"type": "object"}, "description": "Batch store up to 50 memories"},
                },
                [],
            ),
            schema(
                "munin_retrieve_memory",
                "Retrieve one memory by its unique key.",
                {"key": key_prop},
                ["key"],
            ),
            schema(
                "munin_search_memories",
                "Hybrid 6-signal search (keyword + semantic + named-entity + quoted-phrase + recency + pinned). "
                "Use double quotes for exact phrases ('Project Munin v1.3'); include proper nouns for entity boost. "
                "Protocol: ALWAYS search before non-trivial work.",
                {
                    "query": {"type": "string", "description": "Search query"},
                    "topK": {"type": "integer", "description": "Max results (default 8, server max 50)"},
                    "minScore": {"type": "number", "description": "Minimum relevance score 0-1 (default 0.35)"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter by tags"},
                    "tagMode": {"type": "string", "enum": ["or", "and"], "description": "Tag combination (default or)"},
                    "since": {"type": "string", "description": "Created on/after: ISO date or relative ('7 days ago','last week')"},
                    "before": {"type": "string", "description": "Created on/before"},
                    "offset": {"type": "integer", "description": "Pagination offset"},
                },
                ["query"],
            ),
            schema(
                "munin_list_memories",
                "List all memories (paginated) with title/key/tags/updatedAt — no content bodies.",
                {
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer", "description": "Page size (default 50)"},
                    "tag": {"type": "string", "description": "Filter by one tag"},
                },
                [],
            ),
            schema(
                "munin_recent_memories",
                "Most recently updated memories (compact view).",
                {"limit": {"type": "integer", "description": "Count (default 10)"}},
                [],
            ),
            schema(
                "munin_project_info",
                "Get current project metadata: E2EE status, tier, capabilities, project id.",
                {},
                [],
            ),
            schema(
                "munin_versions",
                "List all versions of a memory (version history).",
                {"key": key_prop},
                ["key"],
            ),
            schema(
                "munin_rollback",
                "Rollback a memory to a previous version.",
                {"key": key_prop, "version": {"type": "integer", "description": "Version number to restore"}},
                ["key", "version"],
            ),
            schema(
                "munin_diff_memory",
                "Diff two versions of the same memory.",
                {"key": key_prop, "fromVersion": {"type": "integer"}, "toVersion": {"type": "integer"}},
                ["key"],
            ),
            schema(
                "munin_share_memory",
                "Share one or more memories to other projects owned by the same account "
                "(confirmed cross-project sharing).",
                {
                    "memoryIds": {"type": "array", "items": {"type": "string"}, "description": "Memory ids/keys to share"},
                    "targetProjectIds": {"type": "array", "items": {"type": "string"}, "description": "Destination projects"},
                },
                ["memoryIds", "targetProjectIds"],
            ),
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        try:
            return self._dispatch(tool_name, args)
        except Exception as e:
            return json.dumps({
                "ok": False,
                "error": {"code": "MUNIN_CALL_FAILED", "message": str(e)},
            })

    def _dispatch(self, tool_name: str, args: Dict[str, Any]) -> str:
        http = self._get_http()
        if tool_name == "munin_store_memory":
            batch = args.get("memories")
            if isinstance(batch, list) and batch:
                result = http.store(batch[:50])
            else:
                entry = {k: v for k, v in args.items() if k in (
                    "key", "title", "content", "tags", "pinned",
                    "validFrom", "validUntil",
                ) and v not in (None, "")}
                if not entry.get("content"):
                    return json.dumps({"ok": False, "error": {"code": "MISSING_CONTENT", "message": "content (or memories[]) is required"}})
                if not entry.get("key"):
                    import re as _re
                    base = (entry.get("title") or entry["content"])[:48]
                    entry["key"] = _re.sub(r"[^a-z0-9-]+", "-", base.lower()).strip("-")[:48]
                result = http.store([entry])
        elif tool_name == "munin_retrieve_memory":
            result = http.retrieve(args["key"])
        elif tool_name == "munin_search_memories":
            filters = {}
            if args.get("since"):
                filters["since"] = args["since"]
            if args.get("before"):
                filters["before"] = args["before"]
            payload = {k: v for k, v in {
                "query": args.get("query"),
                "topK": args.get("topK") or args.get("limit") or 8,
                "minScore": args.get("minScore", 0.35),
                "tags": args.get("tags"),
                "tagMode": args.get("tagMode"),
                "offset": args.get("offset"),
            }.items() if v not in (None, "")}
            if filters:
                payload["filters"] = filters
            result = http.search(payload)
            # Compact the payload: strip content bodies over 500 chars in
            # list-style results to keep tool output lean.
            memories = _parse_search_payload(result)
            if len(json.dumps(result)) > 20000:
                for m in memories:
                    if isinstance(m, dict) and len(m.get("content") or "") > 500:
                        m["content"] = m["content"][:500] + "…"
                result = {"memories": memories}
        elif tool_name in ("munin_list_memories", "munin_recent_memories"):
            payload = {k: v for k, v in args.items() if v not in (None, "")}
            result = (http.list if tool_name == "munin_list_memories" else http.recent)(payload)
        elif tool_name == "munin_project_info":
            result = http.project_info()
        elif tool_name == "munin_versions":
            result = http.passthrough("versions", {"key": args["key"]})
        elif tool_name == "munin_rollback":
            result = http.passthrough("rollback", {
                "key": args["key"],
                "version": args.get("version") or args.get("id"),
            })
        elif tool_name == "munin_diff_memory":
            result = http.passthrough("diff", {
                "key": args["key"],
                "fromVersion": args.get("fromVersion"),
                "toVersion": args.get("toVersion"),
            })
        elif tool_name == "munin_share_memory":
            result = http.passthrough("share", {
                "memoryIds": args.get("memoryIds"),
                "targetProjectIds": args.get("targetProjectIds"),
            })
        else:
            return json.dumps({"ok": False, "error": {"code": "UNKNOWN_TOOL", "message": tool_name}})
        return json.dumps({"ok": True, "action": tool_name.removeprefix("munin_"), "data": result})

    # -- optional hooks -------------------------------------------------------

    def backup_paths(self) -> List[str]:
        # Munin is a hosted service; nothing on disk to back up beyond the
        # env file the user already backs up.
        return []

    # -- internals ------------------------------------------------------------

    def _get_http(self) -> _MuninHTTP:
        if self._http is not None:
            return self._http
        api_key = _env("MUNIN_API_KEY")
        project = _env("MUNIN_PROJECT")
        if not api_key:
            self._unavailable_reason = "MUNIN_API_KEY is not set (add it to ~/.hermes/.env)"
            raise RuntimeError(self._unavailable_reason)
        if not project:
            self._unavailable_reason = "MUNIN_PROJECT is not set (e.g. proj_hermes-mac-mini-m4)"
            raise RuntimeError(self._unavailable_reason)
        base_url = _env("MUNIN_BASE_URL", DEFAULT_BASE_URL)
        timeout = _as_float(_env("MUNIN_TIMEOUT"), DEFAULT_TIMEOUT)
        self._http = _MuninHTTP(api_key=api_key, project=project, base_url=base_url, timeout=timeout)
        return self._http


# register(ctx) plugin-style entry — how Hermes' memory loader prefers to
# discover providers. Also exposes the class for the subclass-scan fallback.
_provider = MuninMemoryProvider()


def register(ctx) -> None:
    ctx.register_memory_provider(_provider)
