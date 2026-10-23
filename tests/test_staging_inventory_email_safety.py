"""Isolated SQLite, blocked HTTP/SMTP, and PostgreSQL SQL compilation only."""
from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql

from app import db
from app.models import EmailOutbox, NotificationPreference
from app.notifications.email_outbox import dispatch_one, claim
from app.notifications.staging_email_policy import policy, METADATA, COHORT_KEY, ORIGIN
from tests.test_multi_business import app
from tests.test_notifications_business_profile import setup, product, sale, restock


@pytest.fixture
def staging(app, monkeypatch):
    for key, value in METADATA.items(): monkeypatch.setenv(key, value)
    app.config.update(INVENTORY_EMAILS_ENABLED=True, INVENTORY_EMAIL_BASE_URL=ORIGIN,
        INVENTORY_EMAIL_TRIGGER_URL='', STAGING_INVENTORY_EMAIL_TEST_ENABLED=True,
        STAGING_INVENTORY_EMAIL_TEST_RUN_ID='a'*32,
        STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS='multi@example.invalid',
        STAGING_INVENTORY_EMAIL_TEST_STARTED_AT=(datetime.utcnow()-timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ'))
    monkeypatch.setattr('app.notifications.email_outbox.requests.post',
                        Mock(side_effect=AssertionError('External HTTP forbidden')))
    sent=[]
    monkeypatch.setattr('app.email_service._send_email', lambda *a,**k:sent.append(a) or True)
    return sent


def test_each_inventory_event_sends_once_in_current_cohort(app,staging):
    client,_,_=setup(app); p=product(client)
    sale(client,p,3); sale(client,p,5); restock(client,p,4)
    assert [r.event_type for r in EmailOutbox.query.order_by(EmailOutbox.id)]==[
        'low_stock','out_of_stock','restock_recorded']
    assert all(r.payload[COHORT_KEY]=='a'*32 for r in EmailOutbox.query.all())
    assert len(staging)==0
    for _ in range(3): assert client.post('/notifications/email-dispatch').json=={'result':'sent'}
    assert dispatch_one()=='idle' and len(staging)==3
    assert all(r.status=='sent' and r.attempt_count==1 for r in EmailOutbox.query.all())


@pytest.mark.parametrize('status', ['pending','retry','processing','failed','sent','suppressed'])
@pytest.mark.parametrize('tagged', [False,True])
def test_historical_jobs_untouched_including_exhausted(app,staging,status,tagged):
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    old=EmailOutbox.query.one()
    old.status=status;old.attempt_count=old.max_attempts
    old.created_at=datetime.utcnow()-timedelta(days=8)
    old.next_attempt_at=old.created_at;old.claim_expires_at=old.created_at
    if not tagged:old.payload={k:v for k,v in old.payload.items() if k!=COHORT_KEY}
    db.session.commit()
    before={c.name:getattr(old,c.name) for c in EmailOutbox.__table__.columns}
    sale(client,p,5)
    assert dispatch_one()=='sent' and len(staging)==1
    assert dispatch_one()=='idle'
    db.session.refresh(old)
    assert {c.name:getattr(old,c.name) for c in EmailOutbox.__table__.columns}==before


@pytest.mark.parametrize('change', ['unapproved_at_creation','removed_after_creation','changed_owner_email'])
def test_unapproved_recipients_never_claimed_or_sent(app,staging,change):
    client,user,_=setup(app)
    if change=='unapproved_at_creation':app.config['STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS']='approved@example.invalid'
    p=product(client);sale(client,p,3)
    row=EmailOutbox.query.one()
    if change=='removed_after_creation':app.config['STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS']='approved@example.invalid'
    if change=='changed_owner_email':
        user.email='changed@example.invalid';db.session.commit()
    result=dispatch_one()
    assert result in ('idle','failed') and not staging
    if change!='changed_owner_email':
        db.session.refresh(row);assert row.status=='pending' and row.attempt_count==0


@pytest.mark.parametrize('key,value', [
    ('STAGING_INVENTORY_EMAIL_TEST_ENABLED',False),
    ('STAGING_INVENTORY_EMAIL_TEST_RUN_ID',''),
    ('STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS',''),
    ('STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS','bad\n@example.invalid'),
    ('STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS','a@example.invalid,a@example.invalid'),
    ('STAGING_INVENTORY_EMAIL_TEST_STARTED_AT','bad'),
    ('STAGING_INVENTORY_EMAIL_TEST_STARTED_AT','2026-02-30T00:00:00Z'),
    ('STAGING_INVENTORY_EMAIL_TEST_STARTED_AT','2000-01-01T00:00:00Z'),
    ('INVENTORY_EMAIL_BASE_URL','https://production.example.invalid'),
    ('INVENTORY_EMAIL_BASE_URL',None),
    ('STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS','approved@example.invalid;other'),
    ('STAGING_INVENTORY_EMAIL_TEST_STARTED_AT','2999-01-01T00:00:00Z'),
])
def test_bad_configuration_does_not_touch_jobs(app,staging,key,value):
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    app.config[key]=value
    assert dispatch_one()=='disabled' and claim() is None
    row=EmailOutbox.query.one();assert row.status=='pending' and row.attempt_count==0 and not staging


@pytest.mark.parametrize('key',list(METADATA))
def test_missing_identity_fails_closed(app,staging,monkeypatch,key):
    monkeypatch.delenv(key)
    assert policy().mode=='blocked' and dispatch_one()=='disabled' and not staging


def test_staging_production_target_rejected(app,staging,monkeypatch):
    monkeypatch.setenv('VERCEL_ENV','production');monkeypatch.setenv('VERCEL_TARGET_ENV','production')
    assert dispatch_one()=='disabled' and not staging


def test_other_preview_project_rejected(app,staging,monkeypatch):
    monkeypatch.setenv('VERCEL_PROJECT_ID','prj_other')
    assert dispatch_one()=='disabled' and not staging


def test_smtp_failure_retries_only_new_approved_job(app,staging,monkeypatch):
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    def failing(*a,**k):raise ConnectionError('synthetic-private-provider-value')
    monkeypatch.setattr('app.email_service._send_email',failing)
    assert dispatch_one()=='retry'
    row=EmailOutbox.query.one()
    assert row.status=='retry' and row.attempt_count==1 and row.next_attempt_at>row.last_attempt_at
    assert dispatch_one()=='idle'
    monkeypatch.setattr('app.email_service._send_email',lambda *a,**k:staging.append(a) or True)
    assert dispatch_one(now=row.next_attempt_at)=='sent' and len(staging)==1


def test_window_expiry_never_sends_or_changes_job(app,staging):
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    assert dispatch_one(now=datetime.utcnow()+timedelta(hours=2))=='disabled'
    row=EmailOutbox.query.one();assert row.status=='pending' and row.attempt_count==0 and not staging


def test_preference_recheck_preserved(app,staging):
    client,_,shops=setup(app);p=product(client);sale(client,p,3)
    db.session.add(NotificationPreference(business_id=shops[0].id,low_stock_email=False));db.session.commit()
    assert dispatch_one()=='suppressed' and not staging


def test_new_cohort_does_not_release_previous_run(app,staging):
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    app.config['STAGING_INVENTORY_EMAIL_TEST_RUN_ID']='b'*32
    assert dispatch_one()=='idle' and not staging
    assert EmailOutbox.query.one().attempt_count==0


def test_allowlist_rechecked_immediately_before_smtp(app,staging,monkeypatch):
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    from app.notifications import email_outbox
    original=email_outbox.render_template
    def render(*a,**k):
        html=original(*a,**k)
        app.config['STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS']='other@example.invalid'
        return html
    monkeypatch.setattr(email_outbox,'render_template',render)
    assert dispatch_one()=='suppressed' and not staging


def test_postgres_filters_compile_without_connection(app,staging):
    conditions=policy().conditions()
    for stmt in (select(EmailOutbox).where(*conditions).with_for_update(skip_locked=True),
                 update(EmailOutbox).where(*conditions).values(status='failed')):
        compiled=stmt.compile(dialect=postgresql.dialect())
        sql=str(compiled)
        assert 'email_outbox.created_at' in sql and 'lower(email_outbox.recipient_email)' in sql
        assert '->>' in sql and 'CAST' in sql
        assert COHORT_KEY in compiled.params.values()


def test_defaults_and_production_behavior_unchanged(app,monkeypatch):
    for key in METADATA:monkeypatch.delenv(key,raising=False)
    from app.config_safety import PRODUCTION_PROJECT_ID
    monkeypatch.setenv('VERCEL_PROJECT_ID',PRODUCTION_PROJECT_ID)
    monkeypatch.setenv('VERCEL_ENV','production');monkeypatch.setenv('VERCEL_TARGET_ENV','production')
    assert app.config['STAGING_INVENTORY_EMAIL_TEST_ENABLED'] is False
    assert policy().mode=='production' and policy().conditions()==()
    app.config.update(INVENTORY_EMAILS_ENABLED=True,INVENTORY_EMAIL_BASE_URL='https://customer.example.invalid',INVENTORY_EMAIL_TRIGGER_URL='')
    sent=[]
    monkeypatch.setattr('app.email_service._send_email',lambda *a,**k:sent.append(a) or True)
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    assert COHORT_KEY not in EmailOutbox.query.one().payload
    assert dispatch_one()=='sent' and dispatch_one()=='idle' and len(sent)==1


def test_missing_all_metadata_cannot_release_staging_backlog(app,staging,monkeypatch):
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    for key in METADATA:monkeypatch.delenv(key,raising=False)
    app.config['STAGING_INVENTORY_EMAIL_TEST_ENABLED']=False
    assert dispatch_one()=='disabled' and claim() is None
    assert EmailOutbox.query.one().attempt_count==0 and not staging


def test_browser_and_worker_enforce_same_policy(app,staging):
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    app.config['INVENTORY_EMAIL_DISPATCH_SECRET']='test-only-dispatch-key-'+'z'*32
    app.config['STAGING_INVENTORY_EMAIL_TEST_ENABLED']=False
    assert client.post('/notifications/email-dispatch').json=={'result':'disabled'}
    response=client.post('/internal/inventory-emails/dispatch',headers={
        'Authorization':'Bearer '+app.config['INVENTORY_EMAIL_DISPATCH_SECRET']})
    assert response.status_code==200 and response.json=={'result':'disabled'}
    assert EmailOutbox.query.one().attempt_count==0 and not staging


def test_old_untagged_recent_jobs_not_adopted(app,staging):
    client,_,_=setup(app);p=product(client)
    app.config['STAGING_INVENTORY_EMAIL_TEST_ENABLED']=False
    sale(client,p,3)
    app.config['STAGING_INVENTORY_EMAIL_TEST_ENABLED']=True
    assert dispatch_one()=='idle' and not staging
    assert COHORT_KEY not in EmailOutbox.query.one().payload


def test_staging_smtp_has_no_open_database_transaction(app,staging,monkeypatch):
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    def transport(*a,**k):
        assert not db.session().in_transaction()
        staging.append(a)
        return True
    monkeypatch.setattr('app.email_service._send_email',transport)
    assert dispatch_one()=='sent' and len(staging)==1


def test_case_insensitive_allowlist_preserves_restock_preferences(app,staging):
    app.config['STAGING_INVENTORY_EMAIL_TEST_RECIPIENTS']='  MULTI@example.invalid  '
    client,_,shops=setup(app);p=product(client)
    db.session.add(NotificationPreference(business_id=shops[0].id,restocking=False));db.session.commit()
    restock(client,p,2)
    assert EmailOutbox.query.count()==0
    sale(client,p,5)
    assert dispatch_one()=='sent' and len(staging)==1


@pytest.mark.parametrize('metadata', [
    {},
    {'VERCEL':'1'},
    {'VERCEL_ENV':'production'},
    {'VERCEL_TARGET_ENV':'production'},
    {'VERCEL_ENV':'production','VERCEL_TARGET_ENV':'production'},
    {'VERCEL_PROJECT_ID':METADATA['VERCEL_PROJECT_ID']},
    {'VERCEL_PROJECT_ID':METADATA['VERCEL_PROJECT_ID'],'VERCEL_ENV':'production','VERCEL_TARGET_ENV':'production'},
    {'VERCEL_PROJECT_ID':'prj_unknown','VERCEL_ENV':'production','VERCEL_TARGET_ENV':'production'},
    {'VERCEL_PROJECT_ID':METADATA['VERCEL_PROJECT_ID'],'VERCEL_ENV':'preview'},
    {'VERCEL_PROJECT_ID':METADATA['VERCEL_PROJECT_ID'],'VERCEL_TARGET_ENV':'preview'},
    {'VERCEL_PROJECT_ID':METADATA['VERCEL_PROJECT_ID'],'VERCEL_ENV':'preview','VERCEL_TARGET_ENV':'production'},
    {'VERCEL_PROJECT_ID':METADATA['VERCEL_PROJECT_ID'],'VERCEL_ENV':'production','VERCEL_TARGET_ENV':'preview'},
])
@pytest.mark.parametrize('origin', ['', 'https://wrong.example.invalid'])
def test_uncertain_identity_never_claims_jobs_even_without_staging_origin(app,staging,monkeypatch,metadata,origin):
    client,_,_=setup(app);p=product(client);sale(client,p,3)
    row=EmailOutbox.query.one()
    row.attempt_count=row.max_attempts
    db.session.commit()
    before={c.name:getattr(row,c.name) for c in EmailOutbox.__table__.columns}
    for key in (*METADATA,'VERCEL','VERCEL_GIT_PROVIDER'):
        monkeypatch.delenv(key,raising=False)
    for key,value in metadata.items():monkeypatch.setenv(key,value)
    app.config.update(STAGING_INVENTORY_EMAIL_TEST_ENABLED=False,INVENTORY_EMAIL_BASE_URL=origin)
    assert policy().mode=='blocked'
    assert dispatch_one()=='disabled' and claim() is None
    assert not staging
    db.session.refresh(row)
    assert {c.name:getattr(row,c.name) for c in EmailOutbox.__table__.columns}==before


@pytest.mark.parametrize('key,value', [
    ('VERCEL_PROJECT_ID',None), ('VERCEL_PROJECT_ID',''), ('VERCEL_PROJECT_ID','prj_unknown'),
    ('VERCEL_ENV',None), ('VERCEL_ENV',''), ('VERCEL_ENV','preview'), ('VERCEL_ENV','development'),
    ('VERCEL_TARGET_ENV',None), ('VERCEL_TARGET_ENV',''), ('VERCEL_TARGET_ENV','preview'),
    ('VERCEL_GIT_REPO_OWNER','wrong-owner'), ('VERCEL_GIT_REPO_SLUG','wrong-repo'),
    ('VERCEL_GIT_PROVIDER','gitlab'), ('VERCEL','0'),
])
def test_incomplete_or_conflicting_production_identity_rejected(app,monkeypatch,key,value):
    from app.config_safety import PRODUCTION_PROJECT_ID
    env={**METADATA,'VERCEL_PROJECT_ID':PRODUCTION_PROJECT_ID,
         'VERCEL_ENV':'production','VERCEL_TARGET_ENV':'production',
         'VERCEL':'1','VERCEL_GIT_PROVIDER':'github'}
    for field,setting in env.items():monkeypatch.setenv(field,setting)
    if value is None:monkeypatch.delenv(key,raising=False)
    else:monkeypatch.setenv(key,value)
    app.config.update(INVENTORY_EMAILS_ENABLED=True,INVENTORY_EMAIL_BASE_URL='https://customer.example.invalid')
    assert policy().mode=='blocked' and dispatch_one()=='disabled' and claim() is None


def test_production_identity_cannot_override_staging_test_configuration(app,staging,monkeypatch):
    from app.config_safety import PRODUCTION_PROJECT_ID
    monkeypatch.setenv('VERCEL_PROJECT_ID',PRODUCTION_PROJECT_ID)
    monkeypatch.setenv('VERCEL_ENV','production');monkeypatch.setenv('VERCEL_TARGET_ENV','production')
    assert policy().mode=='blocked'  # Explicit staging opt-in remains present.
    app.config['STAGING_INVENTORY_EMAIL_TEST_ENABLED']=False
    assert policy().mode=='blocked'  # Staging origin also conflicts with production.
    assert dispatch_one()=='disabled' and not staging
