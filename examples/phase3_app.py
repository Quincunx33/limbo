"""Runnable Phase 3 integration example.

Set LIMBO_CSRF_SECRET to at least 32 random bytes before starting.
This example uses an in-memory demo identity map; replace it with your own
credential verification and persistent user store before real deployment.
"""
from __future__ import annotations

import os
from pathlib import Path

from limbo import (
    Limbo,
    SessionStore,
    authenticate,
    authorize,
    csrf_protect,
    session_middleware,
)

SECRET = os.environ.get("LIMBO_CSRF_SECRET", "")
if len(SECRET.encode("utf-8")) < 32:
    raise RuntimeError("Set LIMBO_CSRF_SECRET to at least 32 bytes")

app = Limbo(
    max_body_bytes=6 * 1024 * 1024,
    max_upload_file_bytes=5 * 1024 * 1024,
    max_upload_files=3,
    max_form_fields=20,
)
app.middleware(session_middleware(SessionStore(max_age=3600), secure=True))
app.middleware(csrf_protect(SECRET.encode("utf-8"), secure=True))

USERS = {
    1: {"id": 1, "username": "demo", "role": "admin"},
}


def current_user(request):
    return USERS.get(request.session.get("user_id"))


require_login = authenticate(current_user)
require_admin = authorize(lambda request, user: user["role"] == "admin")


@app.get("/")
def home(request):
    return app.html(
        "<h1>Limbo Phase 3 example</h1>"
        "<p>POST /login, then use the CSRF token from the limbo_csrf cookie "
        "in the X-CSRF-Token header for unsafe browser requests.</p>"
    )


@app.post("/login")
def login(request):
    # Demo only: replace with password verification and account throttling.
    request.session["user_id"] = 1
    request.session.regenerate()
    return {"authenticated": True, "user": USERS[1]}


@app.post("/logout", middleware=(require_login,))
def logout(request):
    request.session.destroy()
    return {"authenticated": False}


@app.get("/admin", middleware=(require_login, require_admin))
def admin(request):
    return {"message": "Authorized", "user": request.user}


@app.post("/upload", middleware=(require_login,))
def upload(request):
    uploaded = request.file("file")
    if uploaded is None:
        return app.json({"error": "Missing file field"}, 400)
    destination = Path("uploads")
    destination.mkdir(exist_ok=True)
    saved = uploaded.save(destination, max_bytes=5 * 1024 * 1024)
    return {"filename": saved.name, "size": uploaded.size}


@app.websocket("/ws", origins=("https://localhost",),
               max_message_bytes=64 * 1024, idle_timeout=45)
def websocket(request, connection):
    while True:
        message = connection.receive()
        if message is None:
            break
        connection.send(message)


@app.post("/jobs", middleware=(require_login,))
def enqueue_job(request):
    value = request.json_as(dict).get("value")
    app.background_tasks.submit(lambda item: item.upper(), str(value))
    return {"accepted": True}


if __name__ == "__main__":
    app.run()
