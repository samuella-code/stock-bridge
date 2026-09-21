import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class PaystackError(RuntimeError):
    pass


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
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise PaystackError("Paystack could not be reached. Please try again.") from error
    if not result.get("status"):
        raise PaystackError(result.get("message") or "Paystack rejected the transaction.")
    return result["data"]


def initialize_transaction(secret_key, email, amount_kobo, reference, callback_url):
    return _request("/transaction/initialize", secret_key, "POST", {
        "email": email,
        "amount": amount_kobo,
        "currency": "NGN",
        "reference": reference,
        "callback_url": callback_url,
        "metadata": {"customer_email": email, "product": "stockbridge_lifetime"},
    })


def verify_transaction(secret_key, reference):
    return _request(f"/transaction/verify/{reference}", secret_key)
