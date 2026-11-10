"""Employee receipts for new workflows; no owner token/operation policy changes."""
import hashlib,json,re,secrets
from itsdangerous import URLSafeTimedSerializer,BadData
from flask import current_app
from app.transaction_submissions import TOKEN_MAX_AGE,SubmissionError,_claim_validated_binding

OPERATIONS=frozenset(('product_create','product_edit','expense'))


def signer():return URLSafeTimedSerializer(current_app.config['SECRET_KEY'],salt='staff-operations-v1')


def binding(member,business,operation,product_id=None):
    if operation not in OPERATIONS:raise SubmissionError('Invalid Staff operation.')
    return dict(user_id=member.user_id,owner_id=member.owner_id,membership_id=member.id,
                business_id=business.id,operation=operation,product_id=product_id)


def issue(member,business,operation,*,product_id=None,revision=None):
    return signer().dumps(dict(binding(member,business,operation,product_id),version=1,key=secrets.token_hex(32),revision=revision))


def validate(token,member,business,operation,*,product_id=None):
    if not isinstance(token,str) or len(token)>2048:raise SubmissionError('Invalid Staff reference.')
    try:data=signer().loads(token,max_age=TOKEN_MAX_AGE)
    except BadData:raise SubmissionError('Invalid Staff reference.') from None
    expected=binding(member,business,operation,product_id)
    if (not isinstance(data,dict) or type(data.get('version')) is not int or data['version']!=1
            or any(type(data.get(k)) is not type(v) or data.get(k)!=v for k,v in expected.items())
            or not isinstance(data.get('key'),str) or not re.fullmatch('[a-f0-9]{64}',data['key'])):
        raise SubmissionError('Invalid Staff binding.')
    if operation=='product_edit' and (type(data.get('revision')) is not str or not re.fullmatch('[a-f0-9]{64}',data['revision'])):
        raise SubmissionError('Missing product revision.')
    return data


def claim(token,member,business,operation,intent,*,product_id=None):
    data=validate(token,member,business,operation,product_id=product_id)
    receipt,replay=_claim_validated_binding({k:data[k] for k in ('user_id','business_id','operation','key')},intent)
    return receipt,replay,data.get('revision')


def fingerprint(fields):return hashlib.sha256(json.dumps(fields,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
