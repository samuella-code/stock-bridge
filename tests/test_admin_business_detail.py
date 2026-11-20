"""Business detail coverage using disposable SQLite and mocked email/provider I/O."""
from datetime import datetime, timedelta
from unittest.mock import Mock
import pytest
from app import db
from app.models import AuditLog, Business, Payment, RecurringSubscription, Sale, Expense, User
from app.admin import business_detail_data as projection
from tests.test_admin_portal import client, seed, admin_login
from tests.test_admin_customers import records

@pytest.fixture(autouse=True)
def no_external(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('External service forbidden')
    monkeypatch.setattr('app.admin.routes.verify_transaction', forbidden)
    monkeypatch.setattr('app.email_service._send_email', forbidden)


def form(records, **changes):
    b=db.session.get(Business,records['shop'])
    values=dict(business_name=b.name,confirm='on',reason='Violated rules',note='',
        desired_state='suspend',expected_state='active',expected_suspended_at='')
    values.update(changes)
    return values


def test_page_uses_authoritative_plan_masked_owner_team_and_local_dates(client, records, tmp_path):
    sub=db.session.get(RecurringSubscription,records['sub']);sub.cancel_at_period_end=True
    db.session.commit();admin_login(client)
    before=AuditLog.query.count()
    response=client.get(f"/admin/businesses/{records['shop']}")
    page=response.get_data(as_text=True)
    assert response.status_code==200 and page.count('<h1>')==1
    assert 'Pro · Monthly' in page and 'Renewal stopped' in page and 'Renewal is off.' in page
    assert 'employee@example.invalid' not in page
    assert db.session.get(User,records['owner']).email not in page
    assert 'Employee' in page and 'Salesperson/Cashier' in page and '1 employee assigned' in page
    assert 'Show</button>' in page and 'Showing a full email is recorded in Activity.' in page
    assert 'Open read-only view' in page and f'business_id={records["shop"]}' in page
    assert 'data-customer-action="message"' in page and 'Send win-back message' in page
    assert 'UTC' not in page and 'WAT' in page
    assert 'name="business_name" required' in page and 'data-status-submit disabled' in page
    assert '<input type="checkbox" name="confirm"' not in page
    assert AuditLog.query.count()==before
    (tmp_path/'page.html').write_text(page)
    (tmp_path/'csp.txt').write_text(response.headers['Content-Security-Policy'])


def test_counts_last_records_and_payment_scope(client, records):
    now=datetime.utcnow()
    b=db.session.get(Business,records['shop'])
    active=Sale(business_id=b.id,sold_at=now-timedelta(days=1))
    void=Sale(business_id=b.id,sold_at=now,voided_at=now)
    other=Sale(business_id=records['other_shop'],sold_at=now)
    exp=Expense(business_id=b.id,description='Void',amount=100,voided_at=now)
    p=Payment(business_id=b.id,user_id=b.user_id,product='subscription',plan_code='pro',billing_interval='monthly',
        customer_email=b.owner.email,reference='SB-'+('long-reference-'*4),amount_kobo=120050,status='success',paid_at=now)
    foreign=Payment(business_id=b.id,user_id=records['original'],product='subscription',
        customer_email='foreign@example.invalid',reference='FOREIGN-REFERENCE',amount_kobo=100,status='success')
    db.session.add_all([active,void,other,exp,p,foreign]);db.session.commit()
    result=projection.details(b,now)
    tiles={v['label']:v for v in result['tiles']}
    assert tiles['Sales']['count']==1 and tiles['Expenses']['count']==0
    assert f'Sale #{active.id}' in tiles['Sales']['last']
    assert len(result['payments'])==1
    assert result['payments'][0]['amount']=='₦1,200.50'
    assert len(result['payments'][0]['short_reference'])<len(p.reference)
    admin_login(client);page=client.get(f'/admin/businesses/{b.id}').get_data(as_text=True)
    assert 'FOREIGN-REFERENCE' not in page
    assert f'>{p.reference}</a>' not in page and f'data-copy-reference="{p.reference}"' in page


def test_activity_groups_only_same_admin_business_and_five_minute_window(client, records):
    now=datetime.utcnow();b=db.session.get(Business,records['shop'])
    for i in range(6):db.session.add(AuditLog(actor_id=records['admin'],business_id=b.id,
        action='ADMIN_READ_ONLY_OPENED',description='Admin inspection opened for account 7.',
        target_type='user',target_id=b.user_id,created_at=now-timedelta(seconds=i*20)))
    db.session.add(AuditLog(actor_id=records['owner'],business_id=b.id,action='ADMIN_READ_ONLY_OPENED',
        description='account 7',target_type='user',target_id=b.user_id,created_at=now-timedelta(seconds=10)))
    db.session.add(AuditLog(actor_id=records['admin'],business_id=b.id,action='ADMIN_READ_ONLY_OPENED',
        description='account 7',target_type='user',target_id=b.user_id,created_at=now-timedelta(minutes=6)))
    db.session.add(AuditLog(actor_id=records['admin'],business_id=records['other_shop'],action='ADMIN_READ_ONLY_OPENED',
        description='Foreign business',created_at=now))
    db.session.commit();count=AuditLog.query.count()
    items=projection.activity(b,now)
    views=[i['count'] for i in items if i['action']=='ADMIN_READ_ONLY_OPENED']
    assert views[:2]==[6,1] and max(views)==6  # the older view is not merged into the six
    assert all('account 7' not in i['sentence'] for i in items)
    assert all('Foreign business' not in i['sentence'] for i in items)
    assert AuditLog.query.count()==count
    admin_login(client);page=client.get(f'/admin/businesses/{b.id}').get_data(as_text=True)
    assert '(6 times)' in page and 'account 7' not in page


@pytest.mark.parametrize('wrong',['','Shop','wrong',' shop0 ','SHOP0'])
def test_name_confirmation_is_server_enforced_without_email_or_audit(client, records, wrong, monkeypatch):
    admin_login(client);sent=Mock();monkeypatch.setattr('app.admin.routes.send_account_status_email',sent)
    response=client.post(f"/admin/businesses/{records['shop']}/suspension",data=form(records,business_name=wrong,email_owner='on'))
    assert response.status_code==302
    assert db.session.get(Business,records['shop']).suspended_at is None
    assert AuditLog.query.filter_by(action='BUSINESS_SUSPENDED').count()==0
    sent.assert_not_called()


@pytest.mark.parametrize('notice',[False,True])
def test_suspend_reactivate_are_audited_and_optional_notice_is_once(client, records, notice, monkeypatch):
    admin_login(client);sent=Mock(return_value=True)
    monkeypatch.setattr('app.admin.routes.send_account_status_email',sent)
    values=form(records,note='Reviewed by the team',email_owner='on' if notice else '')
    path=f"/admin/businesses/{records['shop']}/suspension"
    assert client.post(path,data=values).status_code==302
    b=db.session.get(Business,records['shop']);assert b.suspended_at
    assert client.post(path,data=values).status_code==409
    log=AuditLog.query.filter_by(action='BUSINESS_SUSPENDED').one()
    assert log.actor_id==records['admin'] and log.business_id==b.id
    assert 'Violated rules' in log.description and 'Reviewed by the team' in log.description
    assert sent.call_count==int(notice)
    page=client.get(f'/admin/businesses/{b.id}').get_data(as_text=True)
    assert 'Reactivate business' in page and 'data-status-submit disabled' in page
    values=form(records,desired_state='reactivate',expected_state='suspended',
        expected_suspended_at=b.suspended_at.isoformat(),reason='Review complete',email_owner='on' if notice else '')
    assert client.post(path,data=values).status_code==302
    assert db.session.get(Business,b.id).suspended_at is None
    assert AuditLog.query.filter_by(action='BUSINESS_REACTIVATED',actor_id=records['admin']).count()==1
    assert sent.call_count==2*int(notice)
    assert client.post(path,data=values).status_code==409
    assert sent.call_count==2*int(notice)


@pytest.mark.parametrize('changes',[dict(reason='Unexpected'),dict(note='x'*201),dict(email_owner='yes')])
def test_invalid_fields_do_not_change_business(client, records, changes):
    admin_login(client)
    assert client.post(f"/admin/businesses/{records['shop']}/suspension",data=form(records,**changes)).status_code==400
    assert db.session.get(Business,records['shop']).suspended_at is None


def test_failed_notification_does_not_undo_suspension(client, records, monkeypatch):
    admin_login(client);sent=Mock(side_effect=RuntimeError('Synthetic transport error'))
    monkeypatch.setattr('app.admin.routes.send_account_status_email',sent)
    assert client.post(f"/admin/businesses/{records['shop']}/suspension",data=form(records,email_owner='on')).status_code==302
    assert db.session.get(Business,records['shop']).suspended_at
    assert AuditLog.query.filter_by(action='BUSINESS_SUSPENDED').count()==1


def test_guards_and_protected_accounts(client,records):
    path=f"/admin/businesses/{records['shop']}"
    assert client.get(path).status_code==302
    assert client.post(path+'/suspension',data=form(records)).status_code==302
    admin_login(client)
    protected=Business(user_id=records['admin'],name='Protected');db.session.add(protected);db.session.commit()
    assert client.get(f'/admin/businesses/{protected.id}').status_code==404
    assert client.post(f'/admin/businesses/{protected.id}/suspension',data={'business_name':'Protected'}).status_code==403
    client.application.config['WTF_CSRF_ENABLED']=True
    assert client.post(path+'/suspension',data=form(records)).status_code==400


def test_reactivation_keeps_account_and_plan_restrictions(client, records):
    from app.businesses.service import accessible_business
    from app.staff import service
    now=datetime.utcnow();admin_login(client)
    b=db.session.get(Business,records['shop']);b.suspended_at=now
    owner=b.owner;owner.suspended_at=now
    db.session.commit()
    values=form(records,desired_state='reactivate',expected_state='suspended',
        expected_suspended_at=now.isoformat(),reason='Review complete')
    assert client.post(f'/admin/businesses/{b.id}/suspension',data=values).status_code==302
    assert db.session.get(Business,b.id).suspended_at is None
    assert db.session.get(User,owner.id).suspended_at is not None
    assert not projection.details(b,now)['status']=='Active'
    # Reactivation never restores employees while the owner is suspended.
    from app.models import StaffMembership
    member=db.session.get(StaffMembership,records['member'])
    assert service.member_businesses(member,db.session.get(User,records['employee']),now=now)==[]


def test_detail_queries_compile_for_postgres_without_a_connection(client, records):
    from sqlalchemy import event
    from sqlalchemy.dialects import postgresql
    compiled=[]
    def capture(conn,clause,*args):
        if getattr(clause,'is_select',False):compiled.append(str(clause.compile(dialect=postgresql.dialect())))
    event.listen(db.engine,'before_execute',capture)
    try:
        projection.details(db.session.get(Business,records['shop']),datetime.utcnow())
        admin_login(client)
        client.post(f"/admin/businesses/{records['shop']}/suspension",data=form(records))
    finally:event.remove(db.engine,'before_execute',capture)
    assert compiled and any('FOR UPDATE' in sql for sql in compiled)
