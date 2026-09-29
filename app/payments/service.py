import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class PaystackError(RuntimeError):
    def __init__(self, message, *, status_code=None, code=None):
        super().__init__(message)
        self.status_code = status_code
        # A provider error code is useful for diagnosis. Never retain arbitrary
        # response text here because it may include account or request data.
        self.code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,60}", code) else None


def _request(path, secret_key, method="GET", payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"https://api.paystack.co{path}",
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {secret_key}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode())
    except HTTPError as error:
        try:
            failure = json.loads(error.read(4096).decode())
        except (ValueError, UnicodeError):
            failure = {}
        code = failure.get("code") if isinstance(failure, dict) else None
        raise PaystackError("Paystack rejected the request.", status_code=error.code, code=code) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise PaystackError("Paystack could not be reached. Please try again.") from error
    if not isinstance(result, dict):
        raise PaystackError("Paystack returned an invalid response.")
    if not result.get("status"):
        raise PaystackError("Paystack rejected the transaction.", code=result.get("code"))
    if not isinstance(result.get("data"), dict):
        raise PaystackError("Paystack returned an invalid response.")
    return result["data"]


def initialize_transaction(secret_key, email, amount_kobo, reference, callback_url):
    return _request("/transaction/initialize", secret_key, "POST", {
        "email": email,
        "amount": amount_kobo,
        "currency": "NGN",
        "reference": reference,
        "callback_url": callback_url,
        "metadata": json.dumps({"customer_email": email, "product": "stockbridge_lifetime"}),
    })


def verify_transaction(secret_key, reference):
    return _request(f"/transaction/verify/{reference}", secret_key)
