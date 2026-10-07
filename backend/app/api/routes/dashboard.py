"""Connection status and dashboard counts. No tokens are returned."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.dashboard import DashboardResponse, LinkedInStatusResponse
from app.services.scheduler.schedule_service import linkedin_is_connected, post_counts

router = APIRouter(tags=["dashboard"])


@router.get("/api/linkedin/status", response_model=LinkedInStatusResponse)
def linkedin_status(session: Session = Depends(get_db)) -> LinkedInStatusResponse:
    return LinkedInStatusResponse(connected=linkedin_is_connected(session))


@router.get("/api/dashboard", response_model=DashboardResponse)
def dashboard(session: Session = Depends(get_db)) -> DashboardResponse:
    counts = post_counts(session)
    return DashboardResponse(
        linkedin_connected=linkedin_is_connected(session),
        scheduled_count=counts["scheduled"],
        published_count=counts["published"],
        failed_count=counts["failed"],
    )
