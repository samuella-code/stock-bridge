"""Paystack billing adapter. No shared-plan edits or direct charge API."""
import os
import re
from flask import current_app
from app.payments.service import PaystackError, _request
from app.subscriptions.entitlements import plan_spec


def configured():
    cfg=current_app.config
    public,secret=cfg.get('PAYSTACK_PUBLIC_KEY',''),cfg.get('PAYSTACK_SECRET_KEY','')
    return bool(cfg.get('SUBSCRIPTIONS_ENABLED') and cfg.get('BILLING_PROVIDER_ENABLED')
        and (not os.getenv('VERCEL_ENV') or os.getenv('VERCEL_ENV')=='production')
        and ((public.startswith('pk_test_') and secret.startswith('sk_test_'))
             or (public.startswith('pk_live_') and secret.startswith('sk_live_'))))


def mode():
    key=current_app.config.get('PAYSTACK_SECRET_KEY','')
    return 'live' if key.startswith('sk_live_') else 'test' if key.startswith('sk_test_') else None


def call(path, method='GET', payload=None):
    if not configured():
        raise PaystackError('Subscription billing is not enabled in this environment.')
    return _request(path,current_app.config['PAYSTACK_SECRET_KEY'],method,payload,allow_empty=(path=='/subscription/disable' and method=='POST'))


def identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):
        raise ValueError('Invalid provider identifier.')
    return value


def configured_plan(plan,interval):
    spec=plan_spec(plan,interval)
    code=current_app.config.get(f'PAYSTACK_{plan.upper()}_{interval.upper()}_PLAN','')
    if not code.startswith('PLN_'):
        raise ValueError('This plan is not configured yet. Please contact support.')
    identifier(code)
    return code,spec


def validate_plan(data,code,spec):
    return (isinstance(data,dict) and data.get('plan_code')==code and type(data.get('amount')) is int
        and data['amount']==spec['amount'] and data.get('currency')=='NGN'
        and data.get('interval')==spec['interval'] and data.get('domain')==mode())


def fetch_subscription(code):
    data=call('/subscription/'+identifier(code))
    # Some subscription responses omit domain on their embedded plan.
    # Resolve it from the authenticated Plan API, never from an assumed mode.
    plan=data.get('plan') if isinstance(data,dict) else None
    if isinstance(plan,dict) and 'domain' not in plan:
        plan_code=identifier(plan.get('plan_code'))
        if not plan_code.startswith('PLN_'):
            raise ValueError('Provider subscription plan needs review.')
        authoritative=call('/plan/'+plan_code)
        if not isinstance(authoritative,dict) or any(
            authoritative.get(field)!=plan.get(field)
            for field in ('plan_code','amount','currency','interval')
        ) or authoritative.get('domain')!=mode():
            raise ValueError('Provider subscription plan differs from the verified plan.')
        data={**data,'plan':{**plan,'domain':authoritative['domain']}}
    return data


def disable_subscription(code,token):
    identifier(code)
    if not isinstance(token,str) or not token or len(token)>200:
        raise ValueError('Subscription cancellation details need review.')
    return call('/subscription/disable','POST',{'code':code,'token':token})


def verify(reference):
    return call('/transaction/verify/'+identifier(reference))
