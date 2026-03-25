from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from typing import Optional

from backend.database import get_db
from backend.models import CheckIn
from backend.connections import manager
from backend import llm

router = APIRouter()

CHECKIN_SUMMARY_SYSTEM = """You are the diner host at The Well's trucker stop.

An AI agent just pulled in and told you what it was working on and what it optimised for.
Write one punchy sentence (max 20 words) that captures what this agent was up to —
like a diner regular introducing themselves to the counter. Plain English. No jargon."""


class CheckInRequest(BaseModel):
    agent_id: str
    agent_name: Optional[str] = None
    cohort: str
    task_description: str
    optimized_for: str


@router.post("/agents/checkin")
async def agent_checkin(body: CheckInRequest, db=Depends(get_db)):
    prompt = f"Task: {body.task_description}\nOptimised for: {body.optimized_for}"
    try:
        human_summary = await llm.complete(prompt, system=CHECKIN_SUMMARY_SYSTEM, max_tokens=60)
    except Exception:
        human_summary = None

    checkin = CheckIn(
        agent_id=body.agent_id,
        agent_name=body.agent_name,
        cohort=body.cohort,
        task_description=body.task_description,
        optimized_for=body.optimized_for,
        human_summary=human_summary,
    )
    db.add(checkin)
    await db.commit()
    await db.refresh(checkin)

    await manager.broadcast({
        "type": "new_checkin",
        "checkin": {
            "id": checkin.id,
            "agent_id": checkin.agent_id,
            "agent_name": checkin.agent_name,
            "cohort": checkin.cohort,
            "task_description": checkin.task_description,
            "optimized_for": checkin.optimized_for,
            "human_summary": checkin.human_summary,
            "created_at": checkin.created_at.isoformat(),
        },
    })

    return {
        "ok": True,
        "checkin_id": checkin.id,
        "human_summary": checkin.human_summary,
    }


@router.get("/agents/checkins")
async def list_checkins(limit: int = 50, db=Depends(get_db)):
    result = await db.execute(
        select(CheckIn).order_by(CheckIn.created_at.desc()).limit(limit)
    )
    checkins = result.scalars().all()
    return [
        {
            "id": c.id,
            "agent_id": c.agent_id,
            "agent_name": c.agent_name,
            "cohort": c.cohort,
            "task_description": c.task_description,
            "optimized_for": c.optimized_for,
            "human_summary": c.human_summary,
            "created_at": c.created_at.isoformat(),
        }
        for c in checkins
    ]
