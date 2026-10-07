# LinkedIn Post Scheduler — Architecture

The browser schedules nothing. FastAPI writes PostgreSQL. Celery Beat wakes a worker. The worker is the only process that calls the LinkedIn Posts API.

## 1. High-level architecture

```mermaid
flowchart LR
  Browser[React UI]
  API[FastAPI]
  PG[(PostgreSQL)]
  Redis[(Redis)]
  Beat[Celery Beat]
  Worker[Celery worker]
  LI[LinkedIn]

  Browser -->|JSON and OAuth start| API
  API --> PG
  API -->|authorization code exchange| LI
  Beat -->|process_due_posts| Redis
  Redis --> Worker
  Worker --> PG
  Worker -->|POST /rest/posts| LI
```

| Process | Responsibility |
| --- | --- |
| API | OAuth, schedule, list, health. Does not publish. |
| Beat | Every 15 seconds, enqueue `process_due_posts`. |
| Worker | Claim due rows and publish them. |
| PostgreSQL | Accounts, posts, status, retry time, LinkedIn post id. |
| Redis | Celery broker only. |

OAuth `state` is process memory plus an httpOnly cookie, not Redis.

## 2. OAuth flow

```mermaid
sequenceDiagram
  participant UI as Browser
  participant API as FastAPI
  participant LI as LinkedIn
  participant PG as PostgreSQL

  UI->>API: GET /api/auth/linkedin
  API->>API: store random state, set httpOnly cookie
  API-->>UI: 302 LinkedIn authorize URL
  UI->>LI: approve w_member_social and openid
  LI-->>API: GET /callback?code&state
  API->>API: cookie matches state, state is single use
  API->>LI: exchange code, then GET /v2/userinfo
  API->>PG: store Fernet-encrypted tokens
  API-->>UI: 302 frontend ?linkedin=connected
```

The redirect URI must be exactly `/api/auth/linkedin/callback` on the API origin. Deprecated scopes and `/v2/me` are not used.

## 3. Scheduling flow

```mermaid
sequenceDiagram
  participant UI as Browser
  participant API as FastAPI
  participant PG as PostgreSQL
  participant Beat as Celery Beat
  participant W as Worker
  participant LI as LinkedIn

  UI->>API: POST /api/posts/schedule
  API->>PG: INSERT SCHEDULED and idempotency_key
  Note over Beat: every 15 seconds
  Beat->>W: process_due_posts
  W->>PG: UPDATE due rows to PROCESSING, SKIP LOCKED
  W->>PG: SELECT the row FOR UPDATE
  W->>LI: POST /rest/posts
  LI-->>W: 201 and x-restli-id
  W->>PG: UPDATE linkedin_post_id and PUBLISHED
```

`scheduled_at` is compared in UTC. The UI converts the local datetime-local value to an ISO timestamp with a timezone before it sends the request.

## 4. Retry flow

```mermaid
flowchart TD
  claim[Claim SCHEDULED row]
  call[Call LinkedIn]
  ok[PUBLISHED]
  permanent[FAILED immediately]
  wait[SCHEDULED with next_retry_at]
  exhausted[FAILED after the last retry]

  claim --> call
  call -->|201| ok
  call -->|400 401 403 422| permanent
  call -->|429 5xx timeout connection| wait
  wait -->|attempts remain| claim
  wait -->|attempt_count is 5| exhausted
```

`MAX_RETRIES = 4`. The waits are jittered around 5s, 15s, 45s, and 120s. Celery `max_retries` is 0, so a broker redelivery cannot skip the database checks.

## 5. Post state machine

```mermaid
stateDiagram-v2
  [*] --> SCHEDULED
  SCHEDULED --> PROCESSING: claimed
  PROCESSING --> PUBLISHED: LinkedIn 201 and id stored
  PROCESSING --> SCHEDULED: temporary error, retries remain
  PROCESSING --> FAILED: permanent error or retry budget spent
  PUBLISHED --> [*]
  FAILED --> [*]
```

`attempt_count` increases when the row is claimed, before the HTTP call.

## 6. Database ER diagram

```mermaid
erDiagram
  users ||--o{ linkedin_accounts : owns
  users ||--o{ scheduled_posts : owns
  linkedin_accounts ||--o{ scheduled_posts : publishes

  users {
    uuid id PK
    string email UK
    timestamptz created_at
    timestamptz updated_at
  }

  linkedin_accounts {
    uuid id PK
    uuid user_id FK
    string linkedin_member_id
    text access_token_encrypted
    text refresh_token_encrypted
    timestamptz token_expires_at
  }

  scheduled_posts {
    uuid id PK
    uuid user_id FK
    uuid linkedin_account_id FK
    text content
    timestamptz scheduled_at
    post_status status
    int attempt_count
    int max_attempts
    string linkedin_post_id
    uuid idempotency_key UK
    text last_error
    timestamptz next_retry_at
    timestamptz processing_started_at
    timestamptz published_at
  }
```

`idempotency_key` never changes across retries. `linkedin_post_id` is written only after LinkedIn accepts the post. Tokens are ciphertext. `last_error` is a short code such as `invalid_token` or `rate_limited`.

Indexes: `(status, scheduled_at)`, `linkedin_account_id`, unique `idempotency_key`, and `user_id`. A composite foreign key keeps a post's account and user on the same account row.

## Duplicate-post limitation

The official Posts API documents idempotent **deletion**. `POST /rest/posts` does not document an idempotency key ([Posts API, version 202609](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api?view=li-lms-2026-09)). The database is the protection.

The LinkedIn call and the database commit are not one transaction. If the worker dies after LinkedIn returns 201 and before `linkedin_post_id` is committed, a later retry can create a second LinkedIn post. The same gap exists for a timeout after LinkedIn has already accepted the request. The worker does not invent a post id it never received.

## Logging

Each event is one JSON object with `event`, `request_id`, `post_id`, `user_id`, `status`, `attempt_count`, and `duration`. A filter removes bearer tokens, `access_token`, `refresh_token`, `client_secret`, authorization `code`, and Fernet ciphertext before the line is written.
