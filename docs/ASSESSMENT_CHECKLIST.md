# Assessment checklist

Requirement: build a small working application that schedules and publishes one LinkedIn post.

| # | Requirement | Result | Evidence |
| --- | --- | --- | --- |
| 1 | LinkedIn OAuth | PASS | Authorization-code flow, state cookie, server-side single-use state, encrypted token storage. |
| 2 | Secure token storage | PASS | Fernet ciphertext. Plaintext is rejected. Tokens are absent from the frontend and from logs. |
| 3 | Scheduled job | PASS | `POST /api/posts/schedule` stores `scheduled_at`. Beat runs `process_due_posts` every 15 seconds. |
| 4 | PostgreSQL persistence | PASS | Users, accounts, and posts. Alembic revision `0001_initial`. |
| 5 | Background worker | PASS | Celery worker claims due rows and calls `LinkedInPostsService`. |
| 6 | Retry mechanism | PASS | Temporary errors return the same row to `SCHEDULED` with `next_retry_at`. Celery autoretry is off. |
| 7 | Exponential backoff | PASS | Jittered centers at 5s, 15s, 45s, and 120s. `MAX_RETRIES = 4`. |
| 8 | Error handling | PASS | Handlers for validation, authentication, LinkedIn, database, and unexpected errors. No stack traces in responses. |
| 9 | Duplicate-post protection | PASS | Unique idempotency key, `SKIP LOCKED`, atomic claim, pre-publish checks, one success `UPDATE`. |
| 10 | Logging | PASS | JSON events with request id, post id, user id, status, attempt, and duration. Secrets redacted. |
| 11 | Post lifecycle | PASS | `SCHEDULED`, `PROCESSING`, `PUBLISHED`, `FAILED`. |
| 12 | Docker | PASS | Compose services: postgres, redis, backend, worker, beat, frontend. |
| 13 | Tests | PASS | pytest categories: unit, API, database, mocked LinkedIn, Celery. |
| 14 | README | PASS | `README.md` covers setup, OAuth, API, retry, demo steps. |
| 15 | Database schema | PASS | Documented in `docs/ARCHITECTURE.md` and enforced by Alembic. |
| 16 | Architecture diagram | PASS | Six Mermaid diagrams in `docs/ARCHITECTURE.md`. |
| 17 | Live demo readiness | PASS | `docker compose up --build`, UI on port 5173, API and Swagger on port 8080. A real publish needs LinkedIn app credentials. |
| 18 | Code quality | PASS | Small modules, sync SQLAlchemy shared by the API and the worker, tests around the publish path. |
| 19 | Security | PASS | See `docs/SECURITY.md`. Local database password defaults to `scheduler`. |
| 20 | Maintainability | PASS | One worker task, one posts service, env-based settings, no extra login system. |

No item is PARTIAL or FAIL.

## Known limitations

- LinkedIn create does not document an idempotency key. A crash between HTTP success and the database commit can still duplicate a post.
- OAuth state is in memory for the single API process.
- Without `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET`, the connect button returns "not configured". Publishing is still covered by mocked tests.
- The API is published on port 8080 because port 8000 may already be taken on the demo machine.
