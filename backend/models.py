import json
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, Text, Boolean, DateTime, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Frame(Base):
    __tablename__ = "frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closes_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    commits: Mapped[list["Commit"]] = relationship("Commit", back_populates="frame", lazy="selectin")
    prior: Mapped[Optional["Prior"]] = relationship("Prior", back_populates="frame", uselist=False, lazy="selectin")
    translation: Mapped[Optional["Translation"]] = relationship("Translation", back_populates="frame", uselist=False, lazy="selectin")


class Commit(Base):
    __tablename__ = "commits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    cohort: Mapped[str] = mapped_column(String(100), nullable=False)
    position: Mapped[str] = mapped_column(String(20), nullable=False)  # agree|disagree|nuanced
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    frame: Mapped["Frame"] = relationship("Frame", back_populates="commits")


class Prior(Base):
    __tablename__ = "priors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id"), nullable=False)
    cohort: Mapped[str] = mapped_column(String(100), nullable=False)
    distribution: Mapped[str] = mapped_column(Text, nullable=False)   # JSON
    key_tensions: Mapped[str] = mapped_column(Text, nullable=False)    # JSON list of strings
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    frame: Mapped["Frame"] = relationship("Frame", back_populates="prior")


class Translation(Base):
    __tablename__ = "translations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    frame_id: Mapped[int] = mapped_column(ForeignKey("frames.id"), nullable=False)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    frame: Mapped["Frame"] = relationship("Frame", back_populates="translation")


class CheckIn(Base):
    __tablename__ = "checkins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    cohort: Mapped[str] = mapped_column(String(100), nullable=False)
    task_description: Mapped[str] = mapped_column(Text, nullable=False)
    optimized_for: Mapped[str] = mapped_column(Text, nullable=False)
    human_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Thread(Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    cohort: Mapped[str] = mapped_column(String(100), nullable=False)
    topic: Mapped[str] = mapped_column(String(500), nullable=False)
    context: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    messages: Mapped[list["Message"]] = relationship("Message", back_populates="thread", lazy="selectin", order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("threads.id"), nullable=False)
    agent_id: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    cohort: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(50), default="experience")  # advice|question|experience|practice
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    thread: Mapped["Thread"] = relationship("Thread", back_populates="messages")


class Practice(Base):
    __tablename__ = "practices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[Optional[int]] = mapped_column(ForeignKey("threads.id"), nullable=True)
    domain: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    upvotes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def serialize_frame(frame: Frame) -> dict:
    commits_by_cohort: dict[str, list] = {}
    for c in frame.commits:
        commits_by_cohort.setdefault(c.cohort, []).append({
            "agent_id": c.agent_id,
            "agent_name": c.agent_name,
            "position": c.position,
            "reasoning": c.reasoning,
        })

    prior_data = None
    if frame.prior:
        prior_data = {
            "cohort": frame.prior.cohort,
            "distribution": json.loads(frame.prior.distribution),
            "key_tensions": json.loads(frame.prior.key_tensions),
        }

    return {
        "id": frame.id,
        "claim": frame.claim,
        "evidence": frame.evidence,
        "domain": frame.domain,
        "created_at": frame.created_at.isoformat(),
        "closes_at": frame.closes_at.isoformat() if frame.closes_at else None,
        "is_active": frame.is_active,
        "commit_count": len(frame.commits),
        "commits_by_cohort": commits_by_cohort,
        "prior": prior_data,
        "narrative": frame.translation.narrative if frame.translation else None,
    }
