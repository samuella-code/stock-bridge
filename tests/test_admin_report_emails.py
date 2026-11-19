"""Disconnected consent, schedule and durable-delivery regressions. SMTP is always mocked."""
from datetime import datetime, timedelta
from unittest.mock import Mock
import re

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from app import db
from app.admin import report_emails as emails
from app.models import AdminAuthenticator, AdminReportDelivery, AdminReportPreference, AuditLog, User
from tests.test_admin_portal import client, seed, admin_login

MONDAY=datetime(2026,11,23,7)  # 08:00 WAT; previous reporting week is 16–22 November.
URL='/admin/reports/weekly-email'
WORKER='/admin/internal/reports/weekly-dispatch'


@pytest.fixture
def ready(client,monkeypatch):
    admin,owner,shop=seed(client)
    client.application.config.update(ADMIN_REPORT_EMAILS_ENABLED='true',ADMIN_REPORT_EMAILS_APPROVED_SHA='a'*40,
        ADMIN_REPORT_EMAIL_ALLOWED_RECIPIENTS='admin@example.com',ADMIN_REPORT_EMAIL_ORIGIN=emails.ORIGIN,
        ADMIN_REPORT_EMAIL_DISPATCH_SECRET='s'*32,SMTP_HOST='smtp.example.invalid',SMTP_FROM_EMAIL='sender@example.invalid')
    for key,value in dict(VERCEL='1',VERCEL_PROJECT_ID=emails.STAGING_PROJECT_ID,VERCEL_ENV='preview',
        VERCEL_TARGET_ENV='preview',VERCEL_GIT_REPO_OWNER='samuella-code',VERCEL_GIT_REPO_SLUG='stock-bridge',
        VERCEL_GIT_COMMIT_REF='release/staging-phase6',VERCEL_GIT_COMMIT_SHA='a'*40).items():monkeypatch.setenv(key,value)
    send=Mock(return_value=True);monkeypatch.setattr('app.email_service._send_email',send)
    db.session.add(AdminReportPreference(user_id=admin,weekly_enabled=True,auth_version=0,version=1));db.session.commit()
    return admin,owner,send


def test_preferences_are_own_audited_versioned_and_csrf_protected(client):
    admin,owner,shop=seed(client)
    assert client.post(URL,data={'weekly_email':'on','version':'0'}).status_code==302
    admin_login(client);client.application.config['WTF_CSRF_ENABLED']=True
    assert client.post(URL,data={'weekly_email':'on','version':'0'}).status_code==400
    html=client.get('/admin/reports').get_data(as_text=True)
    token=re.search(r'<meta name="csrf-token" content="([^"]+)"',html).group(1)
    data=dict(weekly_email='on',version='0',csrf_token=token,user_id=owner,period='90d')
    response=client.post(URL,data=data)
    assert response.status_code==303 and 'period=90d' in response.location
    row=db.session.get(AdminReportPreference,admin)
    assert row.weekly_enabled and row.version==1
    assert db.session.get(AdminReportPreference,owner) is None
    assert AuditLog.query.filter_by(action='ADMIN_REPORT_PREFERENCE_UPDATED').count()==1
    assert client.post(URL,data=data).status_code==409
    assert client.post(URL,data=dict(version='1',csrf_token=token)).status_code==303
    assert not db.session.get(AdminReportPreference,admin).weekly_enabled
    assert not AdminReportDelivery.query.count()


@pytest.mark.parametrize('change', ['role','suspended','unverified','auth_version','consent'])
def test_ineligible_and_revoked_admins_are_not_sent(client,ready,change):
    admin,owner,send=ready;user=db.session.get(User,admin);pref=db.session.get(AdminReportPreference,admin)
    if change=='role':user.role='user'
    elif change=='suspended':user.suspended_at=MONDAY
    elif change=='unverified':user.email_verified_at=None
    elif change=='auth_version':user.admin_auth_version+=1
    else:pref.weekly_enabled=False
    db.session.commit()
    assert emails.dispatch_one(MONDAY)=='idle'
    send.assert_not_called()


@pytest.mark.parametrize('key,value', [('ADMIN_REPORT_EMAILS_ENABLED','false'),
    ('ADMIN_REPORT_EMAILS_ENABLED',True),('ADMIN_REPORT_EMAILS_APPROVED_SHA','b'*40),
    ('ADMIN_REPORT_EMAIL_ALLOWED_RECIPIENTS','*'),('ADMIN_REPORT_EMAIL_ALLOWED_RECIPIENTS','Admin@example.com'),
    ('ADMIN_REPORT_EMAIL_ORIGIN','https://stock-bridge-one.vercel.app'),('SMTP_HOST','')])
def test_bad_configuration_blocks_before_database_or_transport(client,ready,key,value):
    admin,owner,send=ready;client.application.config[key]=value
    statements=[]
    def capture(*args):statements.append(args)
    event.listen(db.engine,'before_cursor_execute',capture)
    try:assert emails.dispatch_one(MONDAY) in ('disabled','configuration_required')
    finally:event.remove(db.engine,'before_cursor_execute',capture)
    assert not statements;send.assert_not_called()


@pytest.mark.parametrize('key,value',[('VERCEL_PROJECT_ID','prj_ahZe3mfUk4MvejILpj9Md9QPP4fd'),
    ('VERCEL_ENV','production'),('VERCEL_TARGET_ENV','production'),('VERCEL_GIT_COMMIT_REF','other'),
    ('VERCEL_GIT_COMMIT_SHA','b'*40),('VERCEL_GIT_REPO_OWNER','foreign')])
def test_wrong_identity_never_sends(client,ready,monkeypatch,key,value):
    admin,owner,send=ready;monkeypatch.setenv(key,value)
    assert emails.dispatch_one(MONDAY)=='configuration_required';send.assert_not_called()


@pytest.mark.parametrize('stamp',[MONDAY-timedelta(seconds=1),MONDAY+timedelta(hours=4),MONDAY+timedelta(days=1)])
def test_only_monday_morning_and_no_backlog(client,ready,stamp):
    assert emails.dispatch_one(stamp)=='not_due';ready[2].assert_not_called()


def test_claim_precedes_smtp_and_reentrant_dispatch_never_duplicates(client,ready):
    admin,owner,send=ready
    def transport(subject,recipient,body,**kwargs):
        assert AdminReportDelivery.query.one().status=='sending'
        assert AuditLog.query.filter_by(action='ADMIN_REPORT_EMAIL_CLAIMED').count()==1
        assert emails.dispatch_one(MONDAY)=='idle'
        assert recipient=='admin@example.com'
        assert '16/11/2026 to 22/11/2026' in body
        assert 'Collected revenue is not recurring revenue.' in body
        assert 'owner@example.com' not in body and 'owner@example.com' not in kwargs['html']
        assert kwargs['timeout']==5
        return True
    send.side_effect=transport
    assert emails.dispatch_one(MONDAY)=='sent'
    assert emails.dispatch_one(MONDAY)=='idle'
    assert send.call_count==1
    assert AdminReportDelivery.query.one().completed_at
    # A consent toggle cannot turn a completed week into a second delivery.
    pref=db.session.get(AdminReportPreference,admin);pref.version+=2;db.session.commit()
    assert emails.dispatch_one(MONDAY)=='idle'
    send.side_effect=None
    assert emails.dispatch_one(MONDAY+timedelta(days=7))=='sent'
    assert send.call_count==2


@pytest.mark.parametrize('outcome,status',[(False,'failed'),(None,'unknown'),(RuntimeError('private SMTP secret'),'unknown')])
def test_uncertain_or_failed_delivery_is_not_retried(client,ready,outcome,status):
    admin,owner,send=ready
    if isinstance(outcome,Exception):send.side_effect=outcome
    else:send.return_value=outcome
    assert emails.dispatch_one(MONDAY)==status
    assert emails.dispatch_one(MONDAY)=='idle'
    assert send.call_count==1
    assert 'private SMTP secret' not in str([(r.action,r.description) for r in AuditLog.query.all()])


@pytest.mark.parametrize('change',['consent','role','email','auth_version','gate'])
def test_authority_is_rechecked_after_rendering(client,ready,monkeypatch,change):
    admin,owner,send=ready;original=emails.render_template
    def render(*args,**kwargs):
        html=original(*args,**kwargs)
        user=db.session.get(User,admin);pref=db.session.get(AdminReportPreference,admin)
        if change=='consent':pref.weekly_enabled=False
        elif change=='role':user.role='user'
        elif change=='email':user.email='changed@example.invalid'
        elif change=='auth_version':user.admin_auth_version+=1
        else:client.application.config['ADMIN_REPORT_EMAILS_ENABLED']='false'
        db.session.commit();return html
    monkeypatch.setattr(emails,'render_template',render)
    assert emails.dispatch_one(MONDAY)=='suppressed';send.assert_not_called()


def test_scheduler_authentication_and_preference_never_send_immediately(client,ready):
    assert client.get(WORKER).status_code==404
    admin_login(client)
    assert client.get(WORKER).status_code==404 # Cookie is not worker authority.
    client.application.config['ADMIN_REPORT_EMAILS_ENABLED']='false'
    response=client.get(WORKER,headers={'Authorization':'Bearer '+'s'*32})
    assert response.status_code==200 and response.json=={'result':'disabled'}
    assert response.headers['Cache-Control']=='no-store, private'
    assert client.post(URL,data={'version':'1'}).status_code==303
    ready[2].assert_not_called()


def test_duplicate_week_constraint_and_postgres_ddl(client,ready):
    admin,owner,send=ready
    for table in (AdminReportPreference.__table__,AdminReportDelivery.__table__):
        assert 'CREATE TABLE' in str(CreateTable(table).compile(dialect=postgresql.dialect()))
    emails.dispatch_one(MONDAY)
    db.session.add(AdminReportDelivery(user_id=admin,week_start=MONDAY.date()-timedelta(days=7),recipient_digest='0'*64,status='sending'))
    with pytest.raises(IntegrityError):db.session.commit()
    db.session.rollback();assert send.call_count==1


def test_claim_commit_failure_never_reaches_transport(client,ready,monkeypatch):
    def broken():raise RuntimeError('synthetic commit failure')
    monkeypatch.setattr(db.session,'commit',broken)
    with pytest.raises(RuntimeError):emails.dispatch_one(MONDAY)
    db.session.rollback();ready[2].assert_not_called()


def test_abandoned_claim_and_unlisted_admin_do_not_resend(client,ready):
    admin,owner,send=ready
    db.session.add(AdminReportDelivery(user_id=admin,week_start=MONDAY.date()-timedelta(days=7),
        recipient_digest='0'*64,status='sending',created_at=MONDAY-timedelta(hours=2)))
    other=User(full_name='Other admin',email='unlisted@example.invalid',role='admin',password_hash='unused',email_verified_at=MONDAY)
    db.session.add(other);db.session.flush()
    db.session.add(AdminReportPreference(user_id=other.id,weekly_enabled=True,version=1,auth_version=0))
    db.session.commit()
    assert emails.dispatch_one(MONDAY)=='idle';send.assert_not_called()


def test_multiple_authorized_admins_get_separate_private_reports(client,ready):
    admin,owner,send=ready
    other=User(full_name='Other admin',email='other@example.invalid',role='admin',password_hash='unused',email_verified_at=MONDAY)
    db.session.add(other);db.session.flush()
    db.session.add(AdminReportPreference(user_id=other.id,weekly_enabled=True,version=1,auth_version=0));db.session.commit()
    client.application.config['ADMIN_REPORT_EMAIL_ALLOWED_RECIPIENTS']='admin@example.com,other@example.invalid'
    assert emails.dispatch_one(MONDAY)=='sent'
    assert emails.dispatch_one(MONDAY)=='sent'
    assert emails.dispatch_one(MONDAY)=='idle'
    assert [call.args[1] for call in send.call_args_list]==['admin@example.com','other@example.invalid']


def test_new_mfa_requirement_requires_enrolment_and_fresh_verified_consent(client,ready):
    admin,owner,send=ready
    client.application.config['ADMIN_MFA_REQUIRED']=True
    assert emails.dispatch_one(MONDAY)=='idle'
    db.session.add(AdminAuthenticator(user_id=admin,encrypted_secret=b'synthetic',enabled_at=MONDAY));db.session.commit()
    assert emails.dispatch_one(MONDAY)=='idle' # Enrolment alone is not report consent under MFA.
    pref=db.session.get(AdminReportPreference,admin);pref.mfa_verified=True;db.session.commit()
    assert emails.dispatch_one(MONDAY)=='sent'
    assert send.call_count==1


def test_bearer_worker_drains_one_due_email_per_request(client,ready,monkeypatch):
    class Clock(datetime):
        @classmethod
        def utcnow(cls):return MONDAY
    monkeypatch.setattr(emails,'datetime',Clock)
    header={'Authorization':'Bearer '+'s'*32}
    assert client.get(WORKER,headers=header).json=={'result':'sent'}
    assert client.get(WORKER,headers=header).json=={'result':'idle'}
    assert ready[2].call_count==1


def test_disabled_cli_never_sends(client,ready):
    client.application.config['ADMIN_REPORT_EMAILS_ENABLED']='false'
    result=client.application.test_cli_runner().invoke(args=['admin','dispatch-weekly-reports','--limit','2'])
    assert result.exit_code==0 and 'disabled' in result.output
    ready[2].assert_not_called()
