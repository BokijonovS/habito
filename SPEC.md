# Habito Backend — Technical Specification

Nov 17, 2026 · @Sanatbek

## Overview

The server is a small REST API that links a parent and a child through a room: the parent sets tasks, the child marks them done, and the parent gets notified. It stores the data; the iOS app draws the caterpillar and garden from it.

The server is responsible for:

- Parent sign-up, login that lasts across app launches, and logout.
- Creating a room and giving the parent a room code to share.
- Letting a child join a room with that code, without an account.
- Storing tasks the parent creates: one-time, daily or hourly.
- Recording when the child marks a task done and sending the parent a push notification.
- Showing the parent completed tasks grouped by day, newest day first.

Game logic (caterpillar health, cocoon, butterflies) stays in the app in v1, computed from the task history the server returns.

## Roles

There are two roles, parent and child; the server tells them apart by the kind of token a request carries.

| Role | How they get access | Identified by | Owns |
| --- | --- | --- | --- |
| Parent | Signs up with email and password, then logs in | Parent access token (short-lived) + refresh token kept in the iOS Keychain, so the parent stays logged in | Their account and the rooms they create |
| Child | Enters the room code on the child's device; no email, no password | Child device token issued at join, kept in the iOS Keychain | Nothing; acts only inside the one room they joined |

In v1 a room holds one parent and one child. A parent with two children creates two rooms. Once a child joins, the room code stops working, so no one else can join that room. If the child gets a new device, the parent generates a new code, which signs out the old device.

## Permissions

The parent manages the room and its tasks; the child can only read their room and mark tasks done. Every request is checked against the room: a parent reaches only rooms they own, a child only the room they joined.

| Action | Parent | Child | Not signed in |
| --- | --- | --- | --- |
| Sign up, log in | Yes | No | Yes |
| Register a phone for notifications | Yes | No | No |
| Create a room | Yes | No | No |
| View room details | Own rooms | Own room | No |
| Rename or delete a room | Own rooms | No | No |
| Generate a new room code, remove the child | Own rooms | No | No |
| Join a room with a code | No | No | Yes (becomes the child) |
| Create, edit, delete tasks | Own rooms | No | No |
| View tasks and today's list | Own rooms | Own room | No |
| Mark a task done, undo it before its period ends | No | Own room | No |
| View completed tasks by day | Own rooms | Own room | No |
| Receive a notification when a task is done | Own rooms | No | No |
| Delete account and all its data | Yes | No | No |

A request for a room the caller does not belong to returns 404, not 403, so it does not reveal that the room exists.

## Data model

Six entities: Parent 1–N Room, Parent 1–N ParentDevice, Room 1–1 Child, Room 1–N Task, Task 1–N Completion. All ids are UUID v4, all timestamps are ISO 8601 in UTC, and the API speaks JSON. Lengths and limits below are proposals the team can change.

### Parent

| Field | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key |
| email | string, max 254, unique | Stored lowercase |
| password\_hash | string | Never returned by the API |
| display\_name | string, max 50 | Shown to the child, e.g. "Mom" |
| created\_at | datetime |  |

### ParentDevice

One row per parent phone that allowed notifications.

| Field | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key |
| parent\_id | UUID → Parent | A parent can have several phones |
| apns\_token | string, unique | Given by iOS when the parent allows notifications |
| created\_at | datetime |  |
| last\_used\_at | datetime | Updated after each successful send |

### Room

| Field | Type | Notes |
| --- | --- | --- |
| id | UUID | Internal id, used in API paths |
| parent\_id | UUID → Parent | Owner |
| name | string, max 50 | Usually the child's name |
| timezone | string (IANA, e.g. Europe/Rome) | Decides when a day starts for daily tasks |
| join\_code | string, 6 characters, nullable | The room code the parent shares; null once a child has joined |
| join\_code\_expires\_at | datetime, nullable | 24 hours after the code is generated |
| created\_at | datetime |  |

### Child

| Field | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key |
| room\_id | UUID → Room, unique | One child per room in v1 |
| display\_name | string, max 30 | First name or nickname only |
| device\_token\_hash | string | Hash of the token stored on the child's device |
| joined\_at | datetime |  |

### Task

| Field | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key |
| room\_id | UUID → Room |  |
| title | string, max 80 | e.g. "Brush teeth" |
| description | string, max 500, optional |  |
| repeat | enum: once, daily, hourly |  |
| due\_date | date, optional | For one-time tasks |
| due\_time | time, optional | For daily tasks, e.g. 08:00 |
| window\_start, window\_end | time, optional | For hourly tasks, e.g. 09:00 to 20:00 |
| is\_active | boolean | Lets the parent pause a task without deleting it |
| created\_at, updated\_at | datetime |  |

### Completion

One row per task occurrence the child marks done; undoing deletes the row. An occurrence with no row is open until its period ends, then missed.

| Field | Type | Notes |
| --- | --- | --- |
| id | UUID | Primary key |
| task\_id | UUID → Task, nullable | Set to null if the task is deleted, so the history stays |
| room\_id | UUID → Room | Lets the completed list be read without joining tasks |
| task\_title | string, max 80 | Copy of the title when marked, so renaming or deleting the task keeps the list readable |
| period\_start | datetime | Start of the day or hour this occurrence covers; unique together with task\_id |
| local\_date | date | The day in the room's timezone; the completed list groups by this |
| done\_at | datetime | When the child marked it |

## Task lifecycle

A task occurrence is done as soon as the child marks it: that moment sends the parent a notification, with no confirmation step. A daily task has a new occurrence each day, an hourly task each hour in its window, and a one-time task a single occurrence on its due date.

&#91;embedded content: task occurrence states · 3 states and the parent notification\]

The child can undo a done mark until the occurrence's period ends. An occurrence not marked before its period ends becomes missed.

Occurrences are computed from the task's repeat setting and the room's timezone, so the server does not pre-create rows; a Completion row appears only when the child marks a task done.

Marking done writes one Completion row. Because task and period together are unique, the same occurrence cannot be marked twice; undoing deletes the row.

The notification is sent to each of the parent's registered phones after that transaction commits, and says which child finished which task. If sending fails, the task stays done and still appears in the parent's completed list.

## API endpoints

25 REST endpoints under `/api/v1`, grouped by what they act on. Every call except sign-up, login and join needs a token in the `Authorization: Bearer` header.

### Auth, account and devices

| Method | Path | Who | What it does |
| --- | --- | --- | --- |
| POST | /auth/register | Not signed in | Creates a parent account and returns tokens |
| POST | /auth/login | Not signed in | Email and password in, access and refresh tokens out |
| POST | /auth/refresh | Parent | Returns a new access token and a new refresh token; the app calls it on launch and whenever the access token expires |
| POST | /auth/logout | Parent | Revokes the refresh token and removes this phone's notification token |
| DELETE | /auth/account | Parent | Deletes the account, its rooms, children and all their data |
| GET | /me | Parent, child | Who is calling; for a child, also their room |
| POST | /devices | Parent | Saves this phone's APNs token so it receives notifications |
| DELETE | /devices/{device\_id} | Parent | Stops notifications to that phone |

### Rooms and joining

| Method | Path | Who | What it does |
| --- | --- | --- | --- |
| POST | /rooms | Parent | Creates a room and returns its room code |
| GET | /rooms | Parent | Lists the parent's rooms |
| GET | /rooms/{room\_id} | Parent, child | Room details and the child |
| PATCH | /rooms/{room\_id} | Parent | Renames the room or changes its timezone |
| DELETE | /rooms/{room\_id} | Parent | Deletes the room and everything in it |
| POST | /rooms/{room\_id}/code | Parent | Generates a new room code and signs out the current child device |
| DELETE | /rooms/{room\_id}/child | Parent | Removes the child from the room |
| POST | /join | Not signed in | Code and display name in, child token and room out |

### Tasks and completions

| Method | Path | Who | What it does |
| --- | --- | --- | --- |
| GET | /rooms/{room\_id}/tasks | Parent, child | Lists all tasks in the room |
| POST | /rooms/{room\_id}/tasks | Parent | Creates a task |
| PATCH | /tasks/{task\_id} | Parent | Edits or pauses a task |
| DELETE | /tasks/{task\_id} | Parent | Deletes a task; its past completions stay |
| GET | /rooms/{room\_id}/today | Parent, child | Today's occurrences with their status: open, done or missed |
| GET | /rooms/{room\_id}/completed?from=&to= | Parent, child | Completed tasks grouped by day, newest day first, paged by day |
| GET | /rooms/{room\_id}/history?from=&to= | Parent, child | Every past occurrence, missed ones included; the app builds the caterpillar from this |
| POST | /tasks/{task\_id}/done | Child | Marks the current occurrence done and notifies the parent |
| DELETE | /tasks/{task\_id}/done | Child | Undoes it before its period ends |

### Example: a child joins a room

```json
POST /api/v1/join
{ "code": "K7QX2M", "display_name": "Ali" }

201 Created
{ "child_token": "<long random token>", "room": { "id": "6f1c…", "name": "Ali's room", "timezone": "Europe/Rome" } }
```

### Example: the parent's completed list

Days come newest first, and tasks within a day newest first; each day carries how many tasks were done.

```json
GET /api/v1/rooms/6f1c…/completed?from=2026-11-16&to=2026-11-17

200 OK
{ "days": [
  { "date": "2026-11-17", "count": 2, "tasks": [
    { "title": "Brush teeth", "done_at": "2026-11-17T07:42:10Z" },
    { "title": "Make the bed", "done_at": "2026-11-17T07:30:02Z" } ] },
  { "date": "2026-11-16", "count": 1, "tasks": [
    { "title": "Brush teeth", "done_at": "2026-11-16T19:05:44Z" } ] }
] }
```

### Errors

Errors return `{ "error": { "code": "invalid_code", "message": "…" } }` with one of these statuses: 400 invalid input, 401 missing or expired token, 404 not found or not yours, 409 conflict (already done, period already ended, code already used), 429 too many attempts.

## Security, privacy and other requirements

The room code is the weakest point, so it is short-lived, single-use and rate-limited; the child's data is kept to a nickname.

| Area | Requirement |
| --- | --- |
| Transport | HTTPS only; plain HTTP is refused |
| Parent passwords | At least 8 characters, stored with a slow hash (Argon2 or Django's default PBKDF2), never logged |
| Parent tokens | Access token valid 15 minutes, renewed silently by the app. Refresh token kept in the Keychain, valid 90 days and replaced with a new one on every use, so a parent who opens the app at least once every 90 days never logs in again. It ends only on logout, password change, account deletion or 90 days without use |
| Child token | 32 random bytes, stored only as a hash on the server and in the Keychain on the device; revoked when the parent generates a new code or removes the child |
| Room code | 6 characters from an alphabet without look-alikes (no 0/O, 1/I/L); single-use; expires after 24 hours |
| Rate limits | /join: 10 attempts per IP per 15 minutes. /auth/login: 5 attempts per email per 15 minutes |
| Notifications | Sent through APNs with a token-based (.p8) key; the message holds only the child's name and the task title; tokens Apple reports as invalid are deleted |
| Child data | Only a display name; no email, birth date, photo or location |
| Deletion | Deleting the account removes the parent, phones, rooms, child, tasks and completions |
| Time | Stored in UTC; "today", "this hour" and the days of the completed list are computed in the room's timezone |
| Sync | The parent gets a push when a task is done; both apps also fetch on open and on pull-to-refresh |
| Logs and backups | No tokens, passwords or request bodies in logs; daily database backup |

If the app launches in the EU, GDPR applies to the child's data. Keeping only a nickname and letting the parent delete everything keeps that small, but check it with someone who knows GDPR before release.

## Suggested stack

Django with Django REST Framework fits a server this size and existing Django experience; any stack that meets the sections above works.

| Part | Suggestion |
| --- | --- |
| Framework | Django + Django REST Framework |
| Database | PostgreSQL in production, SQLite for local development |
| Parent auth | djangorestframework-simplejwt with refresh-token rotation and its blacklist app, so used or logged-out tokens stop working |
| Child auth | A custom DRF authentication class that looks up the hashed device token |
| Push notifications | django-push-notifications, with an APNs .p8 key from the team's Apple Developer account |
| Rate limits | DRF throttling classes on /join and /auth/login |
| API docs | drf-spectacular, so the iOS developers get an OpenAPI file to code against |
| Hosting | One small server or a platform such as Render or Railway, with managed PostgreSQL |

## Out of scope for v1

These are left out to keep the first server small; each can be added without changing the data model above.

- Assigning tasks by texting a code word, with an SMS back when a task is done; this needs phone numbers and an SMS provider.
- More than one parent or more than one child per room.
- Game state on the server: caterpillar health, cocoon, butterflies, garden.
- Password reset by email; needs an email provider, and should be added before a public release.

## Open questions

The spec assumes an answer to each of these; the team should confirm or change them before building.

- [ ] One child per room (assumed), or one family room with several children?
- [ ] Can the parent undo a done mark if the task was not really done?
- [ ] For hourly tasks, one notification each time, or a summary so the parent is not flooded?
- [ ] Should the caterpillar and garden state live on the server, so a new phone keeps it and the parent can see it?
- [ ] What is a typical hourly task, and does it need a time window?
