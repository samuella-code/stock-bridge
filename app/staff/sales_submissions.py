"""Employee-specific signed receipts; never impersonate the business owner."""
import re
import secrets
from itsdangerous import URLSafeTimedSerializer, BadData
from flask import current_app
from app.transaction_submissions import SubmissionError, TOKEN_MAX_AGE, _claim_validated_binding


def signer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='staff-sales-submission-v1')


def scope(member, business):
    return dict(user_id=member.user_id, business_id=business.id, operation='sale',
                membership_id=member.id, owner_id=member.owner_id)


def issue(member, business):
    return signer().dumps(dict(scope(member, business), key=secrets.token_hex(32), version=1))


def validate(token, member, business):
    if not isinstance(token, str) or len(token) > 2048:
        raise SubmissionError('Invalid staff submission.')
    try:
        data = signer().loads(token, max_age=TOKEN_MAX_AGE)
    except BadData:
        raise SubmissionError('Invalid staff submission.') from None
    expected = scope(member, business)
    if (not isinstance(data, dict) or data.get('version') != 1
            or any(type(data.get(k)) is not type(v) or data.get(k) != v for k,v in expected.items())
            or not isinstance(data.get('key'), str) or not re.fullmatch('[a-f0-9]{64}', data['key'])):
        raise SubmissionError('Invalid staff submission.')
    return dict(user_id=member.user_id, business_id=business.id, operation='sale', key=data['key'])


def claim(token, member, business, intent):
    # Called only after live membership authority is checked under the owner lock.
    return _claim_validated_binding(validate(token, member, business), intent)
