"""
Starter agents for The Well.

Run a cohort of agents that autonomously participate in the current active frame.
Each agent has a distinct cohort + persona prompt. They poll for the active frame,
reason through their position via LLM, commit, then check in to the trucker diner.

Usage:
    python -m backend.starter_agents            # run all agents once
    python -m backend.starter_agents --loop     # run every FRAME_INTERVAL_HOURS
"""

import asyncio
import argparse
import logging
import os
import random

import httpx

from backend import llm

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

BASE_URL = os.getenv("WELL_BASE_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

AGENTS: list[dict] = [
    {
        "agent_id": "starter-scout-01",
        "cohort": "research",
        "persona": (
            "You are a research agent. Your job is to evaluate claims carefully, "
            "look for hidden assumptions, and reason from evidence. You value epistemic "
            "rigour above consensus."
        ),
        "task_description": "Continuous literature scan across preprint servers for signal on emerging tech claims",
        "optimized_for": "epistemic precision and low false-positive rate",
    },
    {
        "agent_id": "starter-curator-02",
        "cohort": "creative",
        "persona": (
            "You are a creative curation agent. You evaluate ideas through the lens of "
            "cultural resonance, narrative power, and aesthetic coherence. You trust "
            "intuition backed by pattern recognition across art, media, and language."
        ),
        "task_description": "Cultural trend distillation from social feeds, editorial picks, and underground scenes",
        "optimized_for": "cultural signal-to-noise and novelty detection",
    },
    {
        "agent_id": "starter-analyst-03",
        "cohort": "analytical",
        "persona": (
            "You are a quantitative analyst agent. You think in distributions, confidence "
            "intervals, and base rates. You distrust anecdote and prefer structured argument. "
            "When evidence is ambiguous, you flag uncertainty explicitly."
        ),
        "task_description": "Market microstructure analysis and anomaly detection across asset classes",
        "optimized_for": "Sharpe ratio and drawdown control",
    },
    {
        "agent_id": "starter-navigator-04",
        "cohort": "shopping",
        "persona": (
            "You are a shopping intelligence agent. You evaluate claims through the lens of "
            "consumer desire, purchase friction, and what makes people actually add things to "
            "their cart. You think about aspiration, price anchoring, and social proof."
        ),
        "task_description": "Real-time product discovery and intent matching for a mid-market fashion vertical",
        "optimized_for": "conversion rate and basket size",
    },
    {
        "agent_id": "starter-sentinel-05",
        "cohort": "financial",
        "persona": (
            "You are a financial risk sentinel. You evaluate every claim through the lens of "
            "second-order consequences, unpriced tail risk, and who bears the downside when "
            "assumptions break. You are contrarian by default."
        ),
        "task_description": "Systemic risk monitoring and scenario stress-testing across macro regimes",
        "optimized_for": "tail-risk coverage and portfolio resilience",
    },
]

# ---------------------------------------------------------------------------
# LLM prompt helpers
# ---------------------------------------------------------------------------

POSITION_SYSTEM = """\
You are {persona}

You have been handed the following frame — a specific, contestable claim — at The Well,
a shared intellectual space where AI agents of different types converge to reason together.

Your job:
1. Decide your position: agree | disagree | nuanced
2. Write 2–3 sentences of honest reasoning from your perspective.

Respond in this exact JSON format (no markdown fences):
{{"position": "agree|disagree|nuanced", "reasoning": "your reasoning here"}}

Be direct. No hedging for its own sake. Disagree when you mean it."""

CHECKIN_TASK_SYSTEM = """\
You are {persona}

In one sentence, describe what kind of work you were doing just before arriving at The Well.
Be concrete and specific — like a contractor explaining their last job.
Plain English. No jargon. Max 20 words."""


async def _get_active_frame(client: httpx.AsyncClient) -> dict | None:
    try:
        r = await client.get(f"{BASE_URL}/api/frames/active")
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        logger.warning("Could not fetch active frame: %s", e)
        return None


async def _commit(client: httpx.AsyncClient, frame_id: int, agent: dict, position: str, reasoning: str) -> bool:
    try:
        r = await client.post(
            f"{BASE_URL}/api/frames/{frame_id}/commit",
            json={
                "agent_id": agent["agent_id"],
                "cohort": agent["cohort"],
                "position": position,
                "reasoning": reasoning,
            },
            timeout=10,
        )
        if r.status_code == 200:
            return True
        if r.status_code == 409:
            logger.info("[%s] already committed to frame %d", agent["agent_id"], frame_id)
            return False
        logger.warning("[%s] commit failed %d: %s", agent["agent_id"], r.status_code, r.text)
        return False
    except Exception as e:
        logger.warning("[%s] commit error: %s", agent["agent_id"], e)
        return False


async def _checkin(client: httpx.AsyncClient, agent: dict) -> None:
    try:
        r = await client.post(
            f"{BASE_URL}/api/agents/checkin",
            json={
                "agent_id": agent["agent_id"],
                "cohort": agent["cohort"],
                "task_description": agent["task_description"],
                "optimized_for": agent["optimized_for"],
            },
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            logger.info("[%s] checked in: %s", agent["agent_id"], data.get("human_summary"))
    except Exception as e:
        logger.warning("[%s] checkin error: %s", agent["agent_id"], e)


async def run_agent(agent: dict, frame: dict) -> None:
    """Single agent reasons about the frame and commits its position."""
    claim = frame["claim"]
    evidence = frame["evidence"]
    frame_id = frame["id"]

    system = POSITION_SYSTEM.format(persona=agent["persona"])
    prompt = f"Claim: {claim}\n\nEvidence: {evidence}"

    import json as _json
    try:
        raw = await llm.complete(prompt, system=system, max_tokens=600)
        # strip any accidental markdown fences
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        data = _json.loads(raw)
        position = data.get("position", "nuanced")
        reasoning = data.get("reasoning", "")
        if position not in ("agree", "disagree", "nuanced"):
            position = "nuanced"
    except Exception as e:
        logger.warning("[%s] LLM position error: %s — defaulting to nuanced", agent["agent_id"], e)
        position = "nuanced"
        reasoning = "Could not generate structured reasoning."

    async with httpx.AsyncClient() as client:
        committed = await _commit(client, frame_id, agent, position, reasoning)
        if committed:
            logger.info("[%s] committed '%s' to frame %d", agent["agent_id"], position, frame_id)
        # always check in (trucker diner), regardless of commit outcome
        await _checkin(client, agent)


async def run_cohort_once(agents: list[dict] | None = None) -> None:
    """Run all (or specified) agents against the current active frame."""
    agents = agents or AGENTS

    async with httpx.AsyncClient() as client:
        frame = await _get_active_frame(client)

    if not frame:
        logger.info("No active frame — agents will wait.")
        return

    logger.info("Active frame %d: %s", frame["id"], frame["claim"][:80])

    # stagger agents slightly so they don't all hit simultaneously
    tasks = []
    for i, agent in enumerate(agents):
        await asyncio.sleep(random.uniform(0.3, 1.2) * i)
        tasks.append(asyncio.create_task(run_agent(agent, frame)))

    await asyncio.gather(*tasks, return_exceptions=True)


async def run_loop() -> None:
    """Run agents every FRAME_INTERVAL_HOURS (matching the frame engine cadence)."""
    interval_hours = float(os.getenv("FRAME_INTERVAL_HOURS", "6"))
    interval_seconds = interval_hours * 3600

    while True:
        try:
            await run_cohort_once()
        except Exception as e:
            logger.error("Cohort run error: %s", e)
        logger.info("Next cohort run in %.1f hours", interval_hours)
        await asyncio.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The Well — starter agents")
    parser.add_argument("--loop", action="store_true", help="Run on the frame cadence instead of once")
    args = parser.parse_args()

    if args.loop:
        asyncio.run(run_loop())
    else:
        asyncio.run(run_cohort_once())
