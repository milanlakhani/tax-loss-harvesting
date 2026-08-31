from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.deps import get_container
from app.container import AppContainer
from app.services.whatsapp import WhatsAppService, extract_text_messages, verify_meta_signature

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    container: AppContainer = Depends(get_container),
) -> Response:
    expected = container.settings.whatsapp_verify_token
    if not expected or hub_mode != "subscribe" or not hmac_compare(hub_verify_token, expected):
        raise HTTPException(status_code=403, detail="Webhook verification failed")
    return Response(content=hub_challenge or "", media_type="text/plain")


def hmac_compare(value: str | None, expected: str) -> bool:
    import hmac

    return bool(value) and hmac.compare_digest(value, expected)


@router.post("/webhook", status_code=200)
async def receive_webhook(
    request: Request,
    container: AppContainer = Depends(get_container),
) -> dict:
    body = await request.body()
    if not verify_meta_signature(
        body,
        request.headers.get("X-Hub-Signature-256"),
        container.settings.whatsapp_app_secret,
    ):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    payload = await request.json()
    service = WhatsAppService(container)
    processed = 0
    for message in extract_text_messages(payload):
        try:
            reply = await service.answer(message)
        except PermissionError:
            continue
        await service.send_text(message.sender, reply)
        processed += 1
    return {"status": "accepted", "processed": processed}
