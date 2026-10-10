"""Explicit form-token acquisition for pre-existing sales regression tests."""
import re
from werkzeug.datastructures import MultiDict

def with_sale_token(client, payload):
    response = client.get('/sales/')
    token = re.search(rb'name="submission_token" value="([^"]+)"', response.data)
    if not token:
        raise AssertionError('Authorized sales form did not supply a submission token')
    copied = MultiDict(payload) if isinstance(payload, MultiDict) else dict(payload)
    copied['submission_token'] = token[1].decode()
    return copied
