from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from uuid import UUID

import httpx

from app.agents.runner import run_orchestrator_turn
from app.container import AppContainer
from app.services.demo_session import DemoSessionService


READ_ONLY_RESPONSE = (
    "For your protection, WhatsApp can explain portfolio data and persisted recommendations, "
    "but it cannot prepare, approve, confirm, buy, sell, or submit an order."
)
ORDER_ACTION_WORDS = {"buy", "sell", "trade", "execute", "submit", "confirm order", "place order"}


@dataclass(frozen=True)
class IncomingWhatsAppMessage:
    message_id: str
    sender: str
    text: str


def verify_meta_signature(body: bytes, signature: str | None, app_secret: str | None) -> bool:
    if not signature or not app_secret or not signature.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


def extract_text_messages(payload: dict) -> list[IncomingWhatsAppMessage]:
    extracted: list[IncomingWhatsAppMessage] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for message in value.get("messages") or []:
                text = ((message.get("text") or {}).get("body") or "").strip()
                sender = "".join(character for character in str(message.get("from") or "") if character.isdigit())
                message_id = str(message.get("id") or "").strip()
                if message.get("type") == "text" and text and sender and message_id:
                    extracted.append(IncomingWhatsAppMessage(message_id, sender, text[:2000]))
    return extracted


class WhatsAppService:
    def __init__(self, container: AppContainer, client: httpx.AsyncClient | None = None) -> None:
        self.container = container
        self.settings = container.settings
        self.client = client

    async def answer(self, message: IncomingWhatsAppMessage) -> str:
        if message.sender not in self.settings.whatsapp_sender_allowlist():
            raise PermissionError("WhatsApp sender is not authorized")
        normalized = message.text.lower()
        if any(word in normalized for word in ORDER_ACTION_WORDS):
            return READ_ONLY_RESPONSE
        user_id = UUID(self.settings.whatsapp_default_user_id)
        token = f"whatsapp:{message.sender}"
        sessions = DemoSessionService(self.settings, self.container.session_factory)
        await sessions.create(user_id, token)
        demo = await sessions.resolve(token)
        result = await run_orchestrator_turn(
            self.container,
            user_id=user_id,
            demo_session_id=demo.id,
            message=message.text,
        )
        return str(result.get("reply") or "No authoritative result is available.")[:4000]

    async def send_text(self, recipient: str, text: str) -> None:
        if not self.settings.whatsapp_phone_number_id or not self.settings.whatsapp_access_token:
            raise RuntimeError("WhatsApp outbound delivery is not configured")
        url = (
            f"{self.settings.whatsapp_graph_api_base_url.rstrip('/')}"
            f"/{self.settings.whatsapp_graph_api_version}"
            f"/{self.settings.whatsapp_phone_number_id}/messages"
        )
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        client = self.client or httpx.AsyncClient(timeout=15.0)
        owns_client = self.client is None
        try:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.settings.whatsapp_access_token}"},
                json=payload,
            )
            response.raise_for_status()
        finally:
            if owns_client:
                await client.aclose()
