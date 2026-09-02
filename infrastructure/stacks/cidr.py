from __future__ import annotations

import ipaddress
import os

DESTROY_COMPATIBLE_ENVIRONMENTS = frozenset({"demo", "dev", "development", "ephemeral"})
PRODUCTION_NAMED_ENVIRONMENTS = frozenset({"prod", "production", "staging"})


class CidrRestrictionError(ValueError):
    """Raised when AWS synthesis would open the ALB or omit the operator CIDR."""


def require_allowed_cidr(value: str | None = None, *, app=None) -> str:
    """ALLOWED_IPV4_CIDR is AWS/CDK-only. Blank and 0.0.0.0/0 fail synthesis.

    Changing this function is the explicit source-code acceptance of that risk.
    """
    raw = value
    if raw is None and app is not None:
        raw = app.node.try_get_context("allowed_ipv4_cidr") or os.environ.get("ALLOWED_IPV4_CIDR")
    if raw is None:
        raw = os.environ.get("ALLOWED_IPV4_CIDR")
    if isinstance(raw, str):
        raw = raw.strip()
    if not raw:
        raise CidrRestrictionError(
            "ALLOWED_IPV4_CIDR is required for CDK synth/deploy (context allowed_ipv4_cidr "
            "or environment ALLOWED_IPV4_CIDR). It is not used by APP_ENV=local or Docker Compose."
        )
    if raw in {"0.0.0.0/0", "::/0"}:
        raise CidrRestrictionError(
            "ALLOWED_IPV4_CIDR must not be 0.0.0.0/0 or ::/0. This demo has no user authentication; "
            "the ALB must stay operator-CIDR restricted unless you change this check in source."
        )
    try:
        network = ipaddress.ip_network(raw, strict=False)
    except ValueError as exc:
        raise CidrRestrictionError(f"ALLOWED_IPV4_CIDR is not a valid CIDR: {raw}") from exc
    if network.version != 4:
        raise CidrRestrictionError("ALLOWED_IPV4_CIDR must be an IPv4 CIDR.")
    if network.prefixlen == 0:
        raise CidrRestrictionError("ALLOWED_IPV4_CIDR must not be 0.0.0.0/0.")
    return str(network)


def destroy_compatible(environment_name: str) -> bool:
    name = environment_name.strip().lower()
    if name in PRODUCTION_NAMED_ENVIRONMENTS:
        return False
    return name in DESTROY_COMPATIBLE_ENVIRONMENTS
