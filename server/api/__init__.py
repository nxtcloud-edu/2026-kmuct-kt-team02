"""Top-level API router composition."""

from fastapi import APIRouter

from server.api.chat import router as chat_router
from server.api.health import router as health_router
from server.api.policies import router as policies_router
from server.api.sessions import router as sessions_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(sessions_router)
api_router.include_router(chat_router)
api_router.include_router(policies_router)

__all__ = ["api_router"]
