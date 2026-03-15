from fastapi import APIRouter
from pydantic import BaseModel
from .. import learning_path as lp
from .. import user_progress as up

router = APIRouter(prefix="/learning", tags=["learning"])


class CompleteModuleRequest(BaseModel):
    score: int


@router.get("/curriculum")
def get_curriculum():
    """Full curriculum tree with completion/lock status."""
    return lp.get_curriculum()


@router.get("/module/{module_id}")
def get_module(module_id: str):
    """Full module detail including quiz questions."""
    mod = lp.get_module(module_id)
    if not mod:
        return {"error": "Module not found"}, 404
    return mod


@router.post("/module/{module_id}/complete")
def complete_module(module_id: str, req: CompleteModuleRequest):
    result = lp.complete_module(module_id, req.score)
    if not result.get("error"):
        xp_result = up.award_xp("module_complete", note=module_id)
        result["progress"] = xp_result
    return result
