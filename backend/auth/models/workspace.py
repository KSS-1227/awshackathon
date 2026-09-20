from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum


class MemberRole(str, Enum):
    """Member role within a workspace."""
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class WorkspaceResponse(BaseModel):
    """Workspace object returned from API."""
    workspace_id: str = Field(..., alias="workspace_id")
    name: str
    owner_id: str
    created_at: datetime
    updated_at: datetime
    member_count: Optional[int] = None
    role: Optional[str] = None

    class Config:
        from_attributes = True
        populate_by_name = True


class WorkspaceCreateRequest(BaseModel):
    """Request body for creating a new workspace."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1024)


class MemberInviteRequest(BaseModel):
    """Request body for inviting a member to a workspace."""
    email: str = Field(..., description="Email address of user to invite")
    role: MemberRole = Field(default=MemberRole.VIEWER, description="Role to assign")


class MemberRoleChangeRequest(BaseModel):
    """Request body for changing a member's role in a workspace."""
    role: MemberRole = Field(..., description="New role for the member")
