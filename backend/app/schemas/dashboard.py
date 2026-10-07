from pydantic import BaseModel


class LinkedInStatusResponse(BaseModel):
    connected: bool


class DashboardResponse(BaseModel):
    linkedin_connected: bool
    scheduled_count: int
    published_count: int
    failed_count: int
