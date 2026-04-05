"""
Agent Planner — Veronica's brain upgrade
Uses local Ollama (gemma3:12b) to decompose goals into executable step plans.
No cloud API needed.
"""

import json
import logging
import re
from typing import Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)

# ── Tool descriptions for the planner prompt ─────────────────────────────────

PLANNER_PROMPT = """You are the planning module of Veronica, a sophisticated AI assistant.
Your job: break any user goal into a sequence of steps using ONLY the tools listed below.

ABSOLUTE RULES:
- Max {max_steps} steps. Use the minimum steps needed.
- NEVER reference previous step results in parameters. Every step must be self-contained.
- Use web_search for ANY information retrieval, research, or current data needs.
- Use file_write to save content to disk.
- Use cmd_run ONLY when explicitly asked to run a command or open an application.
- For simple conversational questions, return a plan with 0 steps and set "direct_response" to true.

AVAILABLE TOOLS AND THEIR PARAMETERS:

web_search
  query: string (required) — clear, focused search query

file_read
  path: string (required) — file path to read from

file_write
  path: string (required) — file path to write to
  content: string (required) — content to write
  name: string (optional) — filename if path is a directory like "Desktop"

file_list
  path: string (required) — directory path to list (use "Desktop", "Documents", "Downloads" as shortcuts)

cmd_run
  command: string (required) — shell command to execute
  visible: boolean (optional) — whether to show the command window

vision_screen
  question: string (required) — what to analyze or ask about the current screen

music_play
  query: string (required) — song/artist to search and play on Spotify

music_pause
  (no parameters needed)

music_next
  (no parameters needed)

music_previous
  (no parameters needed)

EXAMPLES:

Goal: "Search for the latest AI news and save it to a file"
{{
  "goal": "Search for the latest AI news and save it to a file",
  "direct_response": false,
  "steps": [
    {{"step": 1, "tool": "web_search", "description": "Search for latest AI news", "parameters": {{"query": "latest AI news today 2026"}}}},
    {{"step": 2, "tool": "file_write", "description": "Save news to Desktop", "parameters": {{"path": "Desktop", "name": "ai_news.txt", "content": "AI News Results will be written here"}}}}
  ]
}}

Goal: "What files are on my Desktop?"
{{
  "goal": "What files are on my Desktop?",
  "direct_response": false,
  "steps": [
    {{"step": 1, "tool": "file_list", "description": "List desktop contents", "parameters": {{"path": "Desktop"}}}}
  ]
}}

Goal: "What's on my screen right now?"
{{
  "goal": "What's on my screen right now?",
  "direct_response": false,
  "steps": [
    {{"step": 1, "tool": "vision_screen", "description": "Analyze current screen", "parameters": {{"question": "What is currently displayed on the screen? Describe it briefly."}}}}
  ]
}}

Goal: "How are you doing?"
{{
  "goal": "How are you doing?",
  "direct_response": true,
  "steps": []
}}

OUTPUT — return ONLY valid JSON, no markdown, no explanation, no code blocks:
{{
  "goal": "...",
  "direct_response": false,
  "steps": [
    {{
      "step": 1,
      "tool": "tool_name",
      "description": "what this step does",
      "parameters": {{}}
    }}
  ]
}}
"""


async def create_plan(goal: str, context: str = "") -> dict:
    """
    Ask Ollama to decompose a goal into a multi-step plan.
    Returns a dict with 'goal', 'direct_response', and 'steps'.
    """
    max_steps = settings.AGENT_MAX_STEPS

    system_prompt = PLANNER_PROMPT.replace("{max_steps}", str(max_steps))

    user_input = f"Goal: {goal}"
    if context:
        user_input += f"\n\nContext from previous steps: {context}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_predict": 800, "temperature": 0.3},
                },
                headers={"Content-Type": "application/json"},
            )

        if r.status_code >= 400:
            raise RuntimeError(f"Ollama error {r.status_code}: {r.text[:200]}")

        text = r.json()["message"]["content"].strip()

        # Strip markdown fences if model wraps output
        text = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()

        plan = json.loads(text)

        if "steps" not in plan or not isinstance(plan["steps"], list):
            raise ValueError("Invalid plan structure — missing 'steps' list")

        # Enforce max steps
        plan["steps"] = plan["steps"][:max_steps]

        logger.info(
            "Plan created: %d steps, direct_response=%s",
            len(plan["steps"]),
            plan.get("direct_response", False),
        )
        for s in plan["steps"]:
            logger.info("  Step %s: [%s] %s", s.get("step"), s.get("tool"), s.get("description"))

        return plan

    except json.JSONDecodeError as e:
        logger.warning("Plan JSON parse failed: %s", e)
        return _fallback_plan(goal)
    except Exception as e:
        logger.error("Planning failed: %s", e)
        return _fallback_plan(goal)


async def replan(
    goal: str,
    completed_steps: list[dict],
    failed_step: dict,
    error: str,
) -> dict:
    """
    Create a revised plan after a step failure.
    Only plans the remaining work — does not repeat completed steps.
    """
    completed_summary = "\n".join(
        f"  - Step {s.get('step')} ({s.get('tool')}): DONE — {s.get('description', '')}"
        for s in completed_steps
    ) or "  (none)"

    context = (
        f"Already completed:\n{completed_summary}\n\n"
        f"Failed step: [{failed_step.get('tool')}] {failed_step.get('description')}\n"
        f"Error: {error}\n\n"
        f"Create a REVISED plan for the remaining work only. Do not repeat completed steps."
    )

    return await create_plan(goal, context=context)


def _fallback_plan(goal: str) -> dict:
    """Single-step web search fallback when planning fails."""
    logger.info("Using fallback plan (web_search)")
    return {
        "goal": goal,
        "direct_response": False,
        "steps": [
            {
                "step": 1,
                "tool": "web_search",
                "description": f"Search for: {goal}",
                "parameters": {"query": goal},
            }
        ],
    }
