"""Disconnected SQLite and mocked SMTP; no live mail, provider or PostgreSQL."""
from datetime import datetime, timedelta
import re
import socket

import pytest
from sqlalchemy import event

from app import db, email_service
from app.admin import reminder_service as service, notification_data
from app.config_safety import STAGING_PROJECT_ID
from app.models import (User,Business,AccountBilling,RecurringSubscription,Payment,AdminReminderDelivery,
    AdminReminderBatch,AdminReminderSchedule,CustomerReminderPreference,AdminAlertPreference,
    AdminReminderFeedback,AdminCustomerMessage,AdminLoginAttempt,AuditLog)
from tests.test_admin_portal import client, seed, admin_login

NOW=datetime(2026,11,20,12)
SHA='a'*40

@pytest.fixture(autouse=True)
def disconnected(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('Network access forbidden in disconnected regression')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(email_service,'_send_email',forbidden)

@pytest.fixture
def records(client):
    admin_id,owner_id,shop_id=seed(client)
    admin=db.session.get(User,admin_id);owner=db.session.get(User,owner_id);shop=db.session.get(Business,shop_id)
    account=AccountBilling(user_id=owner.id,trial_started_at=NOW-timedelta(days=7),trial_ends_at=NOW,
        primary_business_id=shop.id)
    db.session.add(account);db.session.commit()
    return admin,owner,shop,account

@pytest.fixture
def approved(client,monkeypatch):
    client.application.config.update(ADMIN_REMINDERS_EMAIL_ENABLED='true',ADMIN_REMINDERS_AUTOMATION_ENABLED='true',
        ADMIN_REMINDERS_APPROVED_SHA=SHA,ADMIN_REMINDERS_ALLOWED_RECIPIENTS='owner@example.com,admin@example.com',
        ADMIN_REMINDERS_ORIGIN=service.ORIGIN,SMTP_HOST='smtp.invalid',SMTP_FROM_EMAIL='reports@example.com')
    env=dict(VERCEL='1',VERCEL_PROJECT_ID=STAGING_PROJECT_ID,VERCEL_ENV='preview',VERCEL_TARGET_ENV='preview',
        VERCEL_GIT_REPO_OWNER='samuella-code',VERCEL_GIT_REPO_SLUG='stock-bridge',
        VERCEL_GIT_COMMIT_REF='release/staging-phase6',VERCEL_GIT_COMMIT_SHA=SHA)
    for key,value in env.items():monkeypatch.setenv(key,value)
    messages=[]
    monkeypatch.setattr(email_service,'_send_email',lambda *args: messages.append(args) or True)
    return messages


def claim(records,key='request_key_123456',**kwargs):
    admin,owner,_,_=records
    return service.claim(admin,key,'trial',[owner.id],service.MESSAGES['trial'],auth_version=admin.admin_auth_version,now=NOW,**kwargs)


def send(records,row,**kwargs):
    return service.deliver(row.id,auth_version=records[0].admin_auth_version,now=NOW,**kwargs)


def subscription(records,ends=None):
    _,owner,shop,account=records
    sub=RecurringSubscription(user_id=owner.id,business_id=shop.id,plan_code='pro',billing_interval='monthly',
        provider_plan_code='PLN_disconnected',amount_kobo=1000000,status='active',current_period_start=NOW-timedelta(days=20),
        current_period_end=ends or NOW+timedelta(days=7),cancel_at_period_end=True,cancelled_at=NOW-timedelta(days=1),
        created_at=NOW-timedelta(days=20))
    db.session.add(sub);db.session.commit();return sub


@pytest.mark.parametrize('flag',['ADMIN_REMINDERS_EMAIL_ENABLED','ADMIN_REMINDERS_AUTOMATION_ENABLED'])
def test_disabled_dispatch_makes_no_sql_or_transport(client,flag,approved):
    client.application.config[flag]=''
    statements=[]
    def capture(*args):statements.append(args)
    event.listen(db.engine,'before_cursor_execute',capture)
    try:
        assert service.dispatch_one(NOW)=='disabled'
        assert service.dispatch_alert_one(NOW)=='disabled'
    finally:event.remove(db.engine,'before_cursor_execute',capture)
    assert not statements and not approved


@pytest.mark.parametrize('key,value',[('VERCEL','0'),('VERCEL_PROJECT_ID','wrong'),('VERCEL_ENV','production'),
    ('VERCEL_TARGET_ENV','production'),('VERCEL_GIT_REPO_OWNER','other'),('VERCEL_GIT_REPO_SLUG','other'),
    ('VERCEL_GIT_COMMIT_REF','main'),('VERCEL_GIT_COMMIT_SHA','b'*40)])
def test_wrong_identity_rejected_before_queries(client,approved,monkeypatch,key,value):
    monkeypatch.setenv(key,value)
    assert service.email_gate()[0]=='configuration_required'
    assert service.dispatch_one(NOW)=='disabled' and not approved


@pytest.mark.parametrize('key,value',[('ADMIN_REMINDERS_APPROVED_SHA','b'*40),('ADMIN_REMINDERS_ALLOWED_RECIPIENTS',''),
    ('ADMIN_REMINDERS_ALLOWED_RECIPIENTS','Owner@example.com'),('ADMIN_REMINDERS_ORIGIN','https://evil.invalid'),
    ('SMTP_HOST',''),('SMTP_FROM_EMAIL','')])
def test_missing_approval_rejected(client,approved,key,value):
    client.application.config[key]=value
    assert service.email_gate()[0]=='configuration_required' and not approved


def test_all_clear_and_email_only_ui(client,records):
    admin_login(client)
    page=client.get('/admin/notifications')
    html=page.get_data(as_text=True)
    assert page.status_code==200 and 'All clear' in html
    assert html.count('<h1>')==1 and 'WhatsApp' not in html and 'UTC' not in html
    assert html.count('role="switch"')==3 and 'Send win-back message' in html
    assert 'name="csrf_token"' in html and 'Business access' not in html
    for kind in ('trial','winback'):
        response=client.get('/admin/reminders/'+kind)
        assert response.status_code==200
        assert b'Email delivery needs separate approval' in response.data
        assert b'owner@example.com' not in response.data and b'WhatsApp' not in response.data
        assert b'name="message"' in response.data and b'Send a test to me' in response.data
        assert response.data.count(b'role="switch"')==6


@pytest.mark.parametrize('change,reason',[
    ('employee','not_owner'),('admin','not_owner'),('suspended','unavailable'),('unverified','unavailable'),
    ('stopped','stopped'),('legacy','existing_access'),('old','outside_window'),('suspended_shop','no_business')])
def test_trial_exclusions(client,records,change,reason):
    _,owner,shop,account=records
    if change=='employee':owner.staff_only=True
    if change=='admin':owner.admin_enabled=True
    if change=='suspended':owner.suspended_at=NOW
    if change=='unverified':owner.email_verified_at=None
    if change=='stopped':db.session.add(CustomerReminderPreference(user_id=owner.id,stopped_at=NOW))
    if change=='legacy':account.legacy_granted_at=NOW
    if change=='old':account.trial_ends_at=NOW-timedelta(days=31)
    if change=='suspended_shop':shop.suspended_at=NOW
    db.session.commit()
    assert service.candidate(owner,'trial',NOW,override=True)[1]==reason


def test_chosen_plan_never_gets_trial_reminders(client,records):
    subscription(records)
    assert service.candidate(records[1],'trial',NOW)[1]=='plan_chosen'


@pytest.mark.parametrize('state',['renewal_on','new_period','paid_same_period','suspended'])
def test_winback_stops_on_restored_renewal_payment_or_suspension(client,records,state):
    sub=subscription(records);owner=records[1]
    assert service.candidate(owner,'winback',NOW)[1] is None
    if state=='renewal_on':sub.cancel_at_period_end=False;sub.cancelled_at=None
    elif state=='suspended':records[2].suspended_at=NOW
    elif state=='paid_same_period':db.session.add(Payment(user_id=owner.id,business_id=records[2].id,subscription_id=sub.id,
        reference='new-payment',customer_email=owner.email,product='subscription',status='success',amount_kobo=1000000,paid_at=NOW))
    else:
        db.session.add(RecurringSubscription(user_id=owner.id,business_id=records[2].id,plan_code='pro',billing_interval='monthly',
            provider_plan_code='PLN_disconnected',amount_kobo=1000000,status='scheduled',current_period_start=sub.current_period_end,
            current_period_end=sub.current_period_end+timedelta(days=30),created_at=NOW))
    db.session.commit()
    assert service.candidate(owner,'winback',NOW,override=True)[1] in ('renewal_on','paid_again','unavailable')


def test_claim_replay_cap_and_exact_message(client,records,approved):
    batch,new=claim(records)
    assert new and not approved
    same,new=claim(records)
    assert same.id==batch.id and not new
    result=send(records,batch)
    assert result[0]['status']=='sent' and len(approved)==1
    assert 'Hi Owner, your StockBridge trial for Shop has ended.' in approved[0][2]
    assert '/admin/reminders/stop/' in approved[0][2]
    assert send(records,batch)[0]['status']=='sent' and len(approved)==1
    with pytest.raises(ValueError):claim(records,key='request_key_654321')
    batch2,new=claim(records,key='request_key_override',override_reason='Owner requested another email')
    assert send(records,batch2)[0]['status']=='sent' and len(approved)==2
    logs=AuditLog.query.filter_by(action='ADMIN_REMINDER_RESULT').all()
    assert all(log.target_type=='reminder_delivery' for log in logs)


@pytest.mark.parametrize('status',['failed','unknown'])
def test_failed_unknown_never_retry_or_escape_cap(client,records,approved,monkeypatch,status):
    def smtp(*args):
        approved.append(args)
        if status=='unknown':raise RuntimeError('secret provider detail')
        return False
    monkeypatch.setattr(email_service,'_send_email',smtp)
    batch,_=claim(records)
    assert send(records,batch)[0]['status']==status
    send(records,batch)
    assert len(approved)==1 and service.candidate(records[1],'trial',NOW)[1]=='recent_contact'
    assert 'secret provider' not in str([x.description for x in AuditLog.query.all()])


@pytest.mark.parametrize('change',['pay','stop','suspend','email','admin_reset','gate'])
def test_recheck_before_smtp(client,records,approved,change):
    batch,_=claim(records)
    admin,owner,_,_=records
    if change=='pay':subscription(records)
    if change=='stop':db.session.get(CustomerReminderPreference,owner.id).stopped_at=NOW
    if change=='suspend':owner.suspended_at=NOW
    if change=='email':owner.email='changed@example.com'
    if change=='admin_reset':admin.admin_auth_version+=1
    if change=='gate':client.application.config['ADMIN_REMINDERS_EMAIL_ENABLED']=''
    db.session.commit()
    result=service.deliver(batch.id,auth_version=0,now=NOW)
    assert result[0]['status']=='suppressed' and not approved


@pytest.mark.parametrize('days,blocked',[(6,True),(7,False),(8,False)])
def test_cap_boundary_and_generic_message_contact(client,records,days,blocked):
    db.session.add(AdminCustomerMessage(actor_id=records[0].id,user_id=records[1].id,request_key='other',content_digest='x'*64,
        status='unknown',created_at=NOW-timedelta(days=days)))
    db.session.commit()
    assert (service.candidate(records[1],'trial',NOW)[1]=='recent_contact') is blocked


@pytest.mark.parametrize('kind,timing,offset',[('trial','day5',-2),('trial','ends',0),('trial','after3',3),
    ('winback','before7',-7),('winback','ends',0),('winback','after3',3)])
def test_all_six_wat_timings(kind,timing,offset):
    end=datetime(2026,11,20,23) # 21st in WAT
    facts=dict(end=end,trial_start=end-timedelta(days=7))
    due=end+timedelta(days=offset)
    assert service.due(kind,timing,facts,due)
    assert not service.due(kind,timing,facts,due-timedelta(seconds=1))


def test_automatic_claims_replay_and_cap(client,records,approved):
    admin,owner,_,_=records
    db.session.add(AdminReminderSchedule(key='trial:ends',enabled=True,actor_id=admin.id,auth_version=0,mfa_verified=False))
    db.session.commit()
    assert service.dispatch_one(NOW)=='sent'
    assert service.dispatch_one(NOW)=='idle' and len(approved)==1
    assert service.dispatch_one(NOW+timedelta(days=3))=='idle'


def test_automation_switch_rechecked_after_claim(client,records,approved):
    admin=records[0]
    db.session.add(AdminReminderSchedule(key='trial:ends',enabled=True,actor_id=admin.id,auth_version=0,mfa_verified=False));db.session.commit()
    batch,_=service.claim(admin,'auto_trial_request_123','trial',[records[1].id],service.MESSAGES['trial'],
        auth_version=0,now=NOW,automatic=True,schedule_key='trial:ends')
    client.application.config['ADMIN_REMINDERS_AUTOMATION_ENABLED']=''
    assert send(records,batch)[0]['status']=='suppressed' and not approved


def test_test_to_me_only_and_rate_limit(client,records,approved):
    admin=records[0]
    batch,_=service.claim(admin,'test_request_123456','trial',[admin.id],service.MESSAGES['trial'],is_test=True,auth_version=0,now=NOW)
    assert send(records,batch)[0]['status']=='sent' and approved[0][1]==admin.email
    assert CustomerReminderPreference.query.count()==0
    with pytest.raises(ValueError):service.claim(admin,'test_request_654321','trial',[admin.id],service.MESSAGES['trial'],is_test=True,auth_version=0,now=NOW)
    with pytest.raises(ValueError):service.claim(admin,'test_request_notme1','trial',[records[1].id],service.MESSAGES['trial'],is_test=True,auth_version=0,now=NOW)


@pytest.mark.parametrize('extra',[dict(override_reason='short'),dict(feedback=True)])
def test_bad_override_or_feedback_rejected(client,records,approved,extra):
    with pytest.raises(ValueError):claim(records,**extra)
    assert not approved and AdminReminderBatch.query.count()==0


def test_unknown_placeholder_and_allowlist_atomic(client,records,approved):
    with pytest.raises(ValueError):service.claim(records[0],'unknown_placeholder1','trial',[records[1].id],'{password}',auth_version=0,now=NOW)
    db.session.rollback()
    assert AdminReminderBatch.query.count()==0
    client.application.config['ADMIN_REMINDERS_ALLOWED_RECIPIENTS']='admin@example.com'
    with pytest.raises(ValueError):claim(records)
    assert not approved


def test_stop_get_is_read_only_post_stops_and_tokens_are_bound(client,records,approved):
    owner=records[1];value=service.token(owner,'stop');path='/admin/reminders/stop/'+value
    assert client.get(path).status_code==200 and CustomerReminderPreference.query.count()==0
    assert client.get('/admin/reminders/feedback/'+value).status_code==404
    response=client.post(path)
    assert response.status_code==200 and b'have been stopped' in response.data
    assert response.headers['Referrer-Policy']=='same-origin'
    assert service.candidate(owner,'trial',NOW,override=True)[1]=='stopped'
    assert client.post(path).status_code==200
    assert AuditLog.query.filter_by(action='REMINDERS_STOPPED').count()==1
    owner.email='new@example.com';db.session.commit()
    assert client.get(path).status_code==404


def test_public_stop_requires_csrf(client,records):
    path='/admin/reminders/stop/'+service.token(records[1],'stop')
    client.application.config['WTF_CSRF_ENABLED']=True
    assert client.post(path).status_code==400
    assert CustomerReminderPreference.query.count()==0


def test_optional_feedback_is_bound_and_aggregated(client,records,approved):
    admin,owner,_,_=records;subscription(records)
    batch,_=service.claim(admin,'feedback_request_123','winback',[owner.id],service.MESSAGES['winback'],feedback=True,auth_version=0,now=NOW)
    result=send(records,batch);row=db.session.get(AdminReminderDelivery,result[0]['id'])
    path='/admin/reminders/feedback/'+service.token(owner,'feedback',row.id)
    assert client.get(path).status_code==200 and AdminReminderFeedback.query.count()==0
    assert client.post(path,data=dict(reason='Unknown')).status_code==400
    assert client.post(path,data=dict(reason='Too expensive')).status_code==200
    assert client.post(path,data=dict(reason='Too expensive')).status_code==200
    assert client.post(path,data=dict(reason='Other')).status_code==409
    assert service.feedback_summary(datetime(2020,1,1),datetime(2030,1,1))['Too expensive']==1


def test_preferences_and_schedules_default_off_save_without_send(client,records):
    admin_login(client)
    assert client.post('/admin/notifications/preferences',data=dict(version='0',failed_payments='on')).status_code==303
    pref=db.session.get(AdminAlertPreference,records[0].id)
    assert pref.failed_payments and not pref.security_alerts and not pref.daily_summary
    assert client.post('/admin/notifications/preferences',data=dict(version='0')).status_code==409
    data={'version_'+kind+':'+timing:'0' for kind,items in service.TIMINGS.items() for timing,label in items}
    data['trial:ends']='on'
    assert client.post('/admin/reminders/schedules',data=data).status_code==303
    assert AdminReminderSchedule.query.filter_by(enabled=True).count()==1
    assert AdminReminderDelivery.query.count()==0
    assert client.post('/admin/reminders/schedules',data=data).status_code==409


@pytest.mark.parametrize('flag',['failed_payments','security_alerts','daily_summary'])
def test_admin_alerts_are_opt_in_deduplicated_and_email_only(client,records,approved,flag):
    admin=records[0]
    db.session.add(AdminAlertPreference(user_id=admin.id,auth_version=0,**{flag:True}))
    db.session.add(Payment(reference='failed',product='subscription',customer_email='owner@example.com',amount_kobo=100,status='failed'))
    for _ in range(5):db.session.add(AdminLoginAttempt(identifier='hashed-source',attempted_at=NOW))
    db.session.commit()
    assert service.dispatch_alert_one(NOW.replace(hour=7))=='sent' # 08:00 WAT
    assert service.dispatch_alert_one(NOW.replace(hour=7))=='idle' and len(approved)==1
    assert approved[0][1]==admin.email and 'Stop reminders:' in approved[0][2]


def test_worker_secret_and_no_implicit_activation(client,records):
    assert client.get('/admin/internal/reminders/dispatch').status_code==404
    client.application.config['ADMIN_REMINDERS_DISPATCH_SECRET']='x'*40
    response=client.get('/admin/internal/reminders/dispatch',headers={'Authorization':'Bearer '+'x'*40})
    assert response.status_code==200 and response.json['result']=='disabled'


def test_anomalies_and_attention_are_not_all_clear(client,records):
    assert notification_data.decisions(NOW)['clear']
    db.session.add(Payment(reference='unlinked-success',product='subscription',customer_email='owner@example.com',
        amount_kobo=1000000,status='success',paid_at=NOW));db.session.commit()
    result=notification_data.decisions(NOW)
    assert result['anomalies']==1 and not result['clear']


def test_decision_banner_reflects_each_critical_category(client,records):
    admin_login(client)
    assert b'All clear' in client.get('/admin/notifications').data
    row=Payment(reference='needs-review',product='subscription',customer_email='owner@example.com',amount_kobo=100,status='failed')
    db.session.add(row);db.session.commit()
    page=client.get('/admin/notifications')
    assert b'Failed payments' in page.data and b'All clear' not in page.data
    row.status='pending';row.created_at=datetime.utcnow()-timedelta(days=3);db.session.commit()
    assert b'Payments pending over two days' in client.get('/admin/notifications').data
    row.status='success';row.paid_at=NOW;db.session.commit()
    assert b'Payment anomalies' in client.get('/admin/notifications').data


def test_https_public_confirmation_csrf_and_signed_in_get_does_not_start_trial(client,records):
    admin,owner,_,account=records
    account.trial_started_at=None;account.trial_ends_at=None;db.session.commit()
    value=service.token(owner,'stop');path='/admin/reminders/stop/'+value
    with client.session_transaction() as session:
        session['_user_id']=str(owner.id);session['_fresh']=True
    client.application.config.update(SUBSCRIPTIONS_ENABLED=True,WTF_CSRF_ENABLED=True)
    response=client.get(path,base_url='https://localhost')
    assert response.status_code==200
    assert db.session.get(AccountBilling,owner.id).trial_started_at is None
    csrf=re.search(r'name="csrf_token" value="([^"]+)"',response.get_data(as_text=True)).group(1)
    response=client.post(path,base_url='https://localhost',headers={'Referer':'https://localhost'+path},data={'csrf_token':csrf})
    assert response.status_code==200


def test_admin_auth_reset_invalidates_preference_writes(client,records,approved):
    admin_login(client)
    records[0].admin_auth_version+=1;db.session.commit()
    assert client.post('/admin/notifications/preferences',data=dict(version='0',daily_summary='on')).status_code in (302,403)
    assert AdminAlertPreference.query.count()==0 and not approved


def test_schema_upgrade_and_downgrade_disconnected(client):
    import importlib.util
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import inspect
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('reminder_migration',Path('migrations/versions/0022_admin_reminders.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    tables=['admin_alert_preference','admin_reminder_schedule','customer_reminder_preference',
        'admin_reminder_batch','admin_reminder_delivery','admin_reminder_feedback']
    db.session.remove()
    with db.engine.begin() as connection:
        for name in reversed(tables):db.metadata.tables[name].drop(connection)
        context=MigrationContext.configure(connection)
        with Operations.context(context):module.upgrade()
        inspector=inspect(connection)
        assert all(name in inspector.get_table_names() for name in tables)
        assert 'whatsapp' not in str(inspector.get_columns('admin_alert_preference')).lower()
        assert {'is_automatic','schedule_key'} <= {x['name'] for x in inspector.get_columns('admin_reminder_delivery')}
        with Operations.context(context):module.downgrade()
        assert all(name not in inspect(connection).get_table_names() for name in tables)
        for name in tables:db.metadata.tables[name].create(connection)


def test_concurrent_admins_share_one_owner_contact_cap(client,approved,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app import create_app
    app=create_app({'TESTING':True,'SECRET_KEY':'disconnected-concurrency',
        'SQLALCHEMY_DATABASE_URI':'sqlite:///'+str(tmp_path/'reminder-claims.db')})
    app.config.update({key:value for key,value in client.application.config.items() if key.startswith('ADMIN_REMINDERS_') or key.startswith('SMTP_')})
    with app.app_context():
        db.create_all()
        admins=[User(full_name='Operator '+str(n),email='operator'+str(n)+'@example.com',role='admin',email_verified_at=NOW,password_hash='unused') for n in range(2)]
        owner=User(full_name='Owner',email='owner@example.com',email_verified_at=NOW,password_hash='unused')
        db.session.add_all(admins+[owner]);db.session.flush()
        shop=Business(user_id=owner.id,name='Concurrent shop');db.session.add(shop);db.session.flush()
        db.session.add(AccountBilling(user_id=owner.id,trial_started_at=NOW-timedelta(days=7),trial_ends_at=NOW,primary_business_id=shop.id))
        db.session.commit();ids=[a.id for a in admins];owner_id=owner.id
    barrier=Barrier(2)
    def attempt(identifier):
        with app.app_context():
            admin=db.session.get(User,identifier)
            barrier.wait(timeout=5)
            try:
                batch,new=service.claim(admin,'parallel_request_123','trial',[owner_id],service.MESSAGES['trial'],auth_version=0,now=NOW)
                return 'claimed' if new else 'replayed'
            except ValueError:
                db.session.rollback();return 'excluded'
            finally:db.session.remove()
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,ids))
    assert sorted(results)==['claimed','excluded']
    with app.app_context():
        assert AdminReminderDelivery.query.count()==1
        db.session.remove();db.drop_all();db.engine.dispose()
    assert not approved


def test_mfa_required_without_enrolment_rejects_claim(client,records,approved):
    client.application.config['ADMIN_MFA_REQUIRED']=True
    with pytest.raises(ValueError):claim(records)
    assert AdminReminderBatch.query.count()==0 and not approved


def test_disabled_schedule_suppresses_an_already_claimed_automatic_message(client,records,approved):
    admin=records[0]
    schedule=AdminReminderSchedule(key='trial:ends',enabled=True,actor_id=admin.id,auth_version=0,mfa_verified=False)
    db.session.add(schedule);db.session.commit()
    batch,_=service.claim(admin,'scheduled_request_123','trial',[records[1].id],service.MESSAGES['trial'],
        auth_version=0,now=NOW,automatic=True,schedule_key='trial:ends')
    schedule.enabled=False;db.session.commit()
    assert send(records,batch)[0]['status']=='suppressed' and not approved


def test_audit_description_fits_postgresql_limit(client,records,approved):
    batch,_=claim(records,override_reason='A'*300)
    assert all(len(row.description)<=300 for row in AuditLog.query.all())
    assert AdminReminderDelivery.query.filter_by(batch_id=batch.id).one().override_reason=='A'*300


def test_postgres_ddl_compiles_without_connection(client):
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql
    for model in (AdminReminderBatch,AdminReminderDelivery,AdminReminderFeedback,AdminReminderSchedule,AdminAlertPreference,CustomerReminderPreference):
        sql=str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert 'CREATE TABLE' in sql


def test_message_body_access_and_masked_activity(client,records,approved):
    batch,_=claim(records);result=send(records,batch)
    delivery_id=result[0]['id']
    assert client.get(f'/admin/reminders/deliveries/{delivery_id}').status_code==302
    admin_login(client)
    page=client.get(f'/admin/reminders/deliveries/{delivery_id}')
    assert page.status_code==200 and b'Hi Owner' in page.data and b'owner@example.com' not in page.data
    assert b'/admin/reminders/stop/' not in page.data
    activity=client.get('/admin/activity')
    assert f'/admin/reminders/deliveries/{delivery_id}'.encode() in activity.data
    assert b'owner@example.com' not in activity.data


def test_winback_payment_after_claim_suppresses_smtp(client,records,approved):
    admin,owner,shop,_=records;sub=subscription(records)
    batch,_=service.claim(admin,'winback_payment_race','winback',[owner.id],service.MESSAGES['winback'],auth_version=0,now=NOW)
    db.session.add(Payment(reference='renewal-paid',user_id=owner.id,business_id=shop.id,subscription_id=sub.id,
        customer_email=owner.email,product='subscription',status='success',amount_kobo=sub.amount_kobo,paid_at=NOW))
    db.session.commit()
    assert send(records,batch)[0]['status']=='suppressed' and not approved


def test_send_panel_posts_and_employee_cannot_access(client,records,approved):
    admin_login(client)
    response=client.post('/admin/reminders/trial',data=dict(action='send',owners=str(records[1].id),
        request_key='http_send_request_123',message=service.MESSAGES['trial']))
    assert response.status_code==303 and len(approved)==1
    assert b'Delivery results' in client.get(response.location).data
    records[1].staff_only=True;db.session.commit()
    with client.session_transaction() as session:
        session.clear();session['_user_id']=str(records[1].id);session['_fresh']=True
    assert client.get('/admin/notifications').status_code==403
    assert client.post('/admin/reminders/trial',data=dict(action='send')).status_code==403
    assert len(approved)==1


def test_public_link_does_not_repair_business_session(client,records):
    subscription(records)
    client.application.config['SUBSCRIPTIONS_ENABLED']=True
    with client.session_transaction() as session:
        session['_user_id']=str(records[1].id);session['_fresh']=True
    path='/admin/reminders/stop/'+service.token(records[1],'stop')
    assert client.get(path).status_code==200
    with client.session_transaction() as session:assert 'active_business_id' not in session


def test_admin_can_explicitly_restore_own_optional_alerts_after_stopping(client,records):
    admin=records[0]
    db.session.add(CustomerReminderPreference(user_id=admin.id,stopped_at=NOW));db.session.commit()
    admin_login(client)
    assert client.post('/admin/notifications/preferences',data=dict(version='0',daily_summary='on')).status_code==303
    assert db.session.get(CustomerReminderPreference,admin.id).stopped_at is None
    assert db.session.get(AdminAlertPreference,admin.id).daily_summary
