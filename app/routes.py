from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.dependencies import DB, Admin, CurrentUser, Writer
from app.models import Comment, Role, Tenant, Ticket, TicketStatus, User
from app.schemas import (
    Assign,
    CommentCreate,
    CommentOut,
    Login,
    Register,
    TicketCreate,
    TicketOut,
    TicketPage,
    TicketPatch,
    TokenOut,
    UserCreate,
    UserOut,
)
from app.security import create_token, dummy_hash, password_hasher

router = APIRouter()


def commit(db):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Resource conflicts with existing data") from None


@router.post("/auth/register", response_model=UserOut, status_code=201, tags=["Auth"])
def register(payload: Register, db: DB):
    """Create a new tenant and its initial admin. Existing tenants cannot be joined publicly."""
    tenant = Tenant(name=payload.tenant_name)
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        email=str(payload.email).lower(),
        name=payload.name,
        password_hash=password_hasher.hash(payload.password),
        role=Role.admin,
    )
    db.add(user)
    commit(db)
    db.refresh(user)
    return user


def authenticate(email: str, password: str, request: Request, db) -> TokenOut:
    email = email.strip().lower()
    try:
        retry = request.app.state.login_limiter.check(
            email, request.client.host if request.client else "unknown"
        )
    except RedisError:
        raise HTTPException(503, "Login temporarily unavailable") from None
    if retry:
        raise HTTPException(429, "Too many login attempts", headers={"Retry-After": str(retry)})
    user = db.scalar(select(User).where(User.email == email))
    valid = password_hasher.verify(password, user.password_hash if user else dummy_hash)
    if not valid or user is None:
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    settings = request.app.state.settings
    return TokenOut(
        access_token=create_token(user, settings), expires_in=settings.access_token_minutes * 60
    )


@router.post("/auth/login", response_model=TokenOut, tags=["Auth"])
def login(payload: Login, request: Request, db: DB):
    """Get an expiring access token. Redis limits attempts by account and client IP."""
    return authenticate(str(payload.email), payload.password, request, db)


@router.post("/auth/token", response_model=TokenOut, tags=["Auth"])
def token(request: Request, db: DB, form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    """Swagger Authorize: enter email as username. Uses the same limiter as JSON login."""
    if len(form.username) > 254 or len(form.password) > 128:
        raise HTTPException(422, "Credentials exceed maximum length")
    return authenticate(form.username, form.password, request, db)


@router.get("/auth/me", response_model=UserOut, tags=["Auth"])
def me(user: CurrentUser):
    return user


@router.post("/users", response_model=UserOut, status_code=201, tags=["Users"])
def create_user(payload: UserCreate, db: DB, user: Admin):
    """Admin provisions an account in their own tenant."""
    new_user = User(
        email=str(payload.email).lower(),
        name=payload.name,
        role=payload.role,
        password_hash=password_hasher.hash(payload.password),
        tenant_id=user.tenant_id,
        created_by=user.id,
    )
    db.add(new_user)
    commit(db)
    db.refresh(new_user)
    return new_user


@router.get("/users", response_model=list[UserOut], tags=["Users"])
def list_users(
    db: DB,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """List tenant members to choose an assignee; password hashes are never returned."""
    return db.scalars(
        select(User)
        .where(User.tenant_id == user.tenant_id)
        .order_by(User.created_at, User.id)
        .offset(offset)
        .limit(limit)
    ).all()


def get_ticket(db, user: User, ticket_id: UUID) -> Ticket:
    ticket = db.scalar(
        select(Ticket).where(
            Ticket.id == str(ticket_id),
            Ticket.tenant_id == user.tenant_id,
            Ticket.deleted_at.is_(None),
        )
    )
    if ticket is None:
        raise HTTPException(404, "Ticket not found")
    return ticket


@router.post("/tickets", response_model=TicketOut, status_code=201, tags=["Tickets"])
def create_ticket(payload: TicketCreate, db: DB, user: Writer):
    ticket = Ticket(**payload.model_dump(), tenant_id=user.tenant_id, created_by=user.id)
    db.add(ticket)
    commit(db)
    db.refresh(ticket)
    return ticket


@router.get("/tickets", response_model=TicketPage, tags=["Tickets"])
def list_tickets(
    db: DB,
    user: CurrentUser,
    status: TicketStatus | None = None,
    assignee_id: UUID | None = None,
    sort: Literal["created_at", "updated_at", "title"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    filters = [Ticket.tenant_id == user.tenant_id, Ticket.deleted_at.is_(None)]
    if status is not None:
        filters.append(Ticket.status == status.value)
    if assignee_id is not None:
        filters.append(Ticket.assignee_id == str(assignee_id))
    column = getattr(Ticket, sort)
    ordering = column.asc() if order == "asc" else column.desc()
    total = db.scalar(select(func.count()).select_from(Ticket).where(*filters))
    items = db.scalars(
        select(Ticket).where(*filters).order_by(ordering, Ticket.id).offset(offset).limit(limit)
    ).all()
    return TicketPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/tickets/{ticket_id}", response_model=TicketOut, tags=["Tickets"])
def read_ticket(ticket_id: UUID, db: DB, user: CurrentUser):
    return get_ticket(db, user, ticket_id)


@router.patch("/tickets/{ticket_id}", response_model=TicketOut, tags=["Tickets"])
def update_ticket(ticket_id: UUID, payload: TicketPatch, db: DB, user: Writer):
    ticket = get_ticket(db, user, ticket_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(ticket, field, value)
    commit(db)
    db.refresh(ticket)
    return ticket


@router.delete("/tickets/{ticket_id}", status_code=204, tags=["Tickets"])
def delete_ticket(ticket_id: UUID, db: DB, user: Admin):
    """Soft delete a ticket and hide its comments from all API reads."""
    ticket = get_ticket(db, user, ticket_id)
    ticket.deleted_at = datetime.now(UTC)
    commit(db)
    return Response(status_code=204)


@router.post("/tickets/{ticket_id}/assign", response_model=TicketOut, tags=["Tickets"])
def assign_ticket(ticket_id: UUID, payload: Assign, db: DB, user: Writer):
    ticket = get_ticket(db, user, ticket_id)
    if payload.assignee_id is not None:
        assignee = db.scalar(
            select(User).where(
                User.id == str(payload.assignee_id),
                User.tenant_id == user.tenant_id,
                User.role.in_([Role.admin, Role.agent]),
            )
        )
        if assignee is None:
            raise HTTPException(404, "Eligible assignee not found")
    ticket.assignee_id = str(payload.assignee_id) if payload.assignee_id else None
    commit(db)
    db.refresh(ticket)
    return ticket


@router.post("/tickets/{ticket_id}/close", response_model=TicketOut, tags=["Tickets"])
def close_ticket(ticket_id: UUID, db: DB, user: Writer):
    ticket = get_ticket(db, user, ticket_id)
    ticket.status = TicketStatus.closed
    commit(db)
    db.refresh(ticket)
    return ticket


@router.post(
    "/tickets/{ticket_id}/comments", response_model=CommentOut, status_code=201, tags=["Comments"]
)
def add_comment(ticket_id: UUID, payload: CommentCreate, db: DB, user: Writer):
    ticket = get_ticket(db, user, ticket_id)
    comment = Comment(
        body=payload.body, ticket_id=ticket.id, tenant_id=user.tenant_id, created_by=user.id
    )
    db.add(comment)
    commit(db)
    db.refresh(comment)
    return comment


@router.get("/tickets/{ticket_id}/comments", response_model=list[CommentOut], tags=["Comments"])
def list_comments(
    ticket_id: UUID,
    db: DB,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    ticket = get_ticket(db, user, ticket_id)
    return db.scalars(
        select(Comment)
        .where(
            Comment.tenant_id == user.tenant_id,
            Comment.ticket_id == ticket.id,
            Comment.deleted_at.is_(None),
        )
        .order_by(Comment.created_at, Comment.id)
        .offset(offset)
        .limit(limit)
    ).all()


def get_comment(db, user: User, ticket_id: UUID, comment_id: UUID) -> Comment:
    ticket = get_ticket(db, user, ticket_id)
    comment = db.scalar(
        select(Comment).where(
            Comment.id == str(comment_id),
            Comment.ticket_id == ticket.id,
            Comment.tenant_id == user.tenant_id,
            Comment.deleted_at.is_(None),
        )
    )
    if comment is None:
        raise HTTPException(404, "Comment not found")
    return comment


@router.get(
    "/tickets/{ticket_id}/comments/{comment_id}", response_model=CommentOut, tags=["Comments"]
)
def read_comment(ticket_id: UUID, comment_id: UUID, db: DB, user: CurrentUser):
    return get_comment(db, user, ticket_id, comment_id)


@router.patch(
    "/tickets/{ticket_id}/comments/{comment_id}", response_model=CommentOut, tags=["Comments"]
)
def update_comment(
    ticket_id: UUID,
    comment_id: UUID,
    payload: CommentCreate,
    db: DB,
    user: Writer,
):
    """Authors and tenant admins can edit comments."""
    comment = get_comment(db, user, ticket_id, comment_id)
    if user.role != Role.admin and comment.created_by != user.id:
        raise HTTPException(403, "Only the author or admin can edit this comment")
    comment.body = payload.body
    commit(db)
    db.refresh(comment)
    return comment


@router.delete("/tickets/{ticket_id}/comments/{comment_id}", status_code=204, tags=["Comments"])
def delete_comment(ticket_id: UUID, comment_id: UUID, db: DB, user: Writer):
    """Authors and tenant admins can soft delete comments."""
    comment = get_comment(db, user, ticket_id, comment_id)
    if user.role != Role.admin and comment.created_by != user.id:
        raise HTTPException(403, "Only the author or admin can delete this comment")
    comment.deleted_at = datetime.now(UTC)
    commit(db)
    return Response(status_code=204)
