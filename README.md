# Habito backend

REST API for the Habito iOS app. A parent creates a room and shares a six-letter code, the child joins with that code, the parent sets tasks, the child marks them done and the parent gets a push notification. [SPEC.md](SPEC.md) is the source of truth; this README covers running the server and lists where the implementation had to decide something the spec left open.

Stack: Python 3.12+, Django 5.2, Django REST Framework, djangorestframework-simplejwt (rotating refresh tokens with blacklist), django-push-notifications (aioapns) for APNs, drf-spectacular for OpenAPI. SQLite locally, PostgreSQL through `DATABASE_URL`.

## Setup on Windows

Open a terminal in the project folder (PowerShell or PyCharm's terminal).

```bat
py -3 -m venv .venv
```

```bat
.venv\Scripts\activate
```

```bat
pip install -r requirements.txt
```

```bat
copy .env.example .env
```

Generate a secret key and paste it into `.env` as `SECRET_KEY=`:

```bat
python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Create the database and run the server:

```bat
python manage.py migrate
```

```bat
python manage.py runserver
```

The API is at `http://127.0.0.1:8000/api/v1/` and the interactive docs at `http://127.0.0.1:8000/api/docs/`. The raw OpenAPI file for the iOS developers is at `http://127.0.0.1:8000/api/schema/`, or write it to disk with:

```bat
python manage.py spectacular --file openapi.yaml
```

Run the tests (181 of them, about 15 seconds):

```bat
python manage.py test
```

Optional, for the Django admin at `/admin/`:

```bat
python manage.py createsuperuser
```

### PyCharm

Settings → Project → Python Interpreter → Add → Existing → pick `.venv\Scripts\python.exe`. Then Settings → Languages & Frameworks → Django: enable Django support, project root is this folder, settings file is `config\settings.py`. After that the green Run button starts the server and PyCharm's test runner can run `manage.py test`.

The `.env` file is read by `config/settings.py` on start, so PyCharm run configurations need no extra environment variables.

### macOS or Linux

Same steps with `python3 -m venv .venv`, `source .venv/bin/activate` and `cp .env.example .env`.

## Configuration

Everything comes from `.env` (never committed; `.env.example` lists every key).

| Key | Meaning |
| --- | --- |
| `SECRET_KEY` | Required. Any long random string. |
| `DEBUG` | `true` locally. `false` turns on HTTPS-only, secure cookies and HSTS. |
| `ALLOWED_HOSTS` | Comma-separated hostnames. |
| `DATABASE_URL` | Empty for SQLite. `postgres://user:pass@host:5432/habito` for PostgreSQL (the `psycopg` driver is already in requirements). |
| `HABITO_PUSH_BACKEND` | `console` (default, prints to the log), `memory` (tests) or `apns`. |
| `APNS_AUTH_KEY_PATH`, `APNS_AUTH_KEY_ID`, `APNS_TEAM_ID`, `APNS_TOPIC`, `APNS_USE_SANDBOX` | Only for `apns`. The `.p8` key from the Apple Developer account, its key id, the team id, the app's bundle id, and whether to use Apple's sandbox gateway. |
| `THROTTLE_JOIN`, `THROTTLE_LOGIN` | Rate limits, default `10/15m` and `5/15m`. |
| `LOG_LEVEL` | Default `INFO`. |

Switching to PostgreSQL is only `DATABASE_URL` plus `python manage.py migrate`.

## Project layout

```
config/          settings, URL root, error format (exceptions.py), throttle base, test helpers
accounts/        Parent (custom user, email login), ParentDevice; register/login/refresh/logout/account, /me, /devices
rooms/           Room, Child; room CRUD, room codes, /join, the child token authentication class
tasks/           Task, Completion; occurrence calculation (occurrences.py), tasks, today, done/undo, completed, history
notifications/   push backends (memory, console, apns) and the "task done" message
```

Each app has a `tests/` package. The suite covers every endpoint, the whole permissions table (`config/tests/test_permissions.py`), the error format, the OpenAPI file, and timezone edge cases (day and hour boundaries in Asia/Tashkent, DST gaps and overlaps in Europe/Rome).

## How the API behaves

- **Auth header** is `Authorization: Bearer <token>` for both roles. A parent sends the JWT access token, a child the 64-character `child_token` from `/join`. The server tells them apart by the token's shape.
- **Errors** are always `{"error": {"code": "...", "message": "..."}}`. Validation errors add `"fields"` with one message per bad input. Rate-limited calls add `"retry_after"` in seconds.
- **Today** (`GET /rooms/{id}/today`) returns `{"date", "occurrences": [...]}`. Each occurrence has `task_id`, `title`, `period_start`, `period_end`, `local_date`, `status` and `done_at`.
- **Completed** (`GET /rooms/{id}/completed?from=&to=`) returns days newest first, tasks within a day newest first, with a `count` per day, as in the spec example. Each task also carries the completion `id`, the `task_id` (null if the task was deleted) and `period_start`.
- **History** (`GET /rooms/{id}/history?from=&to=`) returns every occurrence in the range with its status, in time order.
- **Push** on task done goes to every phone the parent registered, with the title "<child> finished a task" and the task title as the body, plus `type`, `room_id` and `completion_id` in the payload. Tokens Apple reports as dead are deleted.

## Decisions and deviations from SPEC.md

Things the spec left open or that had to change. Each is small; say the word and any of them can be changed.

1. **Status `upcoming`.** The spec names three occurrence statuses: open, done, missed. Today's list for an hourly task also contains the hours that have not started yet; those are returned as `upcoming` so the child is not shown them as doable now. Daily and one-time tasks never produce it.
2. **One-time task without `due_date`** is due on the day it was created, in the room's timezone. The server fills `due_date` in, so the field is never null for a stored one-time task.
3. **Daily tasks** are open the whole local day. `due_time` is kept and returned so the app can order and remind, but the server does not treat the task as missed at that time; only at local midnight.
4. **Hourly window** runs from `window_start` up to but not including `window_end` (09:00 to 20:00 gives eleven occurrences, 09:00 through 19:00). Give both or neither; with neither, every hour of the day. The window cannot cross midnight.
5. **Fields that don't apply** to the chosen `repeat` (a `due_date` on a daily task, a window on a one-time task) are rejected with 400 instead of silently dropped.
6. **Occurrences start when the task is created.** A daily task created on the 10th has no missed occurrence on the 9th, and an hourly task created at 15:30 starts with the 15:00 hour. Nothing is backfilled.
7. **Paused tasks** (`is_active: false`) produce no occurrences in today or history, and marking one done returns 409 `task_paused`. Completions recorded before the pause stay in the completed list.
8. **Deleted tasks** keep their completions in the completed list (title copied, `task_id` null), as the spec says. They do not appear in history, since history is computed from the tasks that exist.
9. **Completed and history ranges** default to the last seven days ending today, and refuse ranges longer than 31 days with 400. The spec says "paged by day" without a format, so this is day-range paging rather than cursors.
10. **Used room code returns 404 `invalid_code`**, not 409. The spec says the code becomes null once a child joins, so the server cannot tell a used code from a wrong one. 409 `code_expired` is returned for a code past its 24 hours, and 409 `code_used` only for the inconsistent case of a room that somehow has both a code and a child.
11. **Wrong password returns 401 `invalid_credentials`.** The spec lists 401 for a missing or expired token and 400 for invalid input; a failed login felt closer to 401.
12. **`/auth/refresh` needs no access token.** The spec lists it under "Parent", but the app calls it exactly when the access token has expired, so the refresh token alone is the credential.
13. **`/auth/logout`** takes `{"refresh": "...", "apns_token": "..."}`. The refresh token is required and is blacklisted; `apns_token` is optional and identifies which phone to remove from notifications.
14. **`POST /devices`** returns 201 for a new token and 200 if the same token is sent again. If the token belonged to another parent (someone else logged in on that phone), it moves to the caller.
15. **Deleting the account** also removes the parent's refresh-token rows; simplejwt otherwise keeps them with a null user.
16. **Password column** is Django's `password` field on the custom user model, which holds the hash the spec calls `password_hash`. It is never returned by the API.
17. **django-push-notifications' `[APNS]` extra is broken on Python 3.10 and newer** (it depends on the abandoned `hyper` library). The requirements use the package's `[apns-async]` extra (aioapns) instead. The package's own device tables are created by its migrations but unused; `ParentDevice` is the table the spec describes.
18. **The real APNs backend has not been exercised against Apple** since there is no key yet. It is covered by a test with Apple's responses mocked. Set `HABITO_PUSH_BACKEND=apns` and the `APNS_*` keys when the key arrives.
19. **Join rate limit is per IP** as the spec says. Behind a reverse proxy that sets `X-Forwarded-For`, set `NUM_PROXIES` in `REST_FRAMEWORK` (see DRF throttling docs) or the limit will count the proxy's address.
20. **Throttle rates** in DRF only understand `5/m`; the project adds a small base class so `.env` can say `5/15m` as the spec does.
21. **HTTPS-only** (`SECURE_SSL_REDIRECT`) is on whenever `DEBUG=false`; the proxy that terminates TLS must send `X-Forwarded-Proto: https`.
22. **Spec open questions** were taken as: one child per room, no parent undo, one notification per hourly completion, no game state on the server, hourly window optional.

Not implemented, as the spec lists them out of scope: SMS code words, multiple parents or children per room, server-side game state, password reset by email.
