from fastapi import APIRouter, Depends
from pydantic import BaseModel
from ..auth import get_current_user, UserInfo
from .. import learning_path as lp
from .. import user_progress as up

router = APIRouter(prefix="/learning", tags=["learning"])


class CompleteModuleRequest(BaseModel):
    score: int


@router.get("/curriculum")
def get_curriculum(user: UserInfo = Depends(get_current_user)):
    return lp.get_curriculum(user.id)


@router.get("/module/{module_id}")
def get_module(module_id: str, user: UserInfo = Depends(get_current_user)):
    mod = lp.get_module(user.id, module_id)
    if not mod:
        return {"error": "Module not found"}
    return mod


@router.post("/module/{module_id}/complete")
def complete_module(module_id: str, req: CompleteModuleRequest,
                    user: UserInfo = Depends(get_current_user)):
    result = lp.complete_module(user.id, module_id, req.score)
    if not result.get("error"):
        xp_result = up.award_xp(user.id, "module_complete", note=module_id)
        result["progress"] = xp_result
    return result
