import asyncio
from fastapi import APIRouter, BackgroundTasks
from typing import Optional
from .. import video_academy as va
from .. import user_progress as up

router = APIRouter(prefix="/academy", tags=["academy"])


@router.get("/catalogue")
def get_catalogue(track: Optional[str] = None):
    """Full lesson catalogue, optionally filtered by track."""
    return {"lessons": va.get_catalogue(track)}


@router.get("/lesson/{lesson_id}")
def get_lesson(lesson_id: str):
    lesson = va.get_lesson(lesson_id)
    if not lesson:
        return {"error": "Lesson not found"}
    return lesson


@router.post("/lesson/{lesson_id}/generate")
async def generate_lesson(lesson_id: str, background_tasks: BackgroundTasks):
    """
    Trigger Veo video generation for a lesson (runs in background).
    Returns immediately with status=generating.
    Poll GET /academy/lesson/{lesson_id} to check status.
    """
    lesson = va.get_lesson(lesson_id)
    if not lesson:
        return {"error": "Lesson not found"}
    if lesson["status"] == "ready":
        return {"status": "already_ready", "playlist": lesson["playlist"]}
    if lesson["status"] == "generating":
        return {"status": "already_generating"}

    va.set_lesson_status(lesson_id, "generating")

    async def _run_generation():
        from ..video_generation_agent import generate_lesson_video
        try:
            playlist = await generate_lesson_video(
                lesson_id=lesson_id,
                topic=lesson["topic"],
                track=lesson["track"],
                level=str(lesson["level"]),
            )
            va.set_lesson_status(lesson_id, "ready" if playlist else "failed", playlist)
        except Exception as e:
            va.set_lesson_status(lesson_id, "failed")

    background_tasks.add_task(_run_generation)
    return {"status": "generating", "lesson_id": lesson_id,
            "message": "Video generation started. Poll GET /academy/lesson/{id} for progress."}


@router.post("/lesson/{lesson_id}/watch")
def mark_watched(lesson_id: str):
    """Mark a lesson as watched and award XP."""
    va.mark_lesson_watched(lesson_id)
    xp = up.award_xp("lesson_complete", note=lesson_id)
    return {"watched": True, "progress": xp}


@router.get("/tracks")
def get_tracks():
    """Return distinct track names."""
    lessons = va.get_catalogue()
    tracks  = sorted(list({l["track"] for l in lessons}))
    return {"tracks": tracks}
