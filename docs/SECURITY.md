# Security review

Reviewed against the repository as of this demo. This is a single-operator assessment app. It does not add a second login system.

## Result

| Check | Result |
| --- | --- |
| No secrets committed | Pass. `.env` is gitignored. `.env.example` has empty LinkedIn and Fernet values. |
| No LinkedIn access token in the frontend | Pass. The UI only receives connection status and post fields. |
| No client secret in the frontend | Pass. `LINKEDIN_CLIENT_SECRET` is read only on the server during the token exchange. |
| No tokens in logs | Pass. A logging filter redacts bearer tokens, token fields, the authorization code, and Fernet ciphertext, including traceback text. |
| OAuth state protected against CSRF | Pass. `state` is random, stored server-side, mirrored in an httpOnly cookie, compared in constant time, and deleted on use. |
| Redirect URI validated | Pass. It must be an absolute URL whose path is exactly `/api/auth/linkedin/callback`. Non-local HTTP is rejected. |
| Database credentials only in environment variables | Pass for the running containers. Compose reads `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`. The local default password is `scheduler` when those variables are unset. |
| CORS configured safely | Pass. Only `FRONTEND_URL` / `FRONTEND_ORIGIN` is allowed, plus the localhost / 127.0.0.1 twin of that origin. Methods are GET, POST, DELETE, and OPTIONS. |
| SQL injection prevented | Pass. Queries go through SQLAlchemy bound parameters. Identifiers are not built from request strings. |
| Input validation | Pass. Content length, future timezone-aware `scheduled_at`, and the status filter are validated. |
| Authentication boundaries | Pass for this scope. There is no end-user login. Publishing and OAuth stay on the server. The state cookie is the CSRF check for the OAuth callback. |
| Error responses | Pass. Clients receive `error` and `message`. Stack traces stay in the logs. |
| Docker secrets | Pass with a local caveat. LinkedIn and Fernet values are `${...}` from the environment. The Compose database password defaults to `scheduler` so a private demo starts without extra setup. Set `POSTGRES_PASSWORD` before any shared deployment. |
| `.gitignore` | Pass. It includes `.env`, `__pycache__`, `.pytest_cache`, `node_modules`, and `.venv`. |

## What is stored

Access and refresh tokens are encrypted with Fernet before insert. The key is `TOKEN_ENCRYPTION_KEY`. Models refuse plaintext. `__repr__` does not include token fields.

`last_error` stores a short code (`invalid_token`, `rate_limited`), not the LinkedIn body.

## OAuth

- Authorize URL: `https://www.linkedin.com/oauth/v2/authorization`
- Token URL: `https://www.linkedin.com/oauth/v2/accessToken`
- Identity URL: `https://api.linkedin.com/v2/userinfo`
- Cookie: `linkedin_oauth_state`, HttpOnly, SameSite=Lax, path limited to the callback
- Secure cookie flag is on when the redirect URI is HTTPS

## Residual risk

`POST /rest/posts` does not document an idempotency key. A crash after LinkedIn accepts a post and before the id is saved can produce a second post on retry. The application does not pretend otherwise.

The OAuth state store is in memory. Two API processes would not share it. This demo runs one API process.

The local Postgres password `scheduler` is a demo default, not a production secret.
