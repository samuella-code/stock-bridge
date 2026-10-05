"""Verified subscription ledger with durable external-operation staging."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse
from flask import url_for, current_app
from sqlalchemy import update
from app import db
from app.models import User, AccountBilling, RecurringSubscription, Payment, BillingEvent
from app.subscriptions.entitlements import plan_spec, account_for, selected_business, effective_access
from app.subscriptions import provider


def timestamp(value):
    if not isinstance(value,str):
        raise ValueError('Provider billing dates are missing; access was not extended.')
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError,OverflowError):
        raise ValueError('Provider billing dates need review; access was not extended.')


def lock_account(user_id):
    db.session.execute(update(User).where(User.id==user_id).values(last_activity_at=User.last_activity_at))


def notice(user_id,key,kind,message,subscription_id=None):
    if not BillingEvent.query.filter_by(event_key=key).first():
        db.session.add(BillingEvent(event_key=key,user_id=user_id,kind=kind,message=message,subscription_id=subscription_id))


def customer(data):
    value=data.get('customer')
    if not isinstance(value,dict) or not isinstance(value.get('email'),str):
        raise ValueError('Provider customer details are malformed.')
    return value


def subscription_details(sub):
    data=provider.fetch_subscription(sub.provider_subscription_code)
    spec=plan_spec(sub.plan_code,sub.billing_interval)
    if (data.get('domain')!=provider.mode() or data.get('subscription_code')!=sub.provider_subscription_code
        or not provider.validate_plan(data.get('plan') or {},sub.provider_plan_code,spec)
        or customer(data)['email'].lower()!=db.session.get(User,sub.user_id).email.lower()
        or (sub.provider_customer_code and customer(data).get('customer_code')!=sub.provider_customer_code)):
        raise ValueError('Subscription ownership or plan does not match. Contact support.')
    return data


def begin_checkout(user,plan,interval,*,replacement_of_id=None):
    code,spec=provider.configured_plan(plan,interval)
    if not provider.validate_plan(provider.call('/plan/'+code),code,spec):
        raise ValueError('Configured Paystack plan price or interval differs; checkout stopped.')
    if not user.email_verified_at or user.role!='user' or user.admin_enabled or not user.businesses:
        raise ValueError('Verify your customer account before subscribing.')
    lock_account(user.id)
    pending=RecurringSubscription.query.filter(RecurringSubscription.user_id==user.id,
        RecurringSubscription.status.in_(('initializing','checkout_pending','replacement_unknown','scheduled'))).first()
    if pending:
        if pending.status=='checkout_pending' and pending.plan_code==plan and pending.billing_interval==interval and pending.checkout_url:
            return pending.checkout_url
        raise ValueError('A billing operation is already pending. Complete it or contact support before trying again.')
    active=RecurringSubscription.query.filter(RecurringSubscription.user_id==user.id,
        RecurringSubscription.current_period_end>datetime.utcnow(),RecurringSubscription.cancel_at_period_end.is_(False),
        RecurringSubscription.status.in_(('active','past_due'))).first()
    if active:
        raise ValueError('Use Change Plan to stop the old renewal before starting another subscription.')
    account=account_for(user,create=True)
    if account.legacy_granted_at and plan!='plus':
        raise ValueError('Your Lifetime Access already includes the core tools. Only choose Plus voluntarily.')
    sub=RecurringSubscription(user_id=user.id,business_id=selected_business(user).id,plan_code=plan,
        billing_interval=interval,amount_kobo=spec['amount'],provider_plan_code=code,
        checkout_reference='SBS-'+uuid.uuid4().hex,status='initializing',replacement_of_id=replacement_of_id)
    db.session.add(sub);db.session.flush()
    payment=Payment(user_id=user.id,business_id=sub.business_id,subscription_id=sub.id,
        customer_email=user.email,reference=sub.checkout_reference,product='subscription',
        plan_code=plan,billing_interval=interval,amount_kobo=spec['amount'])
    db.session.add(payment)
    # A committed pending operation survives unknown network outcomes. Do not retry it blindly.
    db.session.commit()
    callback=url_for('subscriptions.callback',_external=True)
    if __import__('os').getenv('VERCEL_ENV')=='production':
        callback=f"https://{current_app.config['CUSTOMER_HOST']}"+url_for('subscriptions.callback')
    data=provider.call('/transaction/initialize','POST',{'email':user.email,'amount':spec['amount'],
        'currency':'NGN','plan':code,'reference':payment.reference,'callback_url':callback,'channels':['card'],
        'metadata':{'product':'stockbridge_subscription','user_id':user.id,'subscription_id':sub.id,
                    'plan_code':plan,'billing_interval':interval,'customer_email':user.email}})
    parsed=urlparse(data.get('authorization_url',''))
    if data.get('reference')!=payment.reference or parsed.scheme!='https' or parsed.hostname!='checkout.paystack.com':
        raise ValueError('Provider checkout response needs review. Do not start a second checkout.')
    sub.checkout_url=data['authorization_url'];sub.status='checkout_pending';db.session.commit()
    return sub.checkout_url


def attach_subscription(code):
    provider.identifier(code)
    sub=RecurringSubscription.query.filter_by(provider_subscription_code=code).first()
    data=provider.fetch_subscription(code)
    if not sub:
        client=customer(data);plan=data.get('plan') or {}
        if not isinstance(plan,dict):raise ValueError('Provider plan is malformed.')
        matches=RecurringSubscription.query.join(User,User.id==RecurringSubscription.user_id).filter(
            db.func.lower(User.email)==client['email'].lower(),
            RecurringSubscription.provider_plan_code==plan.get('plan_code'),
            RecurringSubscription.provider_subscription_code.is_(None),
            RecurringSubscription.status.in_(('initializing','checkout_pending','replacement_unknown'))).limit(2).all()
        if len(matches)!=1:
            raise ValueError('Subscription cannot be linked unambiguously. Contact support.')
        sub=matches[0]
        sub.provider_subscription_code=code
    # Check all facts before persisting the association; caller rolls back any failure.
    spec=plan_spec(sub.plan_code,sub.billing_interval)
    if (data.get('domain')!=provider.mode() or data.get('subscription_code')!=code
        or not provider.validate_plan(data.get('plan') or {},sub.provider_plan_code,spec)
        or customer(data)['email'].lower()!=db.session.get(User,sub.user_id).email.lower()):
        raise ValueError('Provider subscription ownership, mode or plan differs.')
    sub.provider_customer_code=provider.identifier(customer(data).get('customer_code'))
    return sub,data


def confirm_payment(sub,payment,data,period=None,details=None):
    lock_account(sub.user_id)
    db.session.refresh(sub)
    if payment.id:
        db.session.refresh(payment)
    if payment.status=='success' and payment.paid_at:
        return True
    user=db.session.get(User,sub.user_id)
    if (data.get('domain')!=provider.mode() or data.get('status')!='success'
        or data.get('reference')!=payment.reference or type(data.get('amount')) is not int
        or data['amount']!=sub.amount_kobo or payment.amount_kobo!=sub.amount_kobo
        or data.get('currency')!='NGN' or payment.currency!='NGN' or payment.provider!='paystack' or payment.product!='subscription' or customer(data)['email'].lower()!=user.email.lower()
        or customer(data).get('customer_code')!=sub.provider_customer_code
        or payment.user_id!=sub.user_id or payment.subscription_id!=sub.id or payment.business_id!=sub.business_id):
        raise ValueError('Payment verification did not match the expected account and amount.')
    if payment.reference==sub.checkout_reference:
        meta=data.get('metadata') or {}
        if isinstance(meta,str):
            try:meta=json.loads(meta)
            except ValueError:raise ValueError('Invalid payment metadata.')
        if not isinstance(meta,dict) or meta.get('product')!='stockbridge_subscription' or meta.get('user_id')!=sub.user_id or meta.get('subscription_id')!=sub.id or meta.get('plan_code')!=sub.plan_code or meta.get('billing_interval')!=sub.billing_interval:
            raise ValueError('Payment metadata did not match the initialized subscription.')
    paid_at=timestamp(data.get('paid_at') or data.get('paidAt'))
    details=details or subscription_details(sub)
    if period:
        start,end=period
    else:
        start,end=paid_at,timestamp(details.get('next_payment_date'))
    if sub.starts_at and start < sub.starts_at:
        raise ValueError('Replacement payment predates the agreed start date.')
    if not period and (end-paid_at).days > (32 if sub.billing_interval=='monthly' else 366):
        raise ValueError('Delayed initial payment needs its original invoice period; access was not extended.')
    if (end-start).total_seconds() > (32 if sub.billing_interval=='monthly' else 366)*86400:
        raise ValueError('Invoice period exceeds the configured billing interval; needs review.')
    if end<=start or end<=paid_at:
        raise ValueError('Provider paid period is invalid. Access was not extended.')
    lock_account(user.id)
    if not sub.current_period_end or end>sub.current_period_end:
        sub.current_period_start=start;sub.current_period_end=end
        sub.next_renewal_at=end;sub.subscription_started_at=sub.subscription_started_at or start
        sub.status='cancel_at_period_end' if sub.cancel_at_period_end else 'active'
    payment.status='success';payment.paid_at=paid_at
    notice(user.id,'payment:'+payment.reference,'subscription_paid',
        f'Your StockBridge {sub.plan_code.title()} {sub.billing_interval} payment is verified. Paid access ends {end:%d %b %Y}.',sub.id)
    return True


def verify_checkout(user,reference):
    payment=Payment.query.filter_by(reference=reference,user_id=user.id,product='subscription').first()
    if not payment:
        raise ValueError('That subscription payment does not belong to your account.')
    sub=db.session.get(RecurringSubscription,payment.subscription_id)
    data=provider.verify(payment.reference)
    if not sub.provider_subscription_code:
        code=(data.get('subscription') or {}).get('subscription_code') if isinstance(data.get('subscription'),dict) else None
        if not code:
            # Payment arrival before subscription.create: preserve pending state and reconcile via that event.
            raise ValueError('Payment verification is awaiting subscription confirmation. Please check again shortly.')
        sub,details=attach_subscription(code)
    else:details=subscription_details(sub)
    confirm_payment(sub,payment,data,details=details)
    db.session.commit()


def cancel(user,sub):
    if sub.user_id!=user.id:
        raise ValueError('This subscription does not belong to your account.')
    lock_account(user.id)
    db.session.refresh(sub)
    if sub.cancel_at_period_end:return
    scheduled=sub.status=='scheduled' and not sub.current_period_end
    if not sub.provider_subscription_code or (not sub.current_period_end and not scheduled):
        raise ValueError('Subscription is still pending; contact support before cancelling.')
    data=subscription_details(sub)
    provider.disable_subscription(sub.provider_subscription_code,data.get('email_token'))
    sub.cancel_at_period_end=True;sub.cancelled_at=datetime.utcnow();sub.status='cancelled' if scheduled else 'cancel_at_period_end'
    account=account_for(user)
    fallback=' Your original Lifetime Access will remain available.' if account and account.legacy_granted_at else ' Your data will remain available in read-only mode.'
    notice(user.id,'cancel:'+sub.provider_subscription_code,'cancellation_scheduled',
        ('Your scheduled subscription was cancelled before its start.' if scheduled else f'Renewal is stopped. Paid access continues until {sub.current_period_end:%d %b %Y}.')+fallback,sub.id)
    db.session.commit()


def change_plan(user,sub,plan,interval):
    spec=plan_spec(plan,interval)
    account=account_for(user)
    if account and account.legacy_granted_at and plan!='plus':
        raise ValueError('Your Lifetime Access already includes Basic tools. Plus is optional.')
    if sub.user_id!=user.id:raise ValueError('This subscription does not belong to your account.')
    if (sub.plan_code,sub.billing_interval)==(plan,interval):raise ValueError('That plan and interval are already selected.')
    if sub.current_period_end is None or sub.current_period_end<=datetime.utcnow():
        raise ValueError('Choose a new checkout after your paid period has ended.')
    current=effective_access(user).subscription
    if not current or current.id!=sub.id:
        raise ValueError('Only your currently paid subscription can be changed.')
    # Immediate Basic→Plus only when interval stays the same. All interval changes defer.
    immediate=sub.plan_code=='basic' and plan=='plus' and interval==sub.billing_interval
    code,spec=provider.configured_plan(plan,interval)
    if not provider.validate_plan(provider.call('/plan/'+code),code,spec):raise ValueError('The replacement plan differs from the configured price.')
    lock_account(user.id)
    pending=RecurringSubscription.query.filter(RecurringSubscription.user_id==user.id,RecurringSubscription.id!=sub.id,RecurringSubscription.status.in_(('initializing','checkout_pending','replacement_unknown','scheduled'))).first()
    if pending:raise ValueError('A billing operation is already pending. Complete it before changing plans.')
    prior=RecurringSubscription.query.filter_by(replacement_of_id=sub.id).first()
    if prior:
        raise ValueError('A plan change already exists. Do not submit a second request; contact support if needed.')
    details=subscription_details(sub)
    auth=details.get('authorization') or {}
    if not immediate and not (auth.get('reusable') is True and auth.get('authorization_code')):
        raise ValueError('A reusable authorization is needed. Cancel and choose a new plan after the paid period instead.')
    cancel(user,sub) # Stops old renewal before any new charge/authorization.
    lock_account(user.id)
    if RecurringSubscription.query.filter_by(replacement_of_id=sub.id).first():
        raise ValueError('A replacement is already pending; do not submit again.')
    if immediate:return begin_checkout(user,plan,interval,replacement_of_id=sub.id)
    replacement=RecurringSubscription(user_id=user.id,business_id=sub.business_id,plan_code=plan,
        billing_interval=interval,amount_kobo=spec['amount'],provider_plan_code=code,
        replacement_of_id=sub.id,starts_at=sub.current_period_end,status='replacement_unknown')
    db.session.add(replacement);db.session.commit()
    data=provider.call('/subscription','POST',{'customer':sub.provider_customer_code,'plan':code,
        'authorization':auth['authorization_code'],'start_date':sub.current_period_end.replace(tzinfo=timezone.utc).isoformat()})
    replacement.provider_subscription_code=provider.identifier(data.get('subscription_code'))
    replacement.provider_customer_code=sub.provider_customer_code
    subscription_details(replacement)
    replacement.status='scheduled'
    notice(user.id,f'change:{replacement.id}','plan_change_scheduled',
        f'{plan.title()} {interval} is scheduled for {sub.current_period_end:%d %b %Y}. No custom proration applies.',replacement.id)
    db.session.commit()
    return None


def process_event(event):
    name=event.get('event');data=event.get('data') or {}
    if not isinstance(data,dict):raise ValueError('Invalid billing event.')
    if data.get('domain')!=provider.mode():raise ValueError('Billing event mode differs.')
    if name=='charge.success':
        payment=Payment.query.filter_by(reference=data.get('reference'),product='subscription').first()
        if not payment:return False # Unknown renewal waits for its authoritative invoice.update.
        sub=db.session.get(RecurringSubscription,payment.subscription_id)
        if not sub.provider_subscription_code:return False
        confirm_payment(sub,payment,provider.verify(payment.reference));db.session.commit();return True
    if name not in ('subscription.create','subscription.not_renew','subscription.disable','invoice.create','invoice.update','invoice.payment_failed'):
        return False
    subscription=data.get('subscription') or {}
    code=data.get('subscription_code') or (subscription.get('subscription_code') if isinstance(subscription,dict) else None)
    if not code:return False
    sub,details=attach_subscription(code)
    lock_account(sub.user_id)
    invoice=data.get('invoice_code') or str(data.get('id') or '')
    key='event:'+hashlib.sha256(f'{name}:{code}:{invoice}:{data.get("period_end","")}'.encode()).hexdigest()
    if BillingEvent.query.filter_by(event_key=key).first():return True
    if name=='subscription.create':
        # Provider-created is not paid. Reconcile only our stored, verified checkout reference.
        if sub.checkout_reference:
            payment=Payment.query.filter_by(reference=sub.checkout_reference,subscription_id=sub.id).first()
            verified=provider.verify(payment.reference)
            if verified.get('status')=='success':confirm_payment(sub,payment,verified,details=details)
    elif name=='invoice.update' and data.get('paid') is True and data.get('status')=='success':
        tx=data.get('transaction') or {};reference=tx.get('reference') if isinstance(tx,dict) else None
        if not reference:raise ValueError('Paid invoice has no transaction reference. Needs reconciliation.')
        period=(timestamp(data.get('period_start')),timestamp(data.get('period_end')))
        verified=provider.verify(reference)
        payment=Payment.query.filter_by(reference=reference).first()
        if payment and payment.subscription_id!=sub.id:raise ValueError('Invoice transaction belongs to another subscription.')
        if not payment:
            user=db.session.get(User,sub.user_id)
            payment=Payment(user_id=sub.user_id,business_id=sub.business_id,subscription_id=sub.id,
                customer_email=user.email,reference=reference,product='subscription',plan_code=sub.plan_code,
                billing_interval=sub.billing_interval,amount_kobo=sub.amount_kobo)
            db.session.add(payment)
        confirm_payment(sub,payment,verified,period,details)
    elif name=='invoice.payment_failed':
        failed_start=timestamp(data.get('period_start'))
        if not sub.current_period_end or failed_start>=sub.current_period_end:
            sub.status='past_due'
            notice(sub.user_id,'failed:'+code+':'+invoice,'renewal_failed',
                "We couldn't renew your StockBridge subscription. Update your billing information. Your data and original Lifetime Access, if any, are retained.",sub.id)
    elif name in ('subscription.not_renew','subscription.disable'):
        if details.get('status') in ('non-renewing','cancelled','completed'):
            sub.cancel_at_period_end=True;sub.cancelled_at=sub.cancelled_at or datetime.utcnow()
            sub.status='cancel_at_period_end' if sub.current_period_end and sub.current_period_end>datetime.utcnow() else 'cancelled'
    elif name not in ('invoice.create','invoice.update'):
        return False
    notice(sub.user_id,key,'provider_event','Billing event reconciled.',sub.id)
    db.session.commit()
    return True


def card_update_link(user,sub):
    if sub.user_id!=user.id or not sub.provider_subscription_code:
        raise ValueError('This subscription cannot be managed from this account.')
    subscription_details(sub)
    data=provider.call('/subscription/'+provider.identifier(sub.provider_subscription_code)+'/manage/link')
    link=data.get('link','');parsed=urlparse(link)
    if parsed.scheme!='https' or parsed.hostname!='paystack.com' or not parsed.path.startswith('/manage/subscriptions/') or parsed.username or parsed.password:
        raise ValueError('Provider billing link needs review.')
    return link
