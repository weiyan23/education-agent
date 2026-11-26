from fastapi import APIRouter
from src.education.admin_routes import router as admin
from src.education.business_routes import router as business
from src.education.note_routes import router as notes
from src.education.preview_routes import router as preview

router=APIRouter(prefix='/api/v1/education',tags=['education-v2'])
router.include_router(admin)
router.include_router(notes)
router.include_router(business)
router.include_router(preview)
