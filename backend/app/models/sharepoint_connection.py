from datetime import datetime

from sqlalchemy import String, Text, false
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamps, UuidPk


class SharePointConnection(UuidPk, Timestamps, Base):
    """Missan's sign-in to SharePoint (decision 83): one for the whole Workbench, made by an admin.

    Without client_id (rule 10, like users): it is a Missan user's own read-only access to
    Missan's SharePoint, used for every client, and holds no client data. Tokens are stored
    encrypted (settings.token_key), never logged."""

    __tablename__ = "sharepoint_connections"

    account_name: Mapped[str] = mapped_column(String(255))  # whose SharePoint access this is
    account_email: Mapped[str] = mapped_column(String(254))
    access_token: Mapped[str] = mapped_column(Text)  # encrypted
    refresh_token: Mapped[str] = mapped_column(Text)  # encrypted
    expires_at: Mapped[datetime]  # when the access token stops working
    connected_by: Mapped[str] = mapped_column(String(128))  # the Workbench user who signed in
    # Microsoft refused the stored sign-in for good: browsing and fetching wait for a new one.
    needs_reconnect: Mapped[bool] = mapped_column(default=False, server_default=false())
    reconnect_reason: Mapped[str | None] = mapped_column(Text)
