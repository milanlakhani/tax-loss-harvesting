from __future__ import annotations

import json
import os


APP_SECRET_KEYS = (
    "OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "COINGECKO_API_KEY",
    "ALPACA_ACCOUNT_1_KEY",
    "ALPACA_ACCOUNT_1_SECRET",
    "ALPACA_ACCOUNT_2_KEY",
    "ALPACA_ACCOUNT_2_SECRET",
    "DEMO_SESSION_SIGNING_SECRET",
)


class LocalEnvSecrets:
    """APP_ENV=local: process environment is already the source of settings."""

    def apply(self, environ: dict[str, str] | None = None) -> dict[str, str]:
        return dict(environ if environ is not None else os.environ)


class SecretsManagerOverlay:
    """APP_ENV=aws: fill missing process env from the application secret JSON.

    ECS injects the same keys from Secrets Manager. This adapter only overlays
    blank values. Secret values are never logged.
    """

    def __init__(self, secret_arn: str, *, client=None) -> None:
        self.secret_arn = secret_arn
        self._client = client

    def apply(self, environ: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(environ if environ is not None else os.environ)
        client = self._client
        if client is None:
            import boto3

            client = boto3.client("secretsmanager")
        payload = client.get_secret_value(SecretId=self.secret_arn)
        raw = payload.get("SecretString") or "{}"
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return env
        for key in APP_SECRET_KEYS:
            current = env.get(key)
            incoming = parsed.get(key)
            if incoming and not current:
                env[key] = str(incoming)
        return env


def apply_runtime_secrets(settings) -> None:
    if not settings.is_aws or not settings.app_secret_arn:
        return
    overlay = SecretsManagerOverlay(settings.app_secret_arn).apply()
    for key in APP_SECRET_KEYS:
        if overlay.get(key) and not os.environ.get(key):
            os.environ[key] = overlay[key]
