from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

from app.models import Role, TicketStatus

Name = Annotated[str, Field(min_length=1, max_length=120)]
Password = Annotated[str, StringConstraints(strip_whitespace=False, min_length=12, max_length=128)]
Title = Annotated[str, Field(min_length=1, max_length=200)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Register(Input):
    email: EmailStr
    password: Password
    name: Name
    tenant_name: Name
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "admin@example.com",
                    "password": "Demo-password-123!",
                    "name": "Demo Admin",
                    "tenant_name": "Acme Operations",
                }
            ]
        }
    )


class Login(Input):
    email: EmailStr
    password: Annotated[
        str, StringConstraints(strip_whitespace=False, min_length=1, max_length=128)
    ]
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "email": "admin@example.com",
                    "password": "Demo-password-123!",
                }
            ]
        }
    )


class UserCreate(Input):
    email: EmailStr
    password: Password
    name: Name
    role: Role = Role.viewer


class Output(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(Output):
    id: str
    tenant_id: str
    email: EmailStr
    name: str
    role: Role
    created_by: str | None
    created_at: datetime
    updated_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TicketCreate(Input):
    title: Title
    description: str = Field(default="", max_length=20000)
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "VPN unavailable",
                    "description": "Remote team cannot connect.",
                }
            ]
        }
    )


class TicketPatch(Input):
    title: Title | None = None
    description: str | None = Field(default=None, max_length=20000)
    status: TicketStatus | None = None

    @field_validator("title", "description", "status")
    @classmethod
    def disallow_null(cls, value):
        if value is None:
            raise ValueError("Field cannot be null; omit it to leave unchanged")
        return value


class Assign(Input):
    assignee_id: UUID | None = Field(
        description="Agent/admin UUID from this tenant, or null to unassign"
    )


class TicketOut(Output):
    id: str
    tenant_id: str
    title: str
    description: str
    status: TicketStatus
    assignee_id: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class TicketPage(BaseModel):
    items: list[TicketOut]
    total: int
    limit: int
    offset: int


class CommentCreate(Input):
    body: str = Field(min_length=1, max_length=10000, examples=["Investigating VPN gateway."])


class CommentOut(Output):
    id: str
    tenant_id: str
    ticket_id: str
    body: str
    created_by: str
    created_at: datetime
    updated_at: datetime


class HealthOut(BaseModel):
    status: str


class ReadyOut(HealthOut):
    database: bool
    redis: bool
