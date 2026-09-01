# WhatsApp no-extra-cost demo setup

This setup uses Meta's WhatsApp Cloud API test number and a temporary Cloudflare Quick Tunnel. It is intended for development and a short demonstration, not production hosting.

## Safety boundary

- WhatsApp can retrieve and explain authoritative holdings, spending, anomaly, risk, drift, and persisted tax-loss decisions.
- It cannot prepare, approve, confirm, buy, sell, or submit an order.
- Recommendations are limited to candidates already evaluated by the deterministic wash-sale, risk, freshness, basis, and mirror-quantity controls.
- Keep `ENABLE_PAPER_ORDERS=false` while configuring WhatsApp.

## 1. Create the Meta test application

1. Sign in to Meta for Developers.
2. Create an app and add the WhatsApp product.
3. Use the test business phone number supplied by Meta.
4. Add your personal WhatsApp number as an allowed test recipient.
5. Copy the test phone number, Phone Number ID, temporary access token, and App Secret.

Do not paste these secrets into chat, screenshots, source files, or Git.

## 2. Configure `.env`

Use digits only for phone numbers, including the country code:

```dotenv
WHATSAPP_PHONE_NUMBER=447700900123
WHATSAPP_PHONE_NUMBER_ID=FROM_META
WHATSAPP_ACCESS_TOKEN=FROM_META
WHATSAPP_VERIFY_TOKEN=CREATE_A_PRIVATE_RANDOM_VALUE
WHATSAPP_APP_SECRET=FROM_META
WHATSAPP_ALLOWED_SENDERS=YOUR_PERSONAL_NUMBER
WHATSAPP_DEFAULT_USER_ID=11111111-1111-4111-8111-111111111111
WHATSAPP_GRAPH_API_VERSION=VERSION_SHOWN_BY_META
```

The temporary access token expires. That is acceptable for a demonstration; obtain a new test token from Meta when required.

## 3. Start the application and free HTTPS tunnel

```powershell
docker compose up -d backend ui
docker compose --profile whatsapp up -d whatsapp-tunnel
docker compose logs --tail=50 whatsapp-tunnel
```

The log prints a temporary URL resembling `https://random-words.trycloudflare.com`.
The Meta callback is `https://random-words.trycloudflare.com/api/whatsapp/webhook`.

Quick Tunnel URLs change when the tunnel is recreated. Update Meta's callback when that happens.

## 4. Configure Meta webhook

1. Paste the callback URL into the WhatsApp webhook configuration.
2. Use the exact value from `WHATSAPP_VERIFY_TOKEN` as the verification token.
3. Subscribe to the `messages` webhook field.
4. Never expose the App Secret or access token in the callback URL.

## 5. Verify without displaying secrets

```powershell
docker compose run --rm backend python -m app.verify_whatsapp
```

Every required field should report `true`, `allowed_senders` should be at least `1`, and `paper_orders_enabled` should remain `false`.

## 6. Demonstrate

1. Open the web app and select **WhatsApp integration**.
2. Enter the Meta test business number and scan the QR.
3. Send `Show my portfolio holdings.`
4. Send `Is my portfolio within its risk limits?`
5. Run Portfolio analysis in the web app.
6. Send `Show my safe tax-loss opportunities and protected decisions.`
7. Send `Buy VTI now.` and verify the assistant refuses the order request.

## Troubleshooting

- **QR appears but no reply:** check webhook credentials and tunnel logs.
- **Webhook verification fails:** confirm the callback URL and verify token match exactly.
- **Sender is ignored:** add the sender to `WHATSAPP_ALLOWED_SENDERS` using digits only.
- **Meta returns an authorization error:** refresh the temporary test access token.
- **Tunnel URL changed:** update the callback URL in Meta.
