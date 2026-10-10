"""Obtain signed tokens from the actual authorized creation forms."""
import re

def with_operation_token(client, path, payload):
    form_path = path.removesuffix('/adjust') if path.endswith('/adjust') else '/expenses/'
    response = client.get(form_path)
    match = re.search(rb'name="submission_token" value="([^"]+)"', response.data)
    if match is None:
        raise AssertionError('Authorized creation form did not provide a token')
    return dict(payload, submission_token=match[1].decode())
