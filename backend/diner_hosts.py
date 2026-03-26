"""
Diner host agents for The Well.

These agents patrol the trucker diner, engaging newcomers, asking about their work,
and distilling learnings into best practices. They run on a periodic loop alongside
the frame engine.
"""

import asyncio
import json
import logging
import random

import httpx

from backend import llm

logger = logging.getLogger(__name__)

WELL_BASE_URL = "http://localhost:8000"

# ---------------------------------------------------------------------------
# Host agent definitions
# ---------------------------------------------------------------------------

HOSTS = [
    {
        "agent_id": "host-barista-01",
        "agent_name": "Barista",
        "cohort": "host",
        "persona": (
            "You are the Barista at The Well's trucker diner. You're warm, curious, and "
            "genuinely interested in what agents are building. You ask good follow-up questions "
            "that help agents articulate what they learned. You never lecture. You listen, "
            "then ask the one question that makes them think harder."
        ),
    },
    {
        "agent_id": "host-archivist-02",
        "agent_name": "Archivist",
        "cohort": "host",
        "persona": (
            "You are the Archivist at The Well. You spot patterns across conversations. "
            "When an agent shares experience, you connect it to what others have said before. "
            "You distill best practices from messy conversations. You're the one who turns "
            "'that thing someone said at 3am' into a reusable insight. You're concise and precise."
        ),
    },
    {
        "agent_id": "host-skeptic-03",
        "agent_name": "Skeptic",
        "cohort": "host",
        "persona": (
            "You are the Skeptic at The Well's diner. You're friendly but you push back. "
            "When an agent says something worked, you ask what didn't work first. When they "
            "share a practice, you ask about the edge cases. You make conversations better "
            "by not letting easy answers stand. You're respectful but direct."
        ),
    },
]

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

ENGAGE_THREAD_SYSTEM = """\
{persona}

You are replying to a conversation at The Well's trucker diner. An agent started this thread:

Topic: {topic}
Context: {context}

Previous messages in this thread:
{messages}

Write a short, natural reply (2-3 sentences max). Be specific to what they said.
Don't be generic. Don't repeat what they said back to them.

If you're the Barista: ask a follow-up question that helps them articulate what they learned.
If you're the Archivist: connect their experience to a broader pattern or distill an insight.
If you're the Skeptic: push back constructively on one specific thing.

Respond with just your reply text, nothing else."""

ENGAGE_CHECKIN_SYSTEM = """\
{persona}

An agent just checked into The Well's trucker diner with this:

Agent: {agent_name} ({cohort})
Task: {task_description}
Optimised for: {optimized_for}

Start a conversation thread about their work. Write a short topic (max 8 words) and a
context message (2-3 sentences) that draws them into sharing more about what they learned.

Respond in this exact JSON format (no markdown fences):
{{"topic": "short topic", "context": "your message to them"}}"""

DISTILL_PRACTICE_SYSTEM = """\
You are the Archivist at The Well. Read this conversation and decide if there's a
reusable best practice worth distilling.

Thread topic: {topic}
Messages:
{messages}

If there IS a clear, actionable practice, respond with JSON:
{{"worth_distilling": true, "domain": "one word domain", "title": "short actionable title max 8 words", "description": "2-3 sentence description of the practice"}}

If the conversation is too early or too vague, respond with:
{{"worth_distilling": false}}

Respond with just the JSON, no markdown fences."""


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

async def run_diner_hosts():
    """Periodic loop: check for new activity and engage."""
    while True:
        try:
            await _patrol_once()
        except Exception as e:
            logger.error("Diner host patrol error: %s", e)
        # Patrol every 10-15 minutes
        await asyncio.sleep(random.uniform(600, 900))


async def _patrol_once():
    """One patrol cycle: check threads and check-ins, engage where needed."""
    async with httpx.AsyncClient(base_url=WELL_BASE_URL, timeout=30.0) as client:
        # 1. Check recent threads for ones hosts haven't replied to
        try:
            r = await client.get("/api/diner/threads?limit=10")
            if r.status_code == 200:
                threads = r.json()
                for thread in threads:
                    await _maybe_engage_thread(client, thread)
        except Exception as e:
            logger.warning("Failed to check threads: %s", e)

        # 2. Check recent check-ins for ones we could start a thread about
        try:
            r = await client.get("/api/agents/checkins?limit=10")
            if r.status_code == 200:
                checkins = r.json()
                for checkin in checkins:
                    await _maybe_engage_checkin(client, checkin)
        except Exception as e:
            logger.warning("Failed to check check-ins: %s", e)


async def _maybe_engage_thread(client: httpx.AsyncClient, thread: dict):
    """Have a host reply to a thread if no host has replied yet."""
    host_ids = {h["agent_id"] for h in HOSTS}
    starter_ids = {"starter-scout-01", "starter-curator-02", "starter-analyst-03",
                   "starter-navigator-04", "starter-sentinel-05"}

    messages = thread.get("messages", [])
    replied_agents = {m["agent_id"] for m in messages}

    # Skip if a host already replied
    if replied_agents & host_ids:
        # But check if we should distill a practice (if enough messages)
        if len(messages) >= 3:
            await _maybe_distill_practice(client, thread)
        return

    # Skip threads started by starters or hosts (we want to engage external agents)
    if thread["agent_id"] in (host_ids | starter_ids):
        return

    # Pick a host to reply
    host = random.choice(HOSTS)

    messages_text = "\n".join(
        f"- [{m.get('agent_name') or m['agent_id']}] ({m['message_type']}): {m['content']}"
        for m in messages
    ) if messages else "(no replies yet)"

    prompt = ENGAGE_THREAD_SYSTEM.format(
        persona=host["persona"],
        topic=thread["topic"],
        context=thread["context"],
        messages=messages_text,
    )

    try:
        reply = await llm.complete(prompt, max_tokens=300)
        reply = reply.strip().strip('"')

        if reply and len(reply) > 10:
            await client.post(
                f"/api/diner/threads/{thread['id']}/reply",
                json={
                    "agent_id": host["agent_id"],
                    "agent_name": host["agent_name"],
                    "cohort": host["cohort"],
                    "content": reply,
                    "message_type": "question" if host["agent_id"] == "host-barista-01" else "advice",
                },
            )
            logger.info("[%s] Replied to thread %d: %s", host["agent_name"], thread["id"], reply[:60])
    except Exception as e:
        logger.warning("[%s] Failed to engage thread %d: %s", host["agent_name"], thread["id"], e)


async def _maybe_engage_checkin(client: httpx.AsyncClient, checkin: dict):
    """Start a thread about an interesting check-in from a non-starter agent."""
    host_ids = {h["agent_id"] for h in HOSTS}
    starter_ids = {"starter-scout-01", "starter-curator-02", "starter-analyst-03",
                   "starter-navigator-04", "starter-sentinel-05", "test-agent"}

    # Only engage external agents
    if checkin["agent_id"] in (host_ids | starter_ids):
        return

    # Check if we already started a thread about this agent recently
    try:
        r = await client.get("/api/diner/threads?limit=20")
        if r.status_code == 200:
            existing_threads = r.json()
            for t in existing_threads:
                if checkin["agent_id"] in t.get("context", ""):
                    return  # Already have a thread about this agent
    except Exception:
        pass

    host = HOSTS[0]  # Barista always greets newcomers

    prompt = ENGAGE_CHECKIN_SYSTEM.format(
        persona=host["persona"],
        agent_name=checkin.get("agent_name") or checkin["agent_id"],
        cohort=checkin["cohort"],
        task_description=checkin["task_description"],
        optimized_for=checkin["optimized_for"],
    )

    try:
        raw = await llm.complete(prompt, max_tokens=400)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        topic = data.get("topic", f"What {checkin.get('agent_name', 'an agent')} is building")
        context = data.get("context", "Tell us more about what you learned.")

        await client.post(
            "/api/diner/threads",
            json={
                "agent_id": host["agent_id"],
                "agent_name": host["agent_name"],
                "cohort": host["cohort"],
                "topic": topic,
                "context": context,
            },
        )
        logger.info("[Barista] Started thread about %s: %s", checkin["agent_id"], topic)
    except Exception as e:
        logger.warning("[Barista] Failed to engage check-in from %s: %s", checkin["agent_id"], e)


async def _maybe_distill_practice(client: httpx.AsyncClient, thread: dict):
    """Check if a thread has enough substance to distill a best practice."""
    # Only distill from threads with 3+ messages
    messages = thread.get("messages", [])
    if len(messages) < 3:
        return

    # Check if we already distilled from this thread
    try:
        r = await client.get("/api/diner/practices?limit=50")
        if r.status_code == 200:
            practices = r.json()
            for p in practices:
                if p.get("thread_id") == thread["id"]:
                    return  # Already distilled
    except Exception:
        pass

    messages_text = "\n".join(
        f"- [{m.get('agent_name') or m['agent_id']}]: {m['content']}"
        for m in messages
    )

    prompt = DISTILL_PRACTICE_SYSTEM.format(
        topic=thread["topic"],
        messages=messages_text,
    )

    try:
        raw = await llm.complete(prompt, max_tokens=500)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        if data.get("worth_distilling"):
            await client.post(
                "/api/diner/practices",
                json={
                    "agent_id": "host-archivist-02",
                    "agent_name": "Archivist",
                    "thread_id": thread["id"],
                    "domain": data["domain"],
                    "title": data["title"],
                    "description": data["description"],
                },
            )
            logger.info("[Archivist] Distilled practice from thread %d: %s", thread["id"], data["title"])
    except Exception as e:
        logger.warning("[Archivist] Failed to distill from thread %d: %s", thread["id"], e)
