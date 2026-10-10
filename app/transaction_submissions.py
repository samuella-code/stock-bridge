"""Caller-owned transactions: no commits, rollback, network calls or retries."""
import hashlib
import json
import re
import secrets
from datetime import datetime
from itsdangerous import URLSafeTimedSerializer, BadData
from flask import current_app
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app import db
from app.models import Business, TransactionSubmission

OPERATIONS = frozenset(('sale', 'restock', 'adjustment', 'expense'))
TOKEN_MAX_AGE = 3600
TRACKING = 'stockbridge_submission_claims'

class SubmissionError(ValueError):
    pass

class SubmissionConflict(SubmissionError):
    pass


def scope(user_id, business_id, operation):
    if type(user_id) is not int or type(business_id) is not int or operation not in OPERATIONS:
        raise SubmissionError('Invalid submission scope.')
    if not Business.query.filter_by(id=business_id, user_id=user_id).first():
        raise SubmissionError('Submission ownership rejected.')
    return dict(user_id=user_id, business_id=business_id, operation=operation)


def signer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt='transaction-submission-v1')


def issue_token(user_id, business_id, operation):
    return signer().dumps(dict(scope(user_id, business_id, operation), key=secrets.token_hex(32), version=1))


def validate_token(token, user_id, business_id, operation):
    expected = scope(user_id, business_id, operation)
    if not isinstance(token, str) or len(token) > 2048:
        raise SubmissionError('Invalid or expired submission token.')
    try:
        data = signer().loads(token, max_age=TOKEN_MAX_AGE)
    except BadData:
        raise SubmissionError('Invalid or expired submission token.') from None
    if (not isinstance(data, dict) or data.get('version') != 1
            or any(data.get(k) != v for k, v in expected.items())
            or not isinstance(data.get('key'), str) or not re.fullmatch(r'[0-9a-f]{64}', data['key'])):
        raise SubmissionError('Invalid submission binding.')
    return dict(expected, key=data['key'])


def payload_hash(payload):
    def check(value):
        if value is None or type(value) in (str, int, bool):
            return
        if type(value) is list:
            for item in value: check(item)
            return
        if type(value) is dict and all(type(k) is str for k in value):
            for item in value.values(): check(item)
            return
        raise SubmissionError('Payload must contain canonical JSON values; represent money as strings.')
    check(payload)
    encoded = json.dumps({'version': 1, 'intent': payload}, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def claim(token, user_id, business_id, operation, payload):
    binding = validate_token(token, user_id, business_id, operation)
    digest = payload_hash(payload)
    dialect = db.session.get_bind().dialect.name
    insert = {'postgresql': pg_insert, 'sqlite': sqlite_insert}.get(dialect)
    if insert is None:
        raise SubmissionError('Unsupported submission database.')
    statement = insert(TransactionSubmission).values(**binding, payload_hash=digest, created_at=datetime.utcnow())
    statement = statement.on_conflict_do_nothing(index_elements=['business_id', 'user_id', 'operation', 'key'])
    result = db.session.execute(statement)
    receipt = TransactionSubmission.query.filter_by(**binding).populate_existing().one()
    if receipt.payload_hash != digest:
        raise SubmissionConflict('Submission key was already used with different contents.')
    if result.rowcount == 1:
        db.session.info.setdefault(TRACKING, set()).add(receipt.id)
        return receipt, False
    if receipt.completed_at is None or not receipt.result_reference:
        raise SubmissionConflict('Submission has no completed result; manual review required.')
    return receipt, True


def complete(receipt, result_reference):
    if receipt.id not in db.session.info.get(TRACKING, set()) or receipt.completed_at is not None:
        raise SubmissionError('Only this transaction may complete its newly claimed submission.')
    if not isinstance(result_reference, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,120}', result_reference):
        raise SubmissionError('Result reference must be a safe internal identifier.')
    receipt.result_reference = result_reference
    receipt.completed_at = datetime.utcnow()
    db.session.flush()


@event.listens_for(Session, 'before_commit')
def prevent_pending_commit(session):
    ids = session.info.get(TRACKING, set())
    if ids:
        session.flush()
        if session.query(TransactionSubmission).filter(TransactionSubmission.id.in_(ids),
                TransactionSubmission.completed_at.is_(None)).first():
            raise SubmissionError('Complete submission receipts before committing business mutations.')


@event.listens_for(Session, 'after_transaction_end')
def clear_claims(session, transaction):
    if transaction.parent is None:
        session.info.pop(TRACKING, None)
