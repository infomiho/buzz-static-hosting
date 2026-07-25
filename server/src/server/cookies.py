from fastapi import Response

COOKIE_NAME = "__Host-buzz_session"
DEV_COOKIE_NAME = "buzz_session"
ACCESS_COOKIE_NAME = "__Host-buzz_access"
DEV_ACCESS_COOKIE_NAME = "buzz_access"


def session_cookie_name(secure: bool) -> str:
    return COOKIE_NAME if secure else DEV_COOKIE_NAME


def set_session_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie(
        key=session_cookie_name(secure),
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=30 * 24 * 3600,
    )


def clear_session_cookie(response: Response, secure: bool) -> None:
    response.delete_cookie(
        key=session_cookie_name(secure),
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


def access_cookie_name(secure: bool) -> str:
    return ACCESS_COOKIE_NAME if secure else DEV_ACCESS_COOKIE_NAME


def set_access_cookie(response: Response, token: str, secure: bool, max_age: int) -> None:
    response.set_cookie(
        key=access_cookie_name(secure),
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
        max_age=max_age,
    )
