"""ORM models.

Importing this package registers every model on ``Base.metadata``, which is
what Alembic (and ``Base.metadata.create_all``) relies on. Add new models by
creating a module in this package and re-exporting it here.
"""

from app.db.models.block import Block
from app.db.models.contact import Contact
from app.db.models.conversation import Conversation
from app.db.models.conversation_member import ConversationMember
from app.db.models.health_check import HealthCheck
from app.db.models.media import Media
from app.db.models.message import Message
from app.db.models.token_blocklist import TokenBlocklist
from app.db.models.user import PROFILE_VISIBILITY_VALUES, ProfileVisibility, User
from app.db.models.user_session import UserSession
from app.db.models.verification_token import VerificationPurpose, VerificationToken

__all__ = [
    "Block",
    "Contact",
    "Conversation",
    "ConversationMember",
    "HealthCheck",
    "Media",
    "Message",
    "PROFILE_VISIBILITY_VALUES",
    "ProfileVisibility",
    "TokenBlocklist",
    "User",
    "UserSession",
    "VerificationPurpose",
    "VerificationToken",
]
