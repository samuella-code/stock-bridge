"""Owned business context and serialized, entitlement-limited creation."""
from datetime import datetime
from uuid import uuid4
from flask import current_app
from itsdangerous import URLSafeTimedSerializer, BadData
from sqlalchemy import update
from app import db
from app.models import User, Business, BillingEvent, RecurringSubscription


def owned_businesses(user):
    return Business.query.filter_by(user_id=user.id).order_by(Business.id).all()


def selection_key(user):
    # Each paid Plus generation requires its own explicit lower-tier choice.
    previous = RecurringSubscription.query.filter_by(user_id=user.id, plan_code='plus').filter(
        RecurringSubscription.current_period_start.isnot(None)).order_by(RecurringSubscription.id.desc()).first()
    return f'business-selection:{user.id}:{previous.id if previous else 0}'


def selection_required(user, access=None):
    from app.subscriptions.entitlements import effective_access
    if not current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        return False
    access = access or effective_access(user)
    if access.business_limit != 1 or Business.query.filter_by(user_id=user.id).count() <= 1:
        return False
    return not BillingEvent.query.filter_by(event_key=selection_key(user), user_id=user.id).first()


def accessible_business(user, business, access=None):
    from app.subscriptions.entitlements import effective_access, account_for
    if not business or business.user_id != user.id or business.suspended_at:
        return False
    if not current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        return business.id == (owned_businesses(user)[0].id if user.businesses else None)
    access = access or effective_access(user)
    if selection_required(user, access):
        return False
    if access.business_limit > 1:
        ids = [row[0] for row in db.session.query(Business.id).filter_by(user_id=user.id).order_by(Business.id).limit(access.business_limit)]
        return business.id in ids
    account = account_for(user)
    primary = account.primary_business_id if account else None
    if primary and not Business.query.filter_by(id=primary, user_id=user.id).first():
        primary = None
    # Expired accounts retain read-only access to their selected business, never to locked businesses.
    if primary is None and Business.query.filter_by(user_id=user.id).count() == 1:
        primary = Business.query.filter_by(user_id=user.id).first().id
    return business.id == primary


def lock_owner(user):
    # A write lock is obtained BEFORE checking limits. PostgreSQL serializes on
    # this user row; SQLite serializes writers. A no-op leaves account data intact.
    db.session.execute(update(User).where(User.id == user.id).values(full_name=User.full_name))
    db.session.expire_all()


def creation_token(user):
    return URLSafeTimedSerializer(current_app.secret_key, salt='business-create').dumps(
        {'user': user.id, 'nonce': uuid4().hex})


def create_business(user, name, token):
    from app.subscriptions.entitlements import effective_access, account_for
    if not current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        raise ValueError('Additional businesses are not available yet.')
    try:
        data = URLSafeTimedSerializer(current_app.secret_key, salt='business-create').loads(token, max_age=3600)
        if data['user'] != user.id or len(data['nonce']) != 32:
            raise ValueError
    except (BadData, KeyError, TypeError, ValueError):
        raise ValueError('Open Add Business again before submitting.') from None
    name = name.strip()
    if not name or len(name) > 140:
        raise ValueError('Enter a business name of 1–140 characters.')
    lock_owner(user)
    key = f'business-created:{user.id}:{data["nonce"]}'
    previous = BillingEvent.query.filter_by(event_key=key, user_id=user.id).first()
    if previous:
        return Business.query.filter_by(id=int(previous.message), user_id=user.id).one()
    access = effective_access(user)
    if not access.can_write:
        raise ValueError('An active plan or trial is required to add a business.')
    count = Business.query.filter_by(user_id=user.id).count()
    if count >= access.business_limit:
        raise ValueError('Your plan supports '+str(access.business_limit)+' business'+('es.' if access.business_limit != 1 else '. Upgrade to Plus to add another business.'))
    now = datetime.utcnow()
    business = Business(user_id=user.id, name=name, subscription_plan='starter',
        subscription_status='inactive', trial_started_at=now, trial_ends_at=now)
    db.session.add(business)
    db.session.flush()
    account = account_for(user, create=True)
    if count == 0:
        account.primary_business_id = business.id
    # Claim internal audit events so the billing mail worker does not send them.
    db.session.add(BillingEvent(event_key=key, user_id=user.id, kind='business_created',
        message=str(business.id), email_claimed_at=now))
    return business


def choose_primary(user, business_id):
    from app.subscriptions.entitlements import account_for, effective_access
    lock_owner(user)
    business = Business.query.filter_by(id=business_id, user_id=user.id).first()
    if not business or business.suspended_at:
        return None
    access = effective_access(user)
    if access.business_limit != 1:
        raise ValueError('Business selection is only required for a one-business entitlement.')
    account = account_for(user, create=True)
    account.primary_business_id = business.id
    key = selection_key(user)
    if not BillingEvent.query.filter_by(event_key=key, user_id=user.id).first():
        now = datetime.utcnow()
        db.session.add(BillingEvent(event_key=key, user_id=user.id, kind='business_selected',
            message='Primary business selected; all other business data preserved.',
            email_claimed_at=now))
    return business
