from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_exception_handlers
from app.api.middleware import RequestContextMiddleware
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.health import router as health_router
from app.api.routes.linkedin_auth import router as linkedin_auth_router
from app.api.routes.posts import router as posts_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.linkedin.oauth_service import InMemoryOAuthStateStore, OAuthStateStore


def create_app(state_store: OAuthStateStore | None = None) -> FastAPI:
    configure_logging()
    app = FastAPI(
        title="LinkedIn Post Scheduler",
        summary="Schedule one text post and publish it to LinkedIn.",
        description=(
            "The API stores the post and the OAuth connection. "
            "A worker publishes it. The browser never sees LinkedIn tokens."
        ),
        version="0.1.0",
    )
    settings = get_settings()
    app.state.oauth_state_store = state_store or InMemoryOAuthStateStore(
        ttl_seconds=settings.oauth_state_ttl_seconds
    )
    install_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_browser_origins(settings.frontend_origin),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(dashboard_router)
    app.include_router(linkedin_auth_router)
    app.include_router(posts_router)
    return app


def _browser_origins(origin: str) -> list[str]:
    """Allow the configured UI origin and the other loopback hostname."""
    if "://localhost" in origin:
        twin = origin.replace("://localhost", "://127.0.0.1", 1)
    elif "://127.0.0.1" in origin:
        twin = origin.replace("://127.0.0.1", "://localhost", 1)
    else:
        return [origin]
    return [origin, twin]


app = create_app()
