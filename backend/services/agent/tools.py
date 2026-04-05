"""
Agent Tools — Veronica's hands
All tools the agent executor can call. Each tool is an async function
that takes a parameters dict and returns a string result.

Security:
  - File operations sandboxed to AGENT_ALLOWED_PATHS (Desktop, Documents, Downloads)
  - CMD execution gated by AGENT_ENABLE_CMD=true
  - Path traversal prevention via resolve() + is_relative_to()
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from core.config import settings

logger = logging.getLogger(__name__)

# ── Path security ─────────────────────────────────────────────────────────────

_HOME = Path.home()

# Shortcut mapping
_PATH_SHORTCUTS = {
    "desktop":   _HOME / "Desktop",
    "documents": _HOME / "Documents",
    "downloads": _HOME / "Downloads",
    "home":      _HOME,
}

# Try Windows registry for Desktop (some installs move it)
try:
    import winreg
    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
    )
    _PATH_SHORTCUTS["desktop"] = Path(winreg.QueryValueEx(key, "Desktop")[0])
except Exception:
    pass


def _get_allowed_roots() -> list[Path]:
    """Build list of allowed root directories from config."""
    roots = []
    raw = settings.AGENT_ALLOWED_PATHS
    names = [n.strip() for n in raw.split(",") if n.strip()] if isinstance(raw, str) else raw
    for name in names:
        name_lower = name.strip().lower()
        if name_lower in _PATH_SHORTCUTS:
            roots.append(_PATH_SHORTCUTS[name_lower])
        else:
            # Treat as absolute path
            p = Path(name.strip())
            if p.is_absolute():
                roots.append(p)
    return roots


def _resolve_path(raw_path: str) -> Path:
    """
    Resolve a user-facing path string to a real filesystem path.
    Applies shortcut expansion and security checks.
    Raises ValueError if path is outside allowed roots.
    """
    raw = raw_path.strip().strip('"').strip("'")
    raw_lower = raw.lower()

    # Shortcut expansion
    if raw_lower in _PATH_SHORTCUTS:
        return _PATH_SHORTCUTS[raw_lower]

    # Check if it starts with a shortcut (e.g. "Desktop/notes.txt")
    for shortcut, base in _PATH_SHORTCUTS.items():
        if raw_lower.startswith(shortcut + "/") or raw_lower.startswith(shortcut + "\\"):
            remainder = raw[len(shortcut) + 1:]
            return (base / remainder).resolve()

    # Absolute path
    resolved = Path(raw).resolve()

    # Security check
    allowed = _get_allowed_roots()
    if allowed:
        for root in allowed:
            try:
                if resolved.is_relative_to(root.resolve()):
                    return resolved
            except (ValueError, AttributeError):
                # Python < 3.9 fallback
                try:
                    resolved.relative_to(root.resolve())
                    return resolved
                except ValueError:
                    continue

        raise ValueError(
            f"Path '{raw}' is outside allowed directories: "
            f"{[str(r) for r in allowed]}"
        )

    return resolved


# ── CMD denylist ──────────────────────────────────────────────────────────────

_CMD_DENYLIST = {
    "format", "fdisk", "diskpart", "rm -rf", "del /s /q",
    "shutdown", "restart", "reboot", "reg delete", "reg add",
    "net user", "net localgroup", "takeown", "icacls",
}


def _is_cmd_safe(command: str) -> bool:
    """Check if a command is not in the denylist."""
    cmd_lower = command.lower().strip()
    for denied in _CMD_DENYLIST:
        if denied in cmd_lower:
            return False
    return True


# ── Tool implementations ──────────────────────────────────────────────────────

async def tool_web_search(params: dict) -> str:
    """Search the web using Tavily."""
    from services.search_service import web_search, format_search_context

    query = params.get("query", "")
    if not query:
        return "Error: No search query provided."

    try:
        results = await web_search(query, max_results=settings.WEB_SEARCH_MAX_RESULTS)
        if not results:
            return f"No results found for: {query}"
        context = format_search_context(results, query)
        return context
    except Exception as e:
        logger.error("web_search tool error: %s", e)
        return f"Search failed: {str(e)}"


async def tool_file_read(params: dict) -> str:
    """Read a file's contents."""
    raw_path = params.get("path", "")
    if not raw_path:
        return "Error: No file path provided."

    try:
        resolved = _resolve_path(raw_path)
    except ValueError as e:
        return f"Error: {e}"

    if not resolved.exists():
        return f"Error: File not found: {resolved}"
    if not resolved.is_file():
        return f"Error: Not a file: {resolved}"
    if resolved.stat().st_size > 1_000_000:  # 1MB limit
        return f"Error: File too large (max 1MB): {resolved}"

    try:
        content = resolved.read_text(encoding="utf-8", errors="replace")
        return f"Contents of {resolved.name}:\n{content[:5000]}"
    except Exception as e:
        return f"Error reading file: {e}"


async def tool_file_write(params: dict) -> str:
    """Write content to a file."""
    raw_path = params.get("path", "")
    content = params.get("content", "")
    name = params.get("name", "")

    if not raw_path:
        return "Error: No file path provided."
    if not content:
        return "Error: No content to write."

    try:
        resolved = _resolve_path(raw_path)
    except ValueError as e:
        return f"Error: {e}"

    # If resolved is a directory (e.g. "Desktop"), append the filename
    if resolved.is_dir():
        if not name:
            return "Error: Path is a directory but no filename ('name') provided."
        resolved = resolved / name

    try:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return f"File written successfully: {resolved}"
    except Exception as e:
        return f"Error writing file: {e}"


async def tool_file_list(params: dict) -> str:
    """List directory contents."""
    raw_path = params.get("path", "Desktop")

    try:
        resolved = _resolve_path(raw_path)
    except ValueError as e:
        return f"Error: {e}"

    if not resolved.exists():
        return f"Error: Directory not found: {resolved}"
    if not resolved.is_dir():
        return f"Error: Not a directory: {resolved}"

    try:
        entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = []
        for entry in entries[:50]:  # cap at 50
            kind = "DIR " if entry.is_dir() else "FILE"
            size = ""
            if entry.is_file():
                sz = entry.stat().st_size
                if sz < 1024:
                    size = f" ({sz}B)"
                elif sz < 1_048_576:
                    size = f" ({sz // 1024}KB)"
                else:
                    size = f" ({sz // 1_048_576}MB)"
            lines.append(f"  {kind}  {entry.name}{size}")

        header = f"Contents of {resolved} ({len(entries)} items):"
        if len(entries) > 50:
            header += " (showing first 50)"
        return header + "\n" + "\n".join(lines)
    except Exception as e:
        return f"Error listing directory: {e}"


async def tool_cmd_run(params: dict) -> str:
    """Run a shell command. Gated by AGENT_ENABLE_CMD setting."""
    if not settings.AGENT_ENABLE_CMD:
        return "Error: Command execution is disabled. Set AGENT_ENABLE_CMD=true in .env to enable."

    command = params.get("command", "")
    if not command:
        return "Error: No command provided."

    if not _is_cmd_safe(command):
        return f"Error: Command blocked by security policy: {command}"

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(_HOME),
        )
        output = result.stdout.strip()
        error = result.stderr.strip()

        if result.returncode == 0:
            return output[:3000] if output else "Command completed successfully."
        else:
            return f"Command failed (exit code {result.returncode}):\n{error[:1000]}"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 30 seconds."
    except Exception as e:
        return f"Error running command: {e}"


async def tool_vision_screen(params: dict) -> str:
    """Capture the screen and analyze it with Ollama vision."""
    from services.vision_service import analyze_screen

    question = params.get("question", "What is on the screen?")
    try:
        description = await analyze_screen(question)
        return description
    except Exception as e:
        logger.error("vision_screen tool error: %s", e)
        return f"Vision analysis failed: {e}"


async def tool_music_play(params: dict) -> str:
    """Search and play a track on Spotify."""
    from services.spotify_service import search_and_play, get_valid_access_token

    query = params.get("query", "")
    if not query:
        return "Error: No music query provided."

    # We don't have device_id here — the frontend manages it.
    # Return an instruction for the executor to relay.
    return f"MUSIC_PLAY:{query}"


async def tool_music_pause(params: dict) -> str:
    """Pause Spotify playback."""
    from services.spotify_service import pause_playback
    ok = await pause_playback()
    return "Music paused." if ok else "Could not pause music."


async def tool_music_next(params: dict) -> str:
    """Skip to next track."""
    from services.spotify_service import skip_next
    ok = await skip_next()
    return "Skipped to next track." if ok else "Could not skip track."


async def tool_music_previous(params: dict) -> str:
    """Go back to previous track."""
    from services.spotify_service import skip_previous
    ok = await skip_previous()
    return "Went back to previous track." if ok else "Could not go back."


# ── Tool registry ─────────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, callable] = {
    "web_search":    tool_web_search,
    "file_read":     tool_file_read,
    "file_write":    tool_file_write,
    "file_list":     tool_file_list,
    "cmd_run":       tool_cmd_run,
    "vision_screen": tool_vision_screen,
    "music_play":    tool_music_play,
    "music_pause":   tool_music_pause,
    "music_next":    tool_music_next,
    "music_previous": tool_music_previous,
}


async def call_tool(tool_name: str, parameters: dict) -> str:
    """
    Dispatch a tool call by name. Returns the tool's string result.
    Raises KeyError if tool is not found.
    """
    fn = TOOL_REGISTRY.get(tool_name)
    if fn is None:
        return f"Error: Unknown tool '{tool_name}'. Available: {list(TOOL_REGISTRY.keys())}"

    logger.info("Calling tool: %s(%s)", tool_name, str(parameters)[:100])
    result = await fn(parameters)
    logger.info("Tool %s result: %s", tool_name, str(result)[:150])
    return result
