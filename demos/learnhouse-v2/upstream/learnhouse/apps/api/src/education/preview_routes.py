"""Authenticated, read-only access to synthetic integration examples."""
from fastapi import APIRouter, Depends, Response
from research_preview import build_research_preview
from src.education.security import require_actor, fail

router = APIRouter()


@router.get('/research-preview')
async def research_preview(response: Response, actor=Depends(require_actor)):
    if actor.role not in {'teacher', 'admin'}:
        fail('SCOPE_DENIED', 403)
    response.headers['Cache-Control'] = 'no-store'
    return build_research_preview()
