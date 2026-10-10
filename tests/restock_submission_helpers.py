"""Obtain real form tokens for pre-existing restocking regression requests."""
import re
from werkzeug.datastructures import MultiDict

def with_restock_token(client, payload):
    response = client.get('/restocking/')
    match = re.search(rb'name="submission_token" value="([^"]+)"', response.data)
    if match is None: raise AssertionError('Authorized restocking form did not provide a token')
    copied = MultiDict(payload) if isinstance(payload, MultiDict) else dict(payload)
    copied['submission_token'] = match[1].decode()
    return copied
