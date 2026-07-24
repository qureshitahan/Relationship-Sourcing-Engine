"""Bulk email campaigns: pasted recipient lists + a chat brief -> reviewed sends."""
from app.services.bulk_email.runner import (
    launch_drafting,
    launch_sending,
    recipients_needing_drafts,
)

__all__ = ["launch_drafting", "launch_sending", "recipients_needing_drafts"]
