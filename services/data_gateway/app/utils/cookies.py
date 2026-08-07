"""Cookie deletion that survives an ``HTTPException`` — SSO-3.

Both SSO callbacks take an injected ``Response`` and, on their error paths, call
``response.delete_cookie(...)`` and then raise ``HTTPException``. That deletion
never reaches the browser: FastAPI merges the injected response's headers into
the real one only AFTER the endpoint returns normally, and raising skips the
merge entirely. The call read like a control and was a no-op.

``HTTPException(headers=...)`` IS applied to the error response, so the fix is to
carry the Set-Cookie header on the exception itself.
"""

from __future__ import annotations

from fastapi import Response


def delete_cookie_headers(
    name: str, *, path: str = "/", domain: str | None = None
) -> dict[str, str]:
    """Return headers that expire *name*, for ``HTTPException(headers=...)``.

    ``domain`` must match what was used to SET the cookie: a Set-Cookie with no
    Domain attribute cannot expire a cookie that was written with one, so
    omitting it here would reintroduce the no-op in a subtler form.

    Built by letting Starlette format the header rather than hand-writing the
    expiry string, so the attribute set stays identical to ``set_cookie``'s.
    """
    probe = Response()
    probe.delete_cookie(name, path=path, domain=domain)
    return {"set-cookie": probe.headers["set-cookie"]}
