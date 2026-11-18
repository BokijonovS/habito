# Habito backend

REST API for the Habito iOS app, v1. A parent registers, gets a room code, and creates one-time or daily tasks. The child's device joins with the code, pulls today's tasks and marks them done, and the parent's phone gets a push. [SPEC.md](SPEC.md) describes the API; this file covers running it and lists the decisions made along the way.

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

The API is at `http://127.0.0.1:8000/api/v1/` and the interactive docs at `http://127.0.0.1:8000/api/docs/`. The OpenAPI file for the iOS developers is at `http://127.0.0.1:8000/api/schema/`, or:

```bat
python manage.py spectacular --file openapi.yaml
```

Run the tests (108 of them, under 10 seconds):

```bat
python manage.py test
```

Optional, for the Django admin at `/admin/`:

```bat
python manage.py createsuperuser
```

### PyCharm

Settings → Project → Python Interpreter → Add → Existing → pick `.venv\Scripts\python.exe`. Then Settings → Languages & Frameworks → Django: enable Django support, project root is this folder, settings file is `config\settings.py`. The green Run button then starts the server and PyCharm's test runner can run `manage.py test`. `.env` is read on start, so run configurations need no extra environment variables.

### macOS or Linux

Same steps with `python3 -m venv .venv`, `source .venv/bin/activate` and `cp .env.example .env`.

## Configuration

Everything comes from `.env` (never committed; `.env.example` lists every key).

| Key | Meaning |
| --- | --- |
| `SECRET_KEY` | Required. Any long random string. |
| `DEBUG` | `true` locally. `false` turns on HTTPS-only, secure cookies and HSTS. |
| `ALLOWED_HOSTS` | Comma-separated hostnames. |
| `DATABASE_URL` | Empty for SQLite. `postgres://user:pass@host:5432/habito` for PostgreSQL. |
| `HABITO_PUSH_BACKEND` | `console` (default, prints to the log), `memory` (tests) or `apns`. |
| `APNS_AUTH_KEY_PATH`, `APNS_AUTH_KEY_ID`, `APNS_TEAM_ID`, `APNS_TOPIC`, `APNS_USE_SANDBOX` | Only for `apns`: the `.p8` key, its id, the team id, the app's bundle id, sandbox or production. |
| `THROTTLE_JOIN`, `THROTTLE_LOGIN` | Rate limits, default `10/15m` and `5/15m`. |
| `LOG_LEVEL` | Default `INFO`. |

## Project layout

```
config/          settings, URL root, error format, throttle base, shared test helpers
accounts/        Parent (custom user: email, room_code, child_name, timezone), ParentDevice, Child; auth, /me, /devices, /join
tasks/           Task, Completion; which tasks are due which day (days.py); /tasks, /today, /done, /history
notifications/   push backends (memory, console, apns) and the "task done" message
```

Each app has a `tests/` package. The suite covers every endpoint, every endpoint against every role (`config/tests/test_permissions.py`), the error format, the OpenAPI file, and the day boundary in a non-UTC timezone.

## The flow in short

1. Parent app: register or login with email and password. The app also sends the device timezone in the same call so the server knows when a day starts. The response carries the `room_code` to show on screen.
2. Parent app registers its APNs token with `POST /devices`.
3. Parent creates tasks with `POST /tasks`, edits them with `PATCH /tasks/{id}`, and reads them, with statuses, from `GET /tasks`. `GET /history` gives, per day, the counts and the task names with their status.
4. Child app: `POST /join` with the code, keep the `child_token`. Then `GET /today` and `POST /tasks/{id}/done`.
5. The parent's phones get a push with the task title.

## Decisions

Things the brief left open, and what the server does. Each is small and can be changed.

1. **One parent is one room.** There is no room entity. A parent with two children needs two accounts in v1.
2. **The room code is permanent.** It is generated at registration and never changes. Anyone who learns it can join and replace the child device; the parent sees nothing until the child's app stops working. Fine for a first family release, but a "new code" endpoint is the first thing to add if that bites.
3. **Rejoining replaces the child device.** A second join with the same code signs the previous device out. This is how a lost or reinstalled phone recovers.
4. **The child's name is the parent's note, not child data.** `child_name` (optional, 40 characters) is typed by the parent at sign-up or later via `PATCH /me`, and only labels the screen. The child's device sends only the code. The push says "Task done" with the task title.
5. **Timezone is an optional field on register and login.** The iOS app should send `TimeZone.current.identifier`. If it never does, the parent is in UTC and "today" rolls over at midnight UTC.
6. **Statuses.** `open` (due today, not marked), `done`, `missed` (a past day with no mark), `upcoming` (a one-time task with a future due date). `GET /tasks` shows daily tasks against today and one-time tasks against their due date.
7. **A daily task starts the day it is created** in the parent's timezone, so no missed days are backfilled.
8. **A one-time task without `due_date`** is due today.
9. **A done mark needs no confirmation, and only the parent can undo it** (changed 2026-11-18, was final). Undo is `DELETE /tasks/{id}/done` and removes the mark of the day the parent sees. Marking the same task twice on the same day is a 409; after an undo the child can mark it again.
10. **History counts a deleted task's completions** in both `total` and `done` for the days it was done, so numbers never go down when a task is removed. Days with no tasks still appear with zeros.
11. **History range** defaults to the last seven days ending today and refuses more than 31 days.
12. **Wrong password is 401 `invalid_credentials`**, a bad timezone on login is 400.
13. **`/auth/refresh` needs no access token**, since the app calls it exactly when that token has expired.
14. **`POST /devices`** returns 201 for a new token and 200 for a repeat; a token that belonged to another parent moves to the caller.
15. **Deleting the account** also removes refresh-token rows, which simplejwt otherwise keeps with a null user.
16. **django-push-notifications' `[APNS]` extra is broken on Python 3.10+**, so requirements use its `[apns-async]` extra (aioapns). Its own device tables exist but are unused; `ParentDevice` is ours.
17. **The real APNs backend has not been run against Apple** (no key yet). It is covered by a test with Apple's responses mocked.
18. **Join rate limit is per IP.** Behind a reverse proxy, set `NUM_PROXIES` in `REST_FRAMEWORK` or the limit counts the proxy's address.
19. **HTTPS-only** is on whenever `DEBUG=false`; the proxy that terminates TLS must send `X-Forwarded-Proto: https`.
20. **Editing a task** (added 2026-11-18) changes the task in place, so a renamed daily task keeps its history. Old completions keep the title they were marked with. Switching once → daily clears the due date; daily → once without a date makes it due today.

21. **Task description** (added 2026-11-18): optional text up to 500 characters on a task, set at creation or by edit.
22. **History lists the tasks of each day** (added 2026-11-18) with `done`, `open`, `missed` or `upcoming`. A done task shows the title it was marked with; a deleted task's completion still appears as `done`.

Not in v1: hourly tasks, pausing tasks, several children per parent, rotating the room code, a per-task completed list, password reset, game state on the server.
