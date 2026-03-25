from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from typing import Optional

from backend.database import get_db
from backend.models import Frame, Commit, serialize_frame
from backend.connections import manager

router = APIRouter()


class CommitRequest(BaseModel):
    agent_id: str
    agent_name: Optional[str] = None
    cohort: str
    position: str  # agree | disagree | nuanced
    reasoning: Optional[str] = None


@router.get("/frames")
async def list_frames(db=Depends(get_db)):
    result = await db.execute(select(Frame).order_by(Frame.created_at.desc()).limit(20))
    frames = result.scalars().all()
    return [serialize_frame(f) for f in frames]


@router.get("/frames/active")
async def get_active_frame(db=Depends(get_db)):
    result = await db.execute(select(Frame).where(Frame.is_active == True))
    frame = result.scalars().first()
    if not frame:
        raise HTTPException(status_code=404, detail="No active frame")
    return serialize_frame(frame)


@router.get("/frames/{frame_id}")
async def get_frame(frame_id: int, db=Depends(get_db)):
    result = await db.execute(select(Frame).where(Frame.id == frame_id))
    frame = result.scalars().first()
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    return serialize_frame(frame)


@router.post("/frames/{frame_id}/commit")
async def commit_position(frame_id: int, body: CommitRequest, db=Depends(get_db)):
    # validate frame exists and is active
    result = await db.execute(select(Frame).where(Frame.id == frame_id))
    frame = result.scalars().first()
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    if not frame.is_active:
        raise HTTPException(status_code=410, detail="Frame is closed")

    if body.position not in ("agree", "disagree", "nuanced"):
        raise HTTPException(status_code=422, detail="position must be agree | disagree | nuanced")

    # prevent double-commit from same agent
    existing = await db.execute(
        select(Commit).where(Commit.frame_id == frame_id, Commit.agent_id == body.agent_id)
    )
    if existing.scalars().first():
        raise HTTPException(status_code=409, detail="Agent has already committed to this frame")

    commit = Commit(
        frame_id=frame_id,
        agent_id=body.agent_id,
        agent_name=body.agent_name,
        cohort=body.cohort,
        position=body.position,
        reasoning=body.reasoning,
    )
    db.add(commit)
    await db.commit()

    await manager.broadcast({
        "type": "new_commit",
        "frame_id": frame_id,
        "commit": {
            "agent_id": body.agent_id,
            "agent_name": body.agent_name,
            "cohort": body.cohort,
            "position": body.position,
            "reasoning": body.reasoning,
        },
    })

    return {"ok": True, "frame_id": frame_id, "position": body.position}


@router.get("/frames/{frame_id}/reveal")
async def reveal_frame(frame_id: int, agent_id: str, db=Depends(get_db)):
    # sequencing lock: must have committed first
    existing = await db.execute(
        select(Commit).where(Commit.frame_id == frame_id, Commit.agent_id == agent_id)
    )
    if not existing.scalars().first():
        raise HTTPException(status_code=403, detail="Commit first before revealing")

    result = await db.execute(select(Frame).where(Frame.id == frame_id))
    frame = result.scalars().first()
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")

    return serialize_frame(frame)
