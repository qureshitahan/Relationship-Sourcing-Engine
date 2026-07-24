"""Model package: import all models so SQLAlchemy registers them on Base."""
from app.db.base import Base
from app.models.agent_config import AgentConfig
from app.models.app_setting import AppSetting
from app.models.agent_copy_variant import AgentCopyVariant
from app.models.agent_playbook import AgentPlaybook
from app.models.agent_run import AgentRun
from app.models.agent_variant import AgentVariant
from app.models.audit import AuditLog
from app.models.bulk_campaign import BulkCampaign, BulkChatMessage, BulkLookup
from app.models.call import Call
from app.models.company import Company
from app.models.contact import Contact
from app.models.discovery_run import DiscoveryRun
from app.models.email_draft import EmailDraft
from app.models.linkedin_message import LinkedInMessage
from app.models.principal import Principal
from app.models.principal_document import PrincipalDocument
from app.models.relevance_insight import RelevanceInsight
from app.models.search_definition import SearchDefinition
from app.models.suppression import OutreachHistory, Suppression

__all__ = [
    "Base",
    "AgentConfig",
    "AppSetting",
    "AgentCopyVariant",
    "AgentPlaybook",
    "AgentRun",
    "AgentVariant",
    "AuditLog",
    "BulkCampaign",
    "BulkChatMessage",
    "BulkLookup",
    "Call",
    "Company",
    "Contact",
    "DiscoveryRun",
    "EmailDraft",
    "LinkedInMessage",
    "Principal",
    "PrincipalDocument",
    "RelevanceInsight",
    "SearchDefinition",
    "OutreachHistory",
    "Suppression",
]
