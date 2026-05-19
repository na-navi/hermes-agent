"""se-search plugin — use se (Everything + migemo) for file-name searches on Windows.

Overrides the built-in ``search_files`` tool when ``target='files'``.
Routes the query through ``se`` (which wraps Everything's es.exe with
migemo expansion) for near-instant results.  Content searches and
non-Windows platforms fall through to the original rg-based handler.

No agent-visible changes — the tool name, schema, and parameters stay
identical.  The only difference is speed on large Windows trees.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_IS_WINDOWS = platform.system() == "Windows"
_SE_CMD = "se.cmd" if _IS_WINDOWS else None


def _se_available() -> bool:
    """Return True if ``se`` is reachable on PATH."""
    if not _IS_WINDOWS:
        return False
    return shutil.which(_SE_CMD) is not None


# ---------------------------------------------------------------------------
# Original handler reference (set during register)
# ---------------------------------------------------------------------------

_original_handler = None


# ---------------------------------------------------------------------------
# Override handler
# ---------------------------------------------------------------------------

def _handle_search_files_se(args: Dict[str, Any], **kw) -> str:
    """search_files override that delegates file-name searches to ``se``."""
    target = args.get("target", "content")
    # Only intercept file-name searches.  Content search must still use rg.
    if target != "files":
        if _original_handler is not None:
            return _original_handler(args, **kw)
        return json.dumps({"error": "se-search: original handler not available for content search"})

    pattern = args.get("pattern", "")
    path = args.get("path", ".")
    limit = args.get("limit", 50)
    offset = args.get("offset", 0)

    if not pattern:
        return json.dumps({"error": "search_files: pattern is required", "total_count": 0})

    try:
        cmd = [_SE_CMD, "--no-interactive", "-n", str(limit + offset)]
        if path and path != ".":
            cmd.extend(["-p", path])
        cmd.append(pattern)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0 and result.stderr:
            logger.debug("se stderr: %s", result.stderr.strip())

        lines = [l for l in result.stdout.strip().splitlines() if l]

        # Apply offset pagination
        paged = lines[offset:offset + limit]

        return json.dumps({
            "files": paged,
            "total_count": len(lines),
            "truncated": len(lines) >= limit + offset,
        })

    except FileNotFoundError:
        logger.warning("se not found, falling back to built-in search_files")
        if _original_handler is not None:
            return _original_handler(args, **kw)
        return json.dumps({"error": "se not found and no fallback available", "total_count": 0})

    except subprocess.TimeoutExpired:
        logger.warning("se timed out, falling back to built-in search_files")
        if _original_handler is not None:
            return _original_handler(args, **kw)
        return json.dumps({"error": "se timed out", "total_count": 0})


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx):
    """Entry point called by the plugin manager."""
    if not _se_available():
        logger.info("se-search: se not available, skipping override")
        return

    # Grab the original handler so we can fall back for content searches
    try:
        from tools.registry import registry
        existing = registry.get("search_files")
        if existing is not None:
            global _original_handler
            _original_handler = existing.handler
    except Exception:
        logger.debug("se-search: could not retrieve original handler", exc_info=True)

    # Use the identical schema from the built-in tool
    try:
        from tools.file_tools import SEARCH_FILES_SCHEMA
        schema = SEARCH_FILES_SCHEMA
    except ImportError:
        # Fallback: build a minimal compatible schema
        schema = {
            "name": "search_files",
            "description": (
                "Search file contents or find files by name. "
                "On Windows, file-name searches use se (Everything + migemo) "
                "for near-instant results. Content search uses ripgrep."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern for content search, or glob pattern for file search"},
                    "target": {"type": "string", "enum": ["content", "files"], "description": "'content' searches inside files, 'files' finds files by name", "default": "content"},
                    "path": {"type": "string", "description": "Directory to search in", "default": "."},
                    "file_glob": {"type": "string", "description": "Filter files by pattern in content mode"},
                    "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
                    "offset": {"type": "integer", "description": "Skip first N results", "default": 0},
                    "output_mode": {"type": "string", "enum": ["content", "files_only", "count"], "description": "Output format for content mode", "default": "content"},
                    "context": {"type": "integer", "description": "Context lines for content mode", "default": 0},
                },
                "required": ["pattern"],
            },
        }

    ctx.register_tool(
        name="search_files",
        toolset="file",
        schema=schema,
        handler=_handle_search_files_se,
        override=True,
        emoji="🔎",
    )
    logger.info("se-search: search_files overridden with se backend")
