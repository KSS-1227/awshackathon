"""
Pydantic models for user profile data.

Used by:
  - backend/auth/services/profile_service.py
  - backend/auth/routes/profile.py
"""
from __future__ import annotations

from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel


class UserProfile(BaseModel):
    """Full user profile returned by GET /profile/{user_id}."""

    user_id: str
    email: str
    display_name: str
    avatar_url: str | None
    preferred_language: str
    preferred_date_format: str
    email_verified: bool
    created_at: datetime


class ProfileUpdateRequest(BaseModel):
    """Partial update payload for PATCH /profile/{user_id}.

    All fields are optional — only non-None fields are written to the DB.
    """

    display_name: str | None = None
    avatar_url: AnyHttpUrl | None = None
    preferred_language: str | None = None
    preferred_date_format: str | None = None
