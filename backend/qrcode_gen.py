"""QR code generation for on-chain payment addresses."""

import base64
import io

import qrcode  # type: ignore[import-untyped]


def generate_payment_qr(wallet: str, amount: str, network: str) -> str:
    """Generate a QR code as a ``data:image/png;base64,...`` string.

    * Solana → Solana Pay URI (``solana:<addr>?amount=<amount>``)
    * EVM chains → plain wallet address (user enters amount manually)
    """
    # USDC SPL token mint on Solana
    USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    if network == "solana":
        qr_data = f"solana:{wallet}?amount={amount}&spl-token={USDC_MINT}&label=PDF-Editor&message=Payment"
    else:
        qr_data = wallet

    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(qr_data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"
