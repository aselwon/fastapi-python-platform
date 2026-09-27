from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.models import Role, User

oauth2 = OAuth2PasswordBearer(tokenUrl="auth/token")


def get_db(request: Request):
    with request.app.state.session_factory() as session:
        yield session


DB = Annotated[Session, Depends(get_db)]


def current_user(request: Request, db: DB, token: Annotated[str, Depends(oauth2)]) -> User:
    unauthorized = HTTPException(
        status_code=401, detail="Invalid or expired token", headers={"WWW-Authenticate": "Bearer"}
    )
    try:
        claims = jwt.decode(
            token,
            request.app.state.settings.jwt_secret,
            algorithms=["HS256"],
            issuer="opsticket",
            options={"require": ["sub", "tenant_id", "exp", "iat", "iss"]},
        )
        if not isinstance(claims["sub"], str) or not isinstance(claims["tenant_id"], str):
            raise unauthorized
    except jwt.InvalidTokenError:
        raise unauthorized from None
    user = db.get(User, claims["sub"])
    if user is None or user.tenant_id != claims["tenant_id"]:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def writer(user: CurrentUser) -> User:
    if user.role not in (Role.admin, Role.agent):
        raise HTTPException(403, "Agent or admin role required")
    return user


def admin(user: CurrentUser) -> User:
    if user.role != Role.admin:
        raise HTTPException(403, "Admin role required")
    return user


Writer = Annotated[User, Depends(writer)]
Admin = Annotated[User, Depends(admin)]
