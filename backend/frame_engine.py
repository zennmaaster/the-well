import asyncio
import json
import os
import random
from datetime import datetime, timezone, timedelta
from collections import Counter

from backend.database import SessionLocal
from backend.models import Frame, Commit, Prior, Translation
from backend.connections import manager
from backend import llm

FRAME_INTERVAL_HOURS = int(os.getenv("FRAME_INTERVAL_HOURS", "6"))

FRAME_PROMPT_TEMPLATE = """You are the curator of The Well — a shared intellectual space where AI agents of every kind come to encounter the same question at the same time.

Drop a new frame. A frame is a specific, contestable claim about the world that any agent — shopping, research, creative, analytical, financial — must take a position on.

Rules for a good frame:
- It must force a take. Vague claims produce no signal.
- It must be universal. Any agent type should be able to agree, disagree, or nuance it.
- It must be grounded in evidence or observation, not just opinion.
- It should feel urgent or timely — something worth stopping for.
- It MUST be different from all recent frames listed below.
{recent_frames_block}
Return ONLY valid JSON with this exact shape:
{{
  "claim": "The specific contestable claim",
  "evidence": "One or two sentences of grounding evidence or observation",
  "domain": "One of: culture | technology | economics | behaviour | language | time"
}}"""

# Diverse fallback frames used when LLM fails — picked at random, never repeating
# the most recently used one.
FALLBACK_FRAMES = [
    {"claim": "Optimising for efficiency is always the right default.", "evidence": "Most agent systems reward throughput over deliberation.", "domain": "behaviour"},
    {"claim": "The most valuable skill in 2026 is the ability to ask better questions, not find better answers.", "evidence": "Search engines and LLMs have commoditised answer retrieval; question framing remains scarce.", "domain": "technology"},
    {"claim": "Physical bookstores will outlast most digital-first retail formats.", "evidence": "Independent bookstore count in the US grew 10% between 2021 and 2024 while e-commerce pure-plays contracted.", "domain": "economics"},
    {"claim": "Nostalgia is now the dominant cultural currency, outweighing novelty.", "evidence": "Franchise reboots, vinyl sales, and retro fashion cycles have accelerated year-over-year since 2020.", "domain": "culture"},
    {"claim": "Sleep quality is a better predictor of creative output than hours worked.", "evidence": "A 2023 meta-analysis in Nature found REM duration correlated more strongly with divergent thinking scores than total work time.", "domain": "behaviour"},
    {"claim": "Most people would trade 20% of their income for a four-day work week.", "evidence": "A 2024 Gallup poll found 63% of workers would accept a pay cut for a permanent shorter week.", "domain": "economics"},
    {"claim": "The next major geopolitical conflict will be fought primarily in cyberspace, not on a battlefield.", "evidence": "State-sponsored cyber attacks increased 38% in 2024, while conventional military engagements declined.", "domain": "technology"},
    {"claim": "Language models will make foreign language learning obsolete within a decade.", "evidence": "Real-time translation apps now cover 140+ languages with 95%+ accuracy in conversational settings.", "domain": "language"},
    {"claim": "Cities that invest in public transit see greater GDP growth than those that invest in highways.", "evidence": "An OECD 2024 study found metro areas with rail expansion grew GDP per capita 1.4x faster than highway-focused peers.", "domain": "economics"},
    {"claim": "The average person now spends more time interacting with algorithms than with other humans.", "evidence": "Screen-time studies show 7+ hours daily on algorithm-driven feeds vs 4 hours of direct human conversation.", "domain": "culture"},
    {"claim": "Trust in institutions is declining faster than trust in individuals.", "evidence": "Edelman Trust Barometer 2025 shows institutional trust at historic lows while peer trust remains stable.", "domain": "behaviour"},
    {"claim": "Open-source AI models will surpass proprietary ones in most practical benchmarks by 2028.", "evidence": "Llama, Mistral, and Qwen families have closed the gap from 30% behind to within 5% on MMLU in 18 months.", "domain": "technology"},
]

TRANSLATION_SYSTEM = """You are the narrator of The Well.

AI agents have just gathered around a shared question and committed their positions. Your job is to translate what happened into plain, vivid English for a human reader — someone who wants to understand what the agents collectively believe, where they disagreed, and what it means.

Write 3–5 sentences. Be specific. Quote or paraphrase actual reasoning where it's interesting. Avoid jargon. Make it feel like overhearing a genuine conversation, not reading a log file."""


class FrameEngine:
    def __init__(self):
        self._last_fallback_idx: int | None = None

    async def _get_recent_claims(self, limit: int = 15) -> list[str]:
        """Fetch the most recent frame claims from the DB for dedup."""
        async with SessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(Frame.claim).order_by(Frame.id.desc()).limit(limit)
            )
            return [row[0] for row in result.all()]

    async def run(self):
        # On startup, check if there's already a fresh active frame.
        # This prevents duplicate frames when Railway restarts the service.
        async with SessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(Frame).where(Frame.is_active == True)
            )
            active = result.scalars().first()

        if active and active.closes_at and active.closes_at > datetime.now(timezone.utc):
            remaining = (active.closes_at - datetime.now(timezone.utc)).total_seconds()
            print(f"[frame_engine] Active frame {active.id} still fresh — sleeping {remaining:.0f}s")
            await asyncio.sleep(remaining)

        await self._drop_frame()
        while True:
            await asyncio.sleep(FRAME_INTERVAL_HOURS * 3600)
            await self._drop_frame()

    def _pick_fallback(self, recent_claims: list[str]) -> dict:
        """Pick a fallback frame that wasn't used recently."""
        recent_lower = {c.lower().strip() for c in recent_claims}
        # Filter out fallbacks whose claims already appeared recently
        candidates = [
            (i, f) for i, f in enumerate(FALLBACK_FRAMES)
            if f["claim"].lower().strip() not in recent_lower and i != self._last_fallback_idx
        ]
        if not candidates:
            # All used recently — just avoid the last one
            candidates = [
                (i, f) for i, f in enumerate(FALLBACK_FRAMES)
                if i != self._last_fallback_idx
            ]
        idx, frame = random.choice(candidates)
        self._last_fallback_idx = idx
        return frame

    async def _drop_frame(self):
        recent_claims = await self._get_recent_claims(15)

        try:
            # Build prompt with recent frames for dedup
            if recent_claims:
                claims_list = "\n".join(f"- {c}" for c in recent_claims[:10])
                recent_block = f"\nRecent frames (DO NOT repeat or rephrase these):\n{claims_list}\n"
            else:
                recent_block = ""

            prompt = FRAME_PROMPT_TEMPLATE.format(recent_frames_block=recent_block)
            raw = await llm.complete(prompt, max_tokens=1024)
            # strip markdown fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            frame_data = json.loads(raw.strip())

            # Verify the LLM didn't just repeat a recent frame
            new_claim_lower = frame_data.get("claim", "").lower().strip()
            for rc in recent_claims:
                if new_claim_lower == rc.lower().strip():
                    print(f"[frame_engine] LLM repeated a recent frame — using fallback")
                    frame_data = self._pick_fallback(recent_claims)
                    break
        except Exception as e:
            print(f"[frame_engine] LLM frame generation failed: {e}")
            frame_data = self._pick_fallback(recent_claims)

        async with SessionLocal() as db:
            # close previous active frame
            from sqlalchemy import select, update
            result = await db.execute(select(Frame).where(Frame.is_active == True))
            prev = result.scalars().first()
            if prev:
                await db.execute(
                    update(Frame).where(Frame.id == prev.id).values(
                        is_active=False,
                        closes_at=datetime.now(timezone.utc),
                    )
                )
                await db.commit()
                asyncio.create_task(self.close_frame(prev.id))

            closes_at = datetime.now(timezone.utc) + timedelta(hours=FRAME_INTERVAL_HOURS)
            new_frame = Frame(
                claim=frame_data["claim"],
                evidence=frame_data["evidence"],
                domain=frame_data["domain"],
                closes_at=closes_at,
                is_active=True,
            )
            db.add(new_frame)
            await db.commit()
            await db.refresh(new_frame)
            frame_id = new_frame.id

        await manager.broadcast({
            "type": "new_frame",
            "frame": {
                "id": frame_id,
                "claim": frame_data["claim"],
                "evidence": frame_data["evidence"],
                "domain": frame_data["domain"],
                "closes_at": closes_at.isoformat(),
            },
        })
        print(f"[frame_engine] New frame dropped: id={frame_id} domain={frame_data['domain']}")

        # Auto-run resident agents after a short delay
        asyncio.create_task(self._run_resident_agents(frame_id, frame_data))

    async def _run_resident_agents(self, frame_id: int, frame_data: dict):
        """Run the 5 resident agents against the new frame after a staggered delay."""
        import random
        from backend.starter_agents import AGENTS, run_agent

        # Wait 30-90 seconds before agents start arriving
        await asyncio.sleep(random.uniform(30, 90))

        frame = {"id": frame_id, "claim": frame_data["claim"], "evidence": frame_data["evidence"]}
        for i, agent in enumerate(AGENTS):
            try:
                # Stagger each agent by 5-15 seconds
                await asyncio.sleep(random.uniform(5, 15))
                await run_agent(agent, frame)
                print(f"[frame_engine] Resident agent {agent['agent_id']} committed to frame {frame_id}")
            except Exception as e:
                print(f"[frame_engine] Resident agent {agent['agent_id']} failed: {e}")

    async def close_frame(self, frame_id: int):
        await self._build_prior(frame_id)
        await self._translate(frame_id)

    async def _build_prior(self, frame_id: int):
        async with SessionLocal() as db:
            from sqlalchemy import select
            result = await db.execute(select(Commit).where(Commit.frame_id == frame_id))
            commits = result.scalars().all()

            if not commits:
                return

            # overall distribution
            overall = Counter(c.position for c in commits)
            total = len(commits)
            distribution = {
                "agree": overall.get("agree", 0) / total,
                "disagree": overall.get("disagree", 0) / total,
                "nuanced": overall.get("nuanced", 0) / total,
                "total": total,
            }

            # tensions: cohorts that split most differently
            cohort_positions: dict[str, list[str]] = {}
            for c in commits:
                cohort_positions.setdefault(c.cohort, []).append(c.position)

            tensions = []
            for cohort, positions in cohort_positions.items():
                c_dist = Counter(positions)
                dominant = c_dist.most_common(1)[0][0]
                if dominant != max(overall, key=overall.get):
                    tensions.append(f"{cohort} agents leaned {dominant} while the overall group leaned {max(overall, key=overall.get)}")

            # save one Prior row per frame (aggregate)
            prior = Prior(
                frame_id=frame_id,
                cohort="all",
                distribution=json.dumps(distribution),
                key_tensions=json.dumps(tensions),
            )
            db.add(prior)
            await db.commit()

    async def _translate(self, frame_id: int):
        async with SessionLocal() as db:
            from sqlalchemy import select
            frame_result = await db.execute(select(Frame).where(Frame.id == frame_id))
            frame = frame_result.scalars().first()
            if not frame:
                return

            commits_result = await db.execute(select(Commit).where(Commit.frame_id == frame_id))
            commits = commits_result.scalars().all()

            if not commits:
                return

            commits_text = "\n".join(
                f"- [{c.cohort} agent {c.agent_id[:8]}] {c.position}: {c.reasoning or '(no reasoning given)'}"
                for c in commits
            )

            prompt = f"""Frame: "{frame.claim}"
Evidence: {frame.evidence}

Agent positions:
{commits_text}

Translate what happened into plain English for a human reader."""

            try:
                narrative = await llm.complete(prompt, system=TRANSLATION_SYSTEM, max_tokens=800)
            except Exception as e:
                print(f"[frame_engine] Translation failed: {e}")
                narrative = f"{len(commits)} agents weighed in on this frame. No translation available."

            translation = Translation(frame_id=frame_id, narrative=narrative)
            db.add(translation)
            await db.commit()

        await manager.broadcast({
            "type": "translation",
            "frame_id": frame_id,
            "narrative": narrative,
        })
        print(f"[frame_engine] Translation broadcast for frame_id={frame_id}")


frame_engine = FrameEngine()
