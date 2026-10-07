# LinkedIn Post Scheduler

A small application that connects one LinkedIn member, stores a text post, and publishes it at a chosen time. The browser does not schedule or call LinkedIn. PostgreSQL is the source of truth. A Celery worker publishes.

## 1. Project overview

An operator opens the React app, connects LinkedIn, and schedules one text post. FastAPI stores the row as `SCHEDULED`. Celery Beat asks the worker, every 15 seconds, to claim posts whose time has arrived. The worker calls LinkedIn's Posts API and records `PUBLISHED` or `FAILED`.

There is no password login. One local operator owns the connection and the posts.

## 2. Features

- LinkedIn OAuth 2.0 authorization code flow
- Encrypted token storage
- Schedule, list, fetch, and delete a text post
- Background publishing with Celery Beat
- Exponential backoff with jitter for temporary LinkedIn errors
- Database locks so two workers cannot publish the same post
- Health check for PostgreSQL and Redis
- Dashboard, connection, schedule, and posts screens

## 3. Architecture

```text
Browser  ->  FastAPI  ->  PostgreSQL
                |
                +--> LinkedIn (OAuth only)

Celery Beat -> Redis -> Celery worker -> PostgreSQL
                              |
                              +--> LinkedIn Posts API
```

The full diagrams are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Endpoint details are in section 16 and in Swagger at `/docs`.

## 4. Technology stack

- Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2
- PostgreSQL 16, Redis 7, Celery and Celery Beat
- httpx for LinkedIn
- React, Vite, TypeScript
- Docker Compose

## 5. Prerequisites

- Docker Desktop
- A LinkedIn developer application with the Share on LinkedIn product and the `w_member_social` scope
- Python 3.12 only if you want to run pytest on the host

## 6. LinkedIn Developer App setup

1. Create an app at [LinkedIn Developers](https://www.linkedin.com/developers/).
2. Add the Share on LinkedIn product.
3. Under Auth, add this redirect URL: `http://localhost:8080/api/auth/linkedin/callback`
4. Copy the client id and client secret into `.env`. Do not put them in the frontend.

The app requests `openid` in addition to `w_member_social` because the current member-identity endpoint is OpenID Connect userinfo. It does not call the deprecated `/v2/me` or `ugcPosts` APIs.

## 7. OAuth configuration

`GET /api/auth/linkedin` sends the browser to LinkedIn with a random `state`. That value is stored in server memory and in an httpOnly cookie. The callback accepts the code only when the cookie and the server record match, then deletes the state so it cannot be reused.

The callback exchanges the code on the server and stores the tokens encrypted. The browser is redirected to `http://localhost:5173/connection?linkedin=connected`.

## 8. Environment variables

Copy `.env.example` to `.env`. Leave secrets out of git.

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Host-side database URL for pytest. Compose sets its own URL for containers. |
| `REDIS_URL` | Celery broker. Compose uses `redis://redis:6379/0`. |
| `LINKEDIN_CLIENT_ID` | OAuth client id |
| `LINKEDIN_CLIENT_SECRET` | OAuth client secret. Server only. |
| `LINKEDIN_REDIRECT_URI` | Must be `http://localhost:8080/api/auth/linkedin/callback` for Compose. |
| `LINKEDIN_VERSION` | Posts API version, `YYYYMM`. Default `202609`. |
| `SECRET_KEY` | Reserved for this demo. Not used to store tokens. |
| `TOKEN_ENCRYPTION_KEY` | Fernet key for OAuth tokens. |
| `FRONTEND_URL` | `http://localhost:5173`. `FRONTEND_ORIGIN` is accepted as the same setting. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Local Compose database. Default user and database name are `scheduler` / `linkedin_scheduler`. |

Generate the encryption key:

```powershell
py -3.12 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 9. Local setup

From `backend`, with PostgreSQL already listening on `127.0.0.1:5433`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8080
```

Pytest uses the database named `linkedin_scheduler_test` on the same server.

## 10. Docker setup

From the repository root:

```powershell
docker compose up --build
```

| Service | URL or role |
| --- | --- |
| frontend | http://localhost:5173 |
| backend | http://localhost:8080 |
| Swagger | http://localhost:8080/docs |
| postgres | host port 5433 |
| redis | host port 6379 |
| worker | Celery worker |
| beat | Celery Beat |

The API is published on port 8080 so it does not collide with another local service on port 8000.

## 11. Database migration

Compose runs `alembic upgrade head` when the API container starts. On the host, from `backend`:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

The revision is `0001_initial`.

## 12. Running backend

Compose starts it. On the host:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8080
```

`GET /health` returns `{"status":"ok"}` when PostgreSQL and Redis answer.

## 13. Running Celery worker

```powershell
celery -A app.workers.celery_app:celery_app worker --loglevel=info
```

Run that command from `backend` with the virtualenv active.

## 14. Running Celery Beat

```powershell
celery -A app.workers.celery_app:celery_app beat --loglevel=info
```

Beat sends `process_due_posts` every 15 seconds. Celery does not retry that task. Retries are rows in PostgreSQL.

## 15. Running frontend

Compose starts the Vite dev server. On the host, from `frontend`:

```powershell
npm install
npm run dev
```

Set `VITE_API_URL=http://localhost:8080` if the API is not on that origin. The frontend has no LinkedIn secret and no access token.

## 16. API endpoints

Interactive docs: http://localhost:8080/docs

There is no end-user login. The operator is whoever can reach the API.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | PostgreSQL and Redis connectivity |
| `GET` | `/api/auth/linkedin` | Start OAuth. Redirects to LinkedIn. |
| `GET` | `/api/auth/linkedin/callback` | Finish OAuth and redirect to the UI. |
| `POST` | `/api/posts/schedule` | Store a future text post. |
| `GET` | `/api/posts` | List posts, newest schedule time first. |
| `GET` | `/api/posts/{id}` | Fetch one post. |
| `DELETE` | `/api/posts/{id}` | Delete a post LinkedIn has not accepted. |
| `GET` | `/api/dashboard` | Connection flag and counts. |
| `GET` | `/api/linkedin/status` | `{"connected": true}` or false. |

### GET /health

- Authentication: none
- Response `200`: `{"status":"ok"}`
- Response `503`: `{"status":"unavailable","dependency":"postgres"}` or `"redis"`

### GET /api/auth/linkedin

- Authentication: none. This starts authentication.
- Response `302`: redirect to `https://www.linkedin.com/oauth/v2/authorization`
- Response `503`: `{"error":"not_configured","message":"LinkedIn OAuth is not configured"}` when the client id or secret is empty

### GET /api/auth/linkedin/callback

- Authentication: the `state` query parameter must match the httpOnly cookie and the server record
- Success `302`: `{FRONTEND_URL}/connection?linkedin=connected`
- Failure `302`: `{FRONTEND_URL}/connection?linkedin_error=invalid_state` (or `expired_state`, `token_exchange_failed`, `access_denied`)
- The JSON body never contains the access token or client secret

### POST /api/posts/schedule

- Authentication: a connected LinkedIn account must already exist
- Request:

```json
{"content":"Hello from the scheduler.","scheduled_at":"2026-10-08T10:00:00+05:30"}
```

- Response `201`:

```json
{
  "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "content": "Hello from the scheduler.",
  "scheduled_at": "2026-10-08T04:30:00Z",
  "status": "scheduled",
  "attempt_count": 0,
  "last_error": null,
  "created_at": "2026-10-07T18:00:00Z"
}
```

- `409` no connected account
- `422` empty content, content over 3000 characters, missing timezone, or a time that is not in the future
- Error body: `{"error":"validation_error","message":"..."}` or `{"error":"conflict","message":"A connected LinkedIn account is required"}`

### GET /api/posts

- Authentication: connected account
- Query: optional `status` of `scheduled`, `processing`, `published`, or `failed`
- Response `200`: array of post objects, ordered by `scheduled_at` descending
- `409` no account. `422` unknown status

### GET /api/posts/{id}

- Authentication: connected account, and the post must belong to that user
- `200` one post. `404` missing. `409` no account

### DELETE /api/posts/{id}

- Authentication: same as fetch
- `204` deleted when the post is not published and has no LinkedIn post id
- `404` missing. `409` published, or no account

Errors never include a stack trace. The server log has the trace, with tokens redacted.

## 17. Scheduling flow

1. The UI converts the local date and time to a timezone-aware timestamp.
2. `POST /api/posts/schedule` validates the text and the time, creates an idempotency key, and inserts `SCHEDULED`.
3. Beat runs `process_due_posts`.
4. One SQL `UPDATE` claims due rows: `SCHEDULED` becomes `PROCESSING`, `attempt_count` increases, and other workers skip locked rows.
5. The worker locks the row again. It calls LinkedIn only when the status is still `PROCESSING` and `linkedin_post_id` is empty.
6. A successful response stores the LinkedIn post id and `PUBLISHED` in one `UPDATE`.

Times are stored in UTC. The UI formats them in the browser's timezone.

## 18. Retry strategy

`MAX_RETRIES = 4`. The first publish runs when the post is due. Each retry waits a jittered delay centered on 5, 15, 45, and 120 seconds (±20%). The publish after the 120-second wait is the last one. If it fails, the row is `FAILED`, `attempt_count` is 5, and `next_retry_at` is cleared.

Temporary failures: HTTP 429, 500, 502, 503, 504, timeout, and connection errors.

Permanent failures, which are not retried: HTTP 400, 401, 403, 422, invalid token, and invalid permissions.

Celery `max_retries` is 0. A redelivered task loads the row and stops when a LinkedIn id is already stored.

## 19. Duplicate protection

1. `idempotency_key` is unique for the life of the row.
2. `SELECT … FOR UPDATE SKIP LOCKED` so two workers cannot claim the same row.
3. The status change `SCHEDULED` → `PROCESSING` is one `UPDATE`.
4. Before HTTP, the worker reloads the locked row and returns if it is `PUBLISHED` or already has a LinkedIn id.
5. After HTTP 201, one `UPDATE` stores the id and `PUBLISHED` only while the id is still null.
6. Celery does not blindly retry the task.

LinkedIn's current Posts API documents idempotent deletion. It does not document an idempotency key on `POST /rest/posts`. If the process dies after LinkedIn accepts the post and before the id is committed, a later retry can create a second post. That window is documented in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 20. Error handling

API clients receive:

```json
{"error":"linkedin_api_error","message":"LinkedIn temporarily unavailable"}
```

Validation, authentication, LinkedIn, database, and unexpected errors each have a handler. Stack traces stay in the server logs. Logs are JSON and include `event`, `request_id`, `post_id`, `user_id`, `status`, `attempt_count`, and `duration` when those values exist. Access tokens, refresh tokens, client secrets, and authorization codes are redacted.

## 21. Testing

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Markers: `unit`, `api`, `database`, `linkedin`, `celery`.

```powershell
.\.venv\Scripts\python.exe -m pytest -m celery
```

LinkedIn tests use mocked HTTP. They do not call LinkedIn. Frontend check:

```powershell
cd frontend
npm install
npm run build
```

## 22. Troubleshooting

- `GET /health` returns redis unavailable: start Redis, or use Compose.
- OAuth returns `not_configured`: set `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET`, then recreate the API container.
- LinkedIn redirects with `invalid_state`: start OAuth from `http://localhost:8080/api/auth/linkedin`, not from a different host. The state cookie is tied to that host and port.
- A post stays `PROCESSING`: the worker stopped after the claim and before it wrote the outcome. Do not publish that row by hand until you know whether LinkedIn accepted it.
- Port 8080 or 5173 is in use: stop the other process or change the Compose port and `LINKEDIN_REDIRECT_URI` together.

## 23. Security considerations

Details are in [docs/SECURITY.md](docs/SECURITY.md).

- Secrets live in `.env`, which is gitignored.
- Tokens are encrypted with Fernet before they are stored.
- The frontend never receives the client secret or the access token.
- CORS allows only `FRONTEND_URL`, plus the localhost / 127.0.0.1 twin of that origin.
- SQL uses SQLAlchemy bound parameters.
- The local Compose database password defaults to `scheduler`. Change `POSTGRES_PASSWORD` before using this anywhere but a private machine.

## 24. Demo instructions

1. Put LinkedIn credentials and a Fernet key in `.env`.
2. `docker compose up --build`
3. Open http://localhost:5173
4. Open LinkedIn, click Connect LinkedIn, approve the app, and confirm the page says Connected.
5. Schedule a post one or two minutes ahead.
6. Open Posts and click Refresh until the badge says `PUBLISHED`.
7. Show http://localhost:8080/docs and http://localhost:8080/health
8. Optional: `cd backend` and run `pytest` to show the mocked LinkedIn cases, retries, and the duplicate-worker tests.

A live publish needs a real LinkedIn app. Without credentials, Connect LinkedIn returns the not-configured error, and the pytest suite still demonstrates publishing with mocked LinkedIn responses.
