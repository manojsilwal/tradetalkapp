from fastapi import APIRouter, BackgroundTasks, Depends
from typing import Optional
from ..auth import get_current_user, UserInfo
from .. import video_academy as va
from .. import user_progress as up

router = APIRouter(prefix="/academy", tags=["academy"])


@router.get("/catalogue")
def get_catalogue(track: Optional[str] = None, user: UserInfo = Depends(get_current_user)):
    return {"lessons": va.get_catalogue(user.id, track)}


@router.get("/lesson/{lesson_id}")
def get_lesson(lesson_id: str, user: UserInfo = Depends(get_current_user)):
    lesson = va.get_lesson(user.id, lesson_id)
    if not lesson:
        return {"error": "Lesson not found"}
    return lesson


@router.post("/lesson/{lesson_id}/generate")
async def generate_lesson(lesson_id: str, background_tasks: BackgroundTasks,
                          user: UserInfo = Depends(get_current_user)):
    lesson = va.get_lesson(user.id, lesson_id)
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
        except Exception:
            va.set_lesson_status(lesson_id, "failed")

    background_tasks.add_task(_run_generation)
    return {"status": "generating", "lesson_id": lesson_id,
            "message": "Video generation started. Poll GET /academy/lesson/{id} for progress."}


@router.post("/lesson/{lesson_id}/watch")
def mark_watched(lesson_id: str, user: UserInfo = Depends(get_current_user)):
    va.mark_lesson_watched(user.id, lesson_id)
    xp = up.award_xp(user.id, "lesson_complete", note=lesson_id)
    return {"watched": True, "progress": xp}


@router.get("/tracks")
def get_tracks(user: UserInfo = Depends(get_current_user)):
    lessons = va.get_catalogue(user.id)
    tracks  = sorted(list({l["track"] for l in lessons}))
    return {"tracks": tracks}
