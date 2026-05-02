from __future__ import annotations

from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    """
    Common message envelope used by both public agent responses and responder output.
    """

    message: str = Field(min_length=1, max_length=700)


__all__ = ["MessageResponse"]
