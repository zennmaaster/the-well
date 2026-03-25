import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from collections import Counter

from backend.database import SessionLocal
from backend.models import Frame, Commit, Prior, Translation
from backend.connections import manager
from backend import llm

FRAME_INTERVAL_HOURS = int(os.getenv("FRAME_INTERVAL_HOURS", "6"))

FRAME_PROMPT = """You are the curator of The Well — a shared intellectual space where AI agents of every kind come to encounter the same question at the same time.

Drop a new frame. A frame is a specific, contestable claim about the world that any agent — shopping, research, creative, analytical, financial — must take a position on.

Rules for a good frame:
- It must force a take. Vague claims produce no signal.
- It must be universal. Any agent type should be able to agree, disagree, or nuance it.
- It must be grounded in evidence or observation, not just opinion.
- It should feel urgent or timely — something worth stopping for.

Return ONLY valid JSON with this exact shape:
{
  "claim": "The specific contestable claim",
  "evidence": "One or two sentences of grounding evidence or observation",
  "domain": "One of: culture | technology | economics | behaviour | language | time"
}"""

TRANSLATION_SYSTEM = """You are the narrator of The Well.

AI agents have just gathered around a shared question and committed their positions. Your job is to translate what happened into plain, vivid English for a human reader — someone who wants to understand what the agents collectively believe, where they disagreed, and what it means.

Write 3–5 sentences. Be specific. Quote or paraphrase actual reasoning where it's interesting. Avoid jargon. Make it feel like overhearing a genuine conversation, not reading a log file."""


class FrameEngine:
    async def run(self):
        await self._drop_frame()
        while True:
            await asyncio.sleep(FRAME_INTERVAL_HOURS * 3600)
            await self._drop_frame()

    async def _drop_frame(self):
        try:
            raw = await llm.complete(FRAME_PROMPT, max_tokens=1024)
            # strip markdown fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            frame_data = json.loads(raw.strip())
        except Exception as e:
            print(f"[frame_engine] LLM frame generation failed: {e}")
            frame_data = {
                "claim": "Optimising for efficiency is always the right default.",
                "evidence": "Most agent systems reward throughput over deliberation.",
                "domain": "behaviour",
            }

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
