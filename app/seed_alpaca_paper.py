from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import or_, select

from app.config import Settings, get_settings
from app.domain.errors import ProviderError
from app.persistence.database import get_session_factory
from app.persistence.models import MirrorManifest, PaperMirrorActivity, PortfolioAccount
from app.providers.alpaca import AlpacaProvider
from app.providers.mappings import expected_alpaca_asset_class
from app.providers.protocols import ExecutionProvider

CRYPTO_SEED_BUFFER_BPS = Decimal("50")
_BPS = Decimal("10000")


def seed_client_order_id(
    portfolio_id: UUID,
    manifest_digest: str,
    symbol: str,
    current_quantity: Decimal = Decimal("0"),
    shortage_quantity: Decimal = Decimal("0"),
) -> str:
    normalized = symbol.lower().replace("/", "-")
    snapshot = hashlib.sha256(
        f"{manifest_digest}|{normalized}|{current_quantity}|{shortage_quantity}".encode()
    ).hexdigest()[:16]
    return f"tlh-seed-{portfolio_id.hex[:16]}-{snapshot}-{normalized}"[:128]


def _load_manifest(path: Path) -> tuple[dict, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid manifest: {path}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("positions"), list):
        raise ValueError("manifest must contain a positions list")
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload, digest


async def seed_alpaca_paper(
    *,
    portfolio: str,
    manifest_path: Path,
    confirm_paper: bool,
    asset_class_filter: str | None = None,
    settings: Settings | None = None,
    execution: ExecutionProvider | None = None,
) -> list[dict]:
    settings = settings or get_settings()
    manifest, manifest_digest = _load_manifest(manifest_path)
    try:
        portfolio_id = UUID(portfolio)
    except ValueError:
        portfolio_id = None

    factory = get_session_factory(settings)
    async with factory() as session:
        conditions = [PortfolioAccount.alpaca_alias == portfolio, PortfolioAccount.name == portfolio]
        if portfolio_id is not None:
            conditions.insert(0, PortfolioAccount.id == portfolio_id)
        account = await session.scalar(select(PortfolioAccount).where(or_(*conditions)))
        if account is None or not account.alpaca_alias:
            raise ValueError(f"portfolio is not mapped to an Alpaca account: {portfolio}")
        if manifest.get("internal_portfolio_id") != str(account.id):
            raise ValueError("manifest portfolio does not match the selected portfolio")
        if manifest.get("alpaca_paper_alias") != account.alpaca_alias:
            raise ValueError("manifest Alpaca alias does not match the portfolio mapping")

        # Reconcile once and fail closed if Alpaca cannot provide the current book.
        # Never assume zero inventory on a provider/network failure.
        provider = execution or AlpacaProvider(
            settings.alpaca_credentials(),
            enable_paper_orders=settings.enable_paper_orders,
        )
        current_positions = {
            position.symbol.upper(): position.quantity
            for position in await provider.list_positions(account.alpaca_alias)
        }

        planned: list[dict] = []
        sufficient: list[dict] = []
        for position in manifest["positions"]:
            if not isinstance(position, dict):
                raise ValueError("manifest positions must be objects")
            symbol = str(position.get("symbol") or "").strip()
            if not symbol:
                raise ValueError("manifest position is missing symbol")
            try:
                quantity = Decimal(str(position.get("manifest_quantity", "0")))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"invalid quantity for {symbol}") from exc
            if quantity < 0:
                raise ValueError(f"negative quantity for {symbol}")
            if quantity == 0:
                continue
            current_quantity = current_positions.get(symbol.upper(), Decimal("0"))
            shortage = max(quantity - current_quantity, Decimal("0"))
            asset_class = expected_alpaca_asset_class(str(position.get("asset_class") or ""))
            if asset_class_filter is not None and asset_class != asset_class_filter:
                continue
            planned_buy = shortage
            if shortage > 0 and asset_class == "crypto":
                # Alpaca paper crypto fills observed in the demo deduct quantity for fees.
                # A small, explicit execution-only buffer prevents the mirror from
                # remaining below the manifest sale quantity. It never changes tax lots.
                planned_buy = shortage * (Decimal("1") + CRYPTO_SEED_BUFFER_BPS / _BPS)
            item = {
                "symbol": symbol,
                "manifest_quantity": quantity,
                "current_quantity": current_quantity,
                "shortage_quantity": shortage,
                "quantity": planned_buy,
                "buffer_bps": CRYPTO_SEED_BUFFER_BPS if asset_class == "crypto" and shortage > 0 else Decimal("0"),
                "asset_class": asset_class,
                "time_in_force": "GTC" if asset_class == "crypto" else "DAY",
                "client_order_id": seed_client_order_id(account.id, manifest_digest, symbol, current_quantity, shortage),
            }
            if shortage == 0:
                sufficient.append(item)
            else:
                planned.append(item)

        def serialized(item: dict) -> dict:
            return {
                **item,
                "manifest_quantity": str(item["manifest_quantity"]),
                "current_quantity": str(item["current_quantity"]),
                "shortage_quantity": str(item["shortage_quantity"]),
                "quantity": str(item["quantity"]),
                "buffer_bps": str(item["buffer_bps"]),
            }

        print(json.dumps([serialized(item) for item in [*planned, *sufficient]], indent=2))
        if not confirm_paper:
            previews = [
                {
                    **serialized(item),
                    "side": "BUY",
                    "status": "PREVIEW_ONLY",
                }
                for item in planned
            ]
            previews.extend(
                {
                    **serialized(item),
                    "side": "NONE",
                    "status": "ALREADY_SUFFICIENT",
                }
                for item in sufficient
            )
            return previews
        if not settings.enable_paper_orders:
            raise ProviderError("ENABLE_PAPER_ORDERS=false; set it explicitly after reviewing the plan", "alpaca")

        results: list[dict] = [
            {**serialized(item), "side": "NONE", "status": "ALREADY_SUFFICIENT"}
            for item in sufficient
        ]
        for item in planned:
            existing = await session.scalar(
                select(PaperMirrorActivity).where(
                    PaperMirrorActivity.portfolio_id == account.id,
                    PaperMirrorActivity.activity_type == "PAPER_MIRROR_SETUP",
                    PaperMirrorActivity.payload["client_order_id"].as_string() == item["client_order_id"],
                )
            )
            if existing is not None:
                results.append({"symbol": item["symbol"], "status": "SKIPPED_EXISTING", "client_order_id": item["client_order_id"]})
                continue
            submitted = await provider.submit_market_buy(
                account_alias=account.alpaca_alias,
                symbol=item["symbol"],
                quantity=item["quantity"],
                client_order_id=item["client_order_id"],
                asset_class=item["asset_class"],
            )
            payload = {
                **serialized(item),
                "side": "BUY",
                "provider_order_id": submitted.provider_order_id,
                "status": submitted.status,
                "manifest_digest": manifest_digest,
            }
            session.add(
                PaperMirrorActivity(
                    id=uuid4(),
                    portfolio_id=account.id,
                    alpaca_alias=account.alpaca_alias,
                    activity_type="PAPER_MIRROR_SETUP",
                    payload=payload,
                    is_synthetic=True,
                )
            )
            await session.commit()
            results.append(payload)
        return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed an Alpaca paper account from a mirror manifest")
    parser.add_argument("--portfolio", required=True, help="portfolio UUID, name, or Alpaca alias")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument(
        "--confirm-paper",
        action="store_true",
        help="submit the displayed BUY plan to Alpaca paper; without this flag the command is preview-only",
    )
    parser.add_argument(
        "--asset-class",
        choices=("us_equity", "crypto"),
        help="limit the preview or submission to one Alpaca asset class",
    )
    args = parser.parse_args()
    results = asyncio.run(
        seed_alpaca_paper(
            portfolio=args.portfolio,
            manifest_path=args.manifest,
            confirm_paper=args.confirm_paper,
            asset_class_filter=args.asset_class,
        )
    )
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
