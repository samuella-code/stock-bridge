"""Explicit expected access outcomes, scalar/bulk parity and query bounds on SQLite."""
from datetime import datetime, timedelta
import pytest
from sqlalchemy import event
from app import db
from app.models import AccountBilling, BillingEvent, RecurringSubscription
from app.subscriptions.entitlements import effective_access, EntitlementSnapshot, PLANS, ENTITLEMENT_BATCH_SIZE
from app.businesses.service import selection_key, selection_required
from tests.test_admin_portal import client
from tests.test_admin_subscription_reporting import customer

NOW = datetime(2026, 10, 25, 12)


def check(user, business, expected, *, now=NOW):
    # Expectations are supplied explicitly, not calculated by the scalar evaluator.
    scalar = effective_access(user, business=business, now=now)
    snapshot = EntitlementSnapshot([user], now=now)
    bulk = snapshot.access(user, business=business)
    for access in (scalar, bulk):
        assert (access.kind, access.can_write, access.business_limit) == expected
    assert scalar == bulk
    return snapshot


@pytest.mark.parametrize('plan,interval,limit', [
    ('basic','monthly',1), ('basic','yearly',1), ('plus','monthly',2),
    ('plus','yearly',2), ('pro','monthly',5), ('pro','yearly',5)])
def test_all_plans_and_intervals(client, plan, interval, limit):
    user, shops, sub = customer(plan, interval, now=NOW)
    db.session.commit()
    check(user, shops[0], (plan, True, limit))


@pytest.mark.parametrize('status,write', [('active',True), ('past_due',True),
    ('cancel_at_period_end',True), ('cancelled',True), ('checkout_pending',False), ('failed',False)])
def test_status_semantics(client, status, write):
    user, shops, sub = customer(now=NOW, status=status)
    db.session.commit()
    check(user, shops[0], ('pro',True,5) if write else ('restricted',False,0))


@pytest.mark.parametrize('offset,expected', [(-1,('restricted',False,0)), (0,('pro',True,5)),
                                           (1,('pro',True,5)), (2,('restricted',False,0))])
def test_period_boundaries(client, offset, expected):
    user, shops, sub = customer(now=NOW)
    sub.current_period_start = NOW
    sub.current_period_end = NOW+timedelta(seconds=2)
    db.session.commit()
    check(user, shops[0], expected, now=NOW+timedelta(seconds=offset))


@pytest.mark.parametrize('offset,kind,write,days', [(-1,'restricted',False,0),
    (0,'trial',True,1), (1,'trial',True,1), (2,'restricted',False,0)])
def test_trial_boundaries(client, offset, kind, write, days):
    user, shops, sub = customer(now=NOW)
    sub.status = 'failed'
    account = db.session.get(AccountBilling, user.id)
    account.trial_started_at, account.trial_ends_at = NOW, NOW+timedelta(seconds=2)
    db.session.commit()
    snapshot = check(user, shops[0], (kind,write,1 if write else 0), now=NOW+timedelta(seconds=offset))
    assert snapshot.access(user).trial_days_remaining == days


@pytest.mark.parametrize('plan,kind,limit', [('basic','basic',1),
    ('plus','legacy_lifetime_plus',2), ('pro','legacy_lifetime_pro',5),
    ('expired','legacy_lifetime',1)])
def test_legacy_overlays(client, plan, kind, limit):
    user, shops, sub = customer('basic' if plan=='expired' else plan, now=NOW)
    account=db.session.get(AccountBilling,user.id)
    account.legacy_granted_at, account.legacy_payment_id = NOW, 123
    if plan=='expired': sub.current_period_end=NOW
    db.session.commit()
    check(user, shops[0], (kind,True,limit))


@pytest.mark.parametrize('condition,legacy,write', [('suspended',False,False),
    ('unverified',False,False), ('admin',True,False), ('dual',False,False), ('dual',True,True)])
def test_user_restrictions(client, condition, legacy, write):
    user, shops, sub = customer(now=NOW)
    if condition=='suspended': user.suspended_at=NOW
    elif condition=='unverified': user.email_verified_at=None
    elif condition=='admin': user.role='admin'
    elif condition=='dual': user.admin_enabled=True
    if legacy:
        account=db.session.get(AccountBilling,user.id)
        account.legacy_granted_at, account.legacy_payment_id=NOW,123
    db.session.commit()
    check(user, shops[0], ('legacy_lifetime_pro' if legacy else 'pro',write,5))


def test_subscription_winner_and_id_tie_break(client):
    user, shops, old=customer('basic',now=NOW)
    newer=RecurringSubscription(user_id=user.id,business_id=shops[0].id,plan_code='plus',
        billing_interval='yearly',amount_kobo=5000000,provider_plan_code='synthetic',status='active',
        current_period_start=old.current_period_start+timedelta(hours=1),current_period_end=old.current_period_end)
    db.session.add(newer);db.session.flush()
    tied=RecurringSubscription(user_id=user.id,business_id=shops[0].id,plan_code='pro',
        billing_interval='monthly',amount_kobo=1000000,provider_plan_code='synthetic',status='active',
        current_period_start=newer.current_period_start,current_period_end=old.current_period_end)
    db.session.add(tied)
    # Higher ID with an older start must not beat the tied period winner.
    db.session.add(RecurringSubscription(user_id=user.id,business_id=shops[0].id,plan_code='basic',
        billing_interval='monthly',amount_kobo=300000,provider_plan_code='synthetic',status='active',
        current_period_start=old.current_period_start,current_period_end=old.current_period_end))
    db.session.commit()
    snap=check(user,shops[0],('pro',True,5))
    assert snap.access(user).subscription.id==tied.id


@pytest.mark.parametrize('plan,allowed', [('basic',1),('plus',2),('pro',5)])
def test_slots_include_suspended_businesses_and_foreign_denial(client, plan, allowed):
    client.application.config['SUBSCRIPTIONS_ENABLED']=True
    user, shops, sub=customer(plan,now=NOW,businesses=6)
    other, foreign, _=customer(now=NOW)
    shops[0].suspended_at=NOW
    db.session.add(BillingEvent(user_id=user.id,event_key=selection_key(user),kind='selection',message='test'))
    db.session.commit()
    for i,shop in enumerate(shops):
        check(user,shop,(plan, bool(i and i<allowed),allowed))
    check(user,foreign[0],(plan,False,allowed))


@pytest.mark.parametrize('enabled', [False,True])
@pytest.mark.parametrize('history', ['past','future-failed'])
def test_invalid_primary_and_selection_history(client, enabled, history):
    client.application.config['SUBSCRIPTIONS_ENABLED']=enabled
    user,shops,current=customer('basic',now=NOW,businesses=2)
    other,foreign,_=customer(now=NOW)
    account=db.session.get(AccountBilling,user.id)
    account.primary_business_id=foreign[0].id
    previous=RecurringSubscription(user_id=user.id,business_id=shops[0].id,plan_code='pro',
        billing_interval='monthly',amount_kobo=1000000,provider_plan_code='synthetic',status='cancelled',
        current_period_start=NOW-timedelta(days=10),current_period_end=NOW-timedelta(days=5))
    if history == 'future-failed':
        previous.current_period_start = NOW+timedelta(days=10)
        previous.status = 'failed'
    db.session.add(previous);db.session.flush()
    db.session.add(BillingEvent(user_id=user.id,event_key=f'business-selection:{user.id}:0',kind='selection',message='stale'))
    # An event for the exact key but owned by someone else must not authorize.
    db.session.add(BillingEvent(user_id=other.id,event_key=selection_key(user),kind='selection',message='foreign'))
    db.session.commit()
    check(user,shops[0],('basic',False,1))
    assert selection_required(user)==enabled
    account.primary_business_id=shops[0].id
    foreign_event=BillingEvent.query.filter_by(event_key=selection_key(user)).one()
    db.session.delete(foreign_event);db.session.flush()
    db.session.add(BillingEvent(user_id=user.id,event_key=selection_key(user),kind='selection',message='correct'))
    db.session.commit()
    check(user,shops[0],('basic',True,1))
    assert not selection_required(user)


def test_single_business_invalid_primary_fallback_and_empty_account(client):
    user,shops,sub=customer('basic',now=NOW)
    other,foreign,_=customer(now=NOW)
    account=db.session.get(AccountBilling,user.id)
    account.primary_business_id=foreign[0].id
    db.session.commit()
    check(user,shops[0],('basic',True,1))
    sub.status='failed';db.session.delete(account);db.session.commit()
    check(user,shops[0],('restricted',False,0))


def test_snapshot_scope_refresh_and_query_bounds(client):
    user,shops,sub=customer(now=NOW)
    other,foreign,_=customer(now=NOW)
    db.session.commit()
    snapshot=EntitlementSnapshot([user],now=NOW)
    with pytest.raises(ValueError): snapshot.access(other)
    check(user,foreign[0],('pro',False,5))
    statements=[]
    def record(*args): statements.append(args[2])
    engine=db.engine
    event.listen(engine,'before_cursor_execute',record)
    try:
        for shop in shops*5: assert snapshot.access(user,business=shop).can_write
    finally: event.remove(engine,'before_cursor_execute',record)
    assert statements==[]
    # Discard the old snapshot after its operation; the next one sees new state.
    sub.current_period_end=NOW;db.session.commit()
    assert EntitlementSnapshot([user],now=NOW).access(user).kind=='restricted'


@pytest.mark.parametrize('size,queries', [(10,4),(100,4),(201,8)])
def test_explicit_batch_limit(client,size,queries):
    users=[customer(now=NOW)[0] for _ in range(size)]
    db.session.flush()
    statements=[]
    def record(*args): statements.append(args[2])
    engine=db.engine
    event.listen(engine,'before_cursor_execute',record)
    try: snapshot=EntitlementSnapshot(users,now=NOW)
    finally: event.remove(engine,'before_cursor_execute',record)
    assert ENTITLEMENT_BATCH_SIZE==200
    assert len(statements)==queries
    assert len(snapshot.results)==size



def test_next_request_refreshes_snapshot_and_one_instant(client, monkeypatch):
    from app.admin import subscription_reporting
    from tests.test_admin_portal import seed, admin_login
    seed(client)
    user, shops, sub=customer(now=datetime.utcnow())
    db.session.commit()
    snapshots=[]
    original=subscription_reporting.EntitlementSnapshot
    def captured(*args, **kwargs):
        snapshot=original(*args, **kwargs)
        snapshots.append(snapshot)
        return snapshot
    monkeypatch.setattr(subscription_reporting,'EntitlementSnapshot',captured)
    admin_login(client)
    first=client.get('/api/admin/reports').json
    assert first['current_subscription_accounts']['total']==1
    assert len(snapshots)==1
    sub.status='failed';db.session.commit()
    second=client.get('/api/admin/reports').json
    assert second['current_subscription_accounts']['total']==0
    assert len(snapshots)==2 and snapshots[0] is not snapshots[1]
    assert second['current_subscription_accounts']==second['period_metrics']['current_subscription_accounts']
