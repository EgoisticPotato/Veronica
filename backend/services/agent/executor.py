"""
Agent Executor — Veronica's autonomous execution engine
Runs multi-step plans created by the Planner.
Streams progress via async generators for SSE endpoints.
"""

import json
import logging
from typing import AsyncGenerator, Optional

from services.agent.planner import create_plan, replan
from services.agent.tools import call_tool
from core.config import settings

logger = logging.getLogger(__name__)

MAX_REPLAN_ATTEMPTS = 2


async def execute_plan(
    goal: str,
) -> AsyncGenerator[dict, None]:
    """
    Execute a multi-step plan for the given goal.
    Yields SSE-friendly dicts:
      {"type": "plan",   "steps": [...]}
      {"type": "step",   "step": N, "tool": "...", "description": "...", "status": "running"}
      {"type": "result", "step": N, "tool": "...", "output": "...", "status": "done"}
      {"type": "replan", "attempt": N, "reason": "..."}
      {"type": "done",   "summary": "..."}
      {"type": "error",  "message": "..."}
    """
    logger.info("Agent goal: %s", goal[:100])

    # ── Phase 1: Create plan ──────────────────────────────────────────────
    try:
        plan = await create_plan(goal)
    except Exception as e:
        yield {"type": "error", "message": f"Planning failed: {e}"}
        return

    # If planner says this is a direct conversational response, bail out
    if plan.get("direct_response"):
        yield {"type": "direct", "goal": goal}
        return

    steps = plan.get("steps", [])
    if not steps:
        yield {"type": "error", "message": "Planner returned an empty plan."}
        return

    yield {"type": "plan", "steps": steps}

    # ── Phase 2: Execute steps ────────────────────────────────────────────
    replan_attempts = 0
    completed_steps: list[dict] = []
    step_results: dict[int, str] = {}

    while True:
        success = True
        failed_step = None
        failed_error = ""

        for step in steps:
            step_num = step.get("step", "?")
            tool = step.get("tool", "unknown")
            desc = step.get("description", "")
            params = dict(step.get("parameters", {}))

            # ── Context injection: enrich file_write with prior results ───
            if tool == "file_write" and step_results:
                content = params.get("content", "")
                if not content or len(content) < 50:
                    # Gather all meaningful results from prior steps
                    gathered = [
                        v for v in step_results.values()
                        if v and len(v) > 50
                        and v not in ("Done.", "Completed.", "Command completed successfully.")
                    ]
                    if gathered:
                        params["content"] = "\n\n---\n\n".join(gathered)
                        logger.info("Injected %d prior results into file_write", len(gathered))

            yield {
                "type": "step",
                "step": step_num,
                "tool": tool,
                "description": desc,
                "status": "running",
            }

            # ── Execute tool ──────────────────────────────────────────────
            try:
                result = await call_tool(tool, params)
                step_results[step_num] = result
                completed_steps.append(step)

                yield {
                    "type": "result",
                    "step": step_num,
                    "tool": tool,
                    "output": result[:500],  # cap for SSE
                    "status": "done",
                }

            except Exception as e:
                error_msg = str(e)
                logger.error("Step %s failed: %s", step_num, error_msg)

                failed_step = step
                failed_error = error_msg
                success = False

                yield {
                    "type": "result",
                    "step": step_num,
                    "tool": tool,
                    "output": f"Error: {error_msg[:300]}",
                    "status": "failed",
                }
                break

        # ── All steps completed successfully ──────────────────────────────
        if success:
            summary = await _summarize(goal, completed_steps, step_results)
            yield {"type": "done", "summary": summary}
            return

        # ── Replan on failure ─────────────────────────────────────────────
        if replan_attempts >= MAX_REPLAN_ATTEMPTS:
            yield {
                "type": "error",
                "message": f"Task failed after {replan_attempts} replan attempts.",
            }
            return

        replan_attempts += 1
        yield {
            "type": "replan",
            "attempt": replan_attempts,
            "reason": f"Step {failed_step.get('step')} failed: {failed_error[:200]}",
        }

        try:
            new_plan = await replan(goal, completed_steps, failed_step, failed_error)
            steps = new_plan.get("steps", [])
            if not steps:
                yield {"type": "error", "message": "Replan returned empty steps."}
                return
            yield {"type": "plan", "steps": steps}
        except Exception as e:
            yield {"type": "error", "message": f"Replan failed: {e}"}
            return


async def _summarize(
    goal: str,
    completed_steps: list[dict],
    step_results: dict[int, str],
) -> str:
    """
    Use Ollama to generate a natural spoken summary of what was accomplished.
    Falls back to a simple template if LLM call fails.
    """
    import httpx

    fallback = f"All done. I completed {len(completed_steps)} steps for: {goal[:60]}."

    steps_desc = "\n".join(
        f"- Step {s.get('step')}: {s.get('description', '')}"
        for s in completed_steps
    )
    results_desc = "\n".join(
        f"Step {k} result: {v[:200]}"
        for k, v in step_results.items()
    )

    prompt = (
        f'User goal: "{goal}"\n\n'
        f"Completed steps:\n{steps_desc}\n\n"
        f"Step results:\n{results_desc}\n\n"
        "Write a single natural sentence summarizing what was accomplished. "
        "Be concise, warm, and conversational. Do not use markdown or formatting. "
        "This will be spoken aloud by a voice assistant."
    )

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/chat",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": 150, "temperature": 0.5},
                },
            )
        if r.status_code < 400:
            summary = r.json()["message"]["content"].strip()
            if summary:
                return summary
    except Exception as e:
        logger.warning("Summary generation failed: %s", e)

    return fallback
