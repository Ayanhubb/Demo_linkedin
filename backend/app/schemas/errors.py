from pydantic import BaseModel, ConfigDict


class ErrorResponse(BaseModel):
    """Safe API error. The message is for a person. The server log holds the trace."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": "linkedin_api_error",
                    "message": "LinkedIn temporarily unavailable",
                }
            ]
        }
    )

    error: str
    message: str
