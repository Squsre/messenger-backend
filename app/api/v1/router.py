"""Aggregate router for API version 1.

Every feature module exposes its own ``router``; they are all mounted here so
that :mod:`app.main` only needs to include a single router.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.endpoints import account, auth, blocks, contacts, health, users

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router)
api_router.include_router(account.router)
api_router.include_router(users.router)
api_router.include_router(contacts.router)
api_router.include_router(blocks.router)
