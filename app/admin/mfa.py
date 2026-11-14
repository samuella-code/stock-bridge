"""Admin authenticator secrets, RFC 6238 codes, and one-time recovery codes."""
import base64
import hashlib
import hmac
import secrets
from time import time
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet

PERIOD_SECONDS = 30
CHALLENGE_SECONDS = 300


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def matching_step(secret, code, last_step=-1):
    if len(code) != 6 or not code.isascii() or not code.isdecimal():
        return None
    key = base64.b32decode(secret)
    current_step = int(time()) // PERIOD_SECONDS
    for step in (current_step, current_step - 1, current_step + 1):
        if step < 0 or step <= last_step:
            continue
        digest = hmac.digest(key, step.to_bytes(8, "big"), "sha1")
        offset = digest[-1] & 15
        number = int.from_bytes(digest[offset:offset + 4], "big") & 0x7fffffff
        if hmac.compare_digest(f"{number % 1_000_000:06d}", code):
            return step
    return None


def digest_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_recovery_codes():
    return [f"{secrets.token_hex(6)}-{secrets.token_hex(6)}" for _ in range(8)]


def setup_uri(email, secret):
    issuer = "StockBridge Admin"
    label = quote(f"{issuer}:{email}", safe="")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": issuer})


def qr_image(uri):
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    drawing = Drawing(256, 256)
    drawing.add(QrCodeWidget(uri, barWidth=256, barHeight=256))
    svg = renderSVG.drawToString(drawing).replace("<svg ", '<svg shape-rendering="crispEdges" ', 1)
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def secret_cipher(key):
    return Fernet(key)
