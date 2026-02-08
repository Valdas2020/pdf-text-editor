"""Payment logic stub.

Temporary placeholder — all downloads are allowed.
TODO: Integrate with CryptoBot API for real payment verification.
"""

PAYMENT_PRICE_USD = 5


def verify_payment(result_file_id: str, token: str) -> bool:
    """Check if payment was made for a given result file.

    STUB: Always returns True.
    TODO: Verify via CryptoBot API /getInvoices.
    """
    return True


def create_invoice(result_file_id: str) -> dict:
    """Create a payment invoice.

    STUB: Returns placeholder data.
    TODO: Call CryptoBot API POST /createInvoice.
    """
    return {
        "invoice_id": "PLACEHOLDER",
        "telegram_url": "https://t.me/CryptoBot?start=PLACEHOLDER",
        "amount": PAYMENT_PRICE_USD,
        "currency": "USDT",
    }
