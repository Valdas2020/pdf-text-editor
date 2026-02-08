"""Payment integration for PDF Text Editor.

CryptoBot (Telegram) + on-chain USDT/USDC via Drops Bot webhooks.
Handles invoice creation, webhook verification, download-token generation,
and in-memory payment tracking (single-server deployment).
"""

import hashlib
import hmac
import logging
import random
import time
from decimal import Decimal

import requests

from config import APP_BASE_URL, CRYPTOBOT_API_TOKEN, CRYPTOBOT_API_URL, DOWNLOAD_SECRET

logger = logging.getLogger(__name__)

PAYMENT_PRICE_USD = 5

# ---------------------------------------------------------------------------
# In-memory payment tracking
# key = result_file_id, value = {"invoice_id", "status", "created_at"}
# ---------------------------------------------------------------------------
_payments: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Invoice creation
# ---------------------------------------------------------------------------

def create_invoice(result_file_id: str) -> dict:
    """Create a CryptoBot USDT invoice and track it.

    Returns dict with ``pay_url``, ``invoice_id``, ``amount``, ``currency``.
    Raises ``RuntimeError`` on failure.
    """
    if not CRYPTOBOT_API_TOKEN:
        raise RuntimeError("CRYPTOBOT_API_TOKEN is not configured")

    download_page_url = f"{APP_BASE_URL}/download-page/{result_file_id}"

    resp = requests.post(
        f"{CRYPTOBOT_API_URL}/createInvoice",
        headers={
            "Crypto-Pay-API-Token": CRYPTOBOT_API_TOKEN,
            "Content-Type": "application/json",
        },
        json={
            "asset": "USDT",
            "amount": str(PAYMENT_PRICE_USD),
            "description": "PDF Text Editor — edited document download",
            "paid_btn_name": "viewItem",
            "paid_btn_url": download_page_url,
            "payload": result_file_id,
            "allow_comments": False,
            "allow_anonymous": True,
        },
        timeout=30,
    )

    data = resp.json()

    if not data.get("ok"):
        error = data.get("error", {})
        logger.error("CryptoBot invoice creation failed: %s", data)
        raise RuntimeError(f"CryptoBot error: {error}")

    invoice = data["result"]
    invoice_id = str(invoice["invoice_id"])
    pay_url = invoice["pay_url"]

    # Track in memory
    _payments[result_file_id] = {
        "invoice_id": invoice_id,
        "status": "pending",
        "created_at": time.time(),
    }

    logger.info("Created CryptoBot invoice %s for file %s", invoice_id, result_file_id)

    return {
        "invoice_id": invoice_id,
        "pay_url": pay_url,
        "amount": PAYMENT_PRICE_USD,
        "currency": "USDT",
    }


# ---------------------------------------------------------------------------
# Payment state
# ---------------------------------------------------------------------------

def mark_paid(result_file_id: str) -> None:
    """Mark a result file as paid (called from webhook handler)."""
    if result_file_id in _payments:
        _payments[result_file_id]["status"] = "paid"
    else:
        _payments[result_file_id] = {
            "invoice_id": "webhook",
            "status": "paid",
            "created_at": time.time(),
        }
    logger.info("Payment marked as paid for file %s", result_file_id)


def is_paid(result_file_id: str) -> bool:
    """Return True if payment for *result_file_id* has been confirmed."""
    entry = _payments.get(result_file_id)
    return entry is not None and entry["status"] == "paid"


# ---------------------------------------------------------------------------
# Webhook signature verification
# ---------------------------------------------------------------------------

def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """Verify CryptoBot webhook HMAC-SHA256 signature.

    CryptoBot signs the body with HMAC using SHA256(api_token) as the key.
    """
    if not CRYPTOBOT_API_TOKEN or not signature:
        return False
    secret = hashlib.sha256(CRYPTOBOT_API_TOKEN.encode()).digest()
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


# ---------------------------------------------------------------------------
# Download token (HMAC-based)
# ---------------------------------------------------------------------------

def generate_download_token(result_file_id: str) -> str:
    """Generate an HMAC-SHA256 token that authorises a file download."""
    return hmac.new(
        DOWNLOAD_SECRET.encode(),
        result_file_id.encode(),
        hashlib.sha256,
    ).hexdigest()


def verify_download_token(result_file_id: str, token: str) -> bool:
    """Return True if *token* is a valid download token for *result_file_id*."""
    expected = generate_download_token(result_file_id)
    return hmac.compare_digest(token, expected)


# ---------------------------------------------------------------------------
# On-chain USDT/USDC payments
# ---------------------------------------------------------------------------

WALLETS = {
    "erc20": "0xD1c06E8E30768dE5CCDB0954C3d06e7789409928",
    "bep20": "0xD1c06E8E30768dE5CCDB0954C3d06e7789409928",
    "base": "0xD1c06E8E30768dE5CCDB0954C3d06e7789409928",
    "solana": "DUrqdE8YFUx4zyxRGoE9ph1KKuGxn7p5fy7Cf1yuA1Lz",
}

# key = unique_amount (str), value = {result_file_id, created_at, network, ...}
_pending_onchain: dict[str, dict] = {}


def generate_unique_amount(result_file_id: str) -> str:
    """Generate amount like 4.9901 … 4.9999 (99 unique slots)."""
    for _ in range(50):
        suffix = random.randint(1, 99)
        amount = f"4.99{suffix:02d}"
        if amount not in _pending_onchain:
            return amount
    # Fallback: timestamp-based
    ts = int(time.time()) % 99 + 1
    return f"4.99{ts:02d}"


def register_pending_payment(result_file_id: str, network: str) -> dict:
    """Register a pending on-chain payment, return amount + wallet info."""
    amount = generate_unique_amount(result_file_id)
    download_token = generate_download_token(result_file_id)

    _pending_onchain[amount] = {
        "result_file_id": result_file_id,
        "created_at": time.time(),
        "network": network,
        "download_token": download_token,
        "status": "pending",
    }

    wallet = WALLETS.get(network, WALLETS["erc20"])

    logger.info(
        "Registered on-chain payment: %s %s on %s for file %s",
        amount, "USDT/USDC", network, result_file_id,
    )

    return {
        "amount": amount,
        "wallet": wallet,
        "network": network,
        "download_token": download_token,
        "result_file_id": result_file_id,
    }


def match_incoming_payment(amount_str: str, network: str) -> dict | None:
    """Match incoming transfer amount to a pending payment (±0.001 tolerance)."""
    try:
        incoming = Decimal(amount_str)
    except Exception:
        return None

    for pending_amount, data in _pending_onchain.items():
        if data["status"] != "pending":
            continue
        if abs(incoming - Decimal(pending_amount)) <= Decimal("0.001"):
            data["status"] = "paid"
            # Also mark in the main _payments dict so download-page works
            mark_paid(data["result_file_id"])
            logger.info("On-chain payment matched: %s → file %s", amount_str, data["result_file_id"])
            return data
    return None


def check_onchain_paid(result_file_id: str) -> dict | None:
    """Return payment data if on-chain payment was confirmed for this file."""
    for _amount, data in _pending_onchain.items():
        if data["result_file_id"] == result_file_id and data["status"] == "paid":
            return data
    return None


def cleanup_expired_payments(max_age_seconds: int = 1800) -> None:
    """Remove on-chain payments older than *max_age_seconds* (default 30 min)."""
    now = time.time()
    expired = [k for k, v in _pending_onchain.items()
               if now - v["created_at"] > max_age_seconds]
    for k in expired:
        del _pending_onchain[k]
    if expired:
        logger.info("Cleaned up %d expired on-chain payments", len(expired))
