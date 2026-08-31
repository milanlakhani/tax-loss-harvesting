from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace

import httpx
import pytest

from app.services.whatsapp import (
    READ_ONLY_RESPONSE,
    IncomingWhatsAppMessage,
    WhatsAppService,
    extract_text_messages,
    verify_meta_signature,
)


def test_signature_and_message_extraction() -> None:
    body = b'{"object":"whatsapp_business_account"}'
    secret = "test-secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(body, signature, secret)
    assert not verify_meta_signature(body + b"x", signature, secret)

    payload = {
        "entry": [{"changes": [{"value": {"messages": [{
            "id": "wamid.1", "from": "+44 7700 900123", "type": "text", "text": {"body": "Show my portfolio"}
        }]}}]}]
    }
    assert extract_text_messages(payload) == [
        IncomingWhatsAppMessage("wamid.1", "447700900123", "Show my portfolio")
    ]


@pytest.mark.asyncio
async def test_whatsapp_blocks_order_language_before_orchestrator(settings) -> None:
    settings.whatsapp_allowed_senders = "447700900123"
    container = SimpleNamespace(settings=settings)
    service = WhatsAppService(container)  # type: ignore[arg-type]

    reply = await service.answer(IncomingWhatsAppMessage("wamid.2", "447700900123", "Buy VTI now"))
    assert reply == READ_ONLY_RESPONSE

    with pytest.raises(PermissionError):
        await service.answer(IncomingWhatsAppMessage("wamid.3", "15550001111", "Show holdings"))


@pytest.mark.asyncio
async def test_outbound_message_uses_configured_meta_endpoint(settings, respx_mock) -> None:
    settings.whatsapp_phone_number_id = "phone-id"
    settings.whatsapp_access_token = "access-token"
    settings.whatsapp_graph_api_version = "v-test"
    route = respx_mock.post("https://graph.facebook.com/v-test/phone-id/messages").mock(
        return_value=httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})
    )
    container = SimpleNamespace(settings=settings)
    async with httpx.AsyncClient() as client:
        await WhatsAppService(container, client).send_text("447700900123", "Portfolio summary")  # type: ignore[arg-type]

    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer access-token"
    assert b'"messaging_product":"whatsapp"' in request.content
