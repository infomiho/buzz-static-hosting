from fastapi import Response

from . import config

COOKIE_NAME = "buzz_session"


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=not config.DEV_MODE,
        samesite="lax",
        path="/",
        max_age=30 * 24 * 3600,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        secure=not config.DEV_MODE,
        samesite="lax",
        path="/",
    )
