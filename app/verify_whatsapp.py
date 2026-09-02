from __future__ import annotations

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    checks = {
        "business_or_test_number": bool(settings.whatsapp_phone_number),
        "phone_number_id": bool(settings.whatsapp_phone_number_id),
        "access_token": bool(settings.whatsapp_access_token),
        "verify_token": bool(settings.whatsapp_verify_token),
        "app_secret": bool(settings.whatsapp_app_secret),
        "allowed_senders": len(settings.whatsapp_sender_allowlist()),
        "paper_orders_enabled": settings.enable_paper_orders,
    }
    ready = all(
        checks[key]
        for key in ("business_or_test_number", "phone_number_id", "access_token", "verify_token", "app_secret")
    ) and checks["allowed_senders"] > 0
    print({"whatsapp_ready": ready, **checks})
    print({"callback_path": "/api/whatsapp/webhook", "channel": "READ_ONLY"})


if __name__ == "__main__":
    main()
