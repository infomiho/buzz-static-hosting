from fastapi import Response

COOKIE_NAME = "buzz_session"


def set_session_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=30 * 24 * 3600,
    )


def clear_session_cookie(response: Response, secure: bool) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
