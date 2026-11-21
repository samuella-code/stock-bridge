"""Read-only reminder projection/preview. SQLite and forbidden SMTP only."""
from datetime import timedelta
from html.parser import HTMLParser
import re

import pytest
from sqlalchemy import event
from app import db
from app.models import User, CustomerReminderPreference, AdminReminderBatch, AdminReminderDelivery, AuditLog
from app.admin import reminder_service as service
from tests.test_admin_reminders import client, records, approved, subscription, NOW, disconnected
from tests.test_admin_portal import admin_login


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__();self.rows=[];self.feed(html)
    def handle_starttag(self, tag, attrs):
        values=dict(attrs)
        if tag=='input' and values.get('name')=='owners':self.rows.append(values)


def preview(client, recipient, kind='trial', **values):
    return client.post('/admin/reminders/'+kind+'/preview',data={
        'owner':str(recipient.id),'message':service.MESSAGES[kind],**values})


@pytest.mark.parametrize('state,label',[
    ('stopped','Opted out'),('suspended','Account unavailable'),('unverified','Account unavailable'),
    ('no_trial','No trial'),('old','Outside reminder window'),('covered','Access already covered')])
def test_excluded_owners_display_disabled_without_becoming_candidates(client,records,approved,state,label):
    _,owner,shop,account=records
    if state=='stopped':db.session.add(CustomerReminderPreference(user_id=owner.id,stopped_at=NOW))
    if state=='suspended':owner.suspended_at=NOW
    if state=='unverified':owner.email_verified_at=None
    if state=='no_trial':account.trial_started_at=None
    if state=='old':account.trial_ends_at=NOW-timedelta(days=90)
    if state=='covered':account.legacy_granted_at=NOW
    db.session.commit();admin_login(client)
    response=client.get('/admin/reminders/trial')
    assert response.status_code==200
    html=response.get_data(as_text=True)
    checkbox=next(row for row in Inputs(html).rows if row['value']==str(owner.id))
    assert 'disabled' in checkbox and 'data-capped' not in checkbox and 'checked' not in checkbox
    assert f'<span class="recipient-reason">{label}</span>' in html
    assert owner.email not in html
    assert service.candidate(owner,'trial',NOW,override=True)[0] is None
    assert preview(client,owner,override='on',override_reason='Reviewed the account carefully').status_code==409


def test_employees_stay_out_of_recipient_rows(client,records,approved):
    owner=records[1];owner.staff_only=True;db.session.commit();admin_login(client)
    html=client.get('/admin/reminders/trial').get_data(as_text=True)
    assert not Inputs(html).rows and '1 employee excluded' in html


@pytest.mark.parametrize('kind',['trial','winback'])
def test_preview_uses_final_formatter_but_never_writes_claims_or_sends(client,records,approved,kind):
    if kind=='winback':subscription(records)
    admin_login(client);owner=records[1]
    baseline=(AdminReminderBatch.query.count(),AdminReminderDelivery.query.count(),AuditLog.query.count(),CustomerReminderPreference.query.count())
    writes=[]
    def capture(conn,cursor,statement,*args):
        if statement.lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE'):writes.append(statement)
    event.listen(db.engine,'before_cursor_execute',capture)
    try:
        response=preview(client,owner,kind,**({'feedback':'on'} if kind=='winback' else {}))
    finally:event.remove(db.engine,'before_cursor_execute',capture)
    assert response.status_code==200
    result=response.json;body=result['body']
    assert response.cache_control.private and response.cache_control.no_store
    assert response.headers['Referrer-Policy']=='same-origin'
    assert result['name']==owner.full_name and '{first_name}' not in body
    assert service.ORIGIN+'/plans/' in body and 'Stop reminders: '+service.ORIGIN in body
    assert ('Optional: why did you stop?' in body)==(kind=='winback')
    assert owner.email not in body and 'inactive' in result['notice']
    assert not writes and not approved
    assert baseline==(AdminReminderBatch.query.count(),AdminReminderDelivery.query.count(),AuditLog.query.count(),CustomerReminderPreference.query.count())
    for link in re.findall(r'https://\S+/admin/reminders/(?:stop|feedback)/\S+',body):
        assert client.get(link.replace(service.ORIGIN,'')).status_code==404


def test_live_footer_remains_identical_to_previous_delivery_composition(client,records,monkeypatch):
    owner=records[1]
    monkeypatch.setattr(service,'token',lambda user,purpose,delivery_id=None: f'{purpose}:{delivery_id}')
    assert service.email_body('Hello',owner)=='Hello\n\nStop reminders: '+service.ORIGIN+'/admin/reminders/stop/stop:None'
    assert service.email_body('Hello',owner,include_feedback=True,delivery_id=123)==(
        'Hello\n\nStop reminders: '+service.ORIGIN+'/admin/reminders/stop/stop:None\nOptional: why did you stop? '+service.ORIGIN+'/admin/reminders/feedback/feedback:123')


def test_preview_respects_cap_and_does_not_consume_override(client,records,approved):
    owner=records[1];preference=CustomerReminderPreference(user_id=owner.id,last_contact_at=NOW)
    db.session.add(preference);db.session.commit();previous=preference.last_contact_at;admin_login(client)
    assert preview(client,owner).status_code==409
    assert preview(client,owner,override='on',override_reason='short').status_code==409
    assert preview(client,owner,override='on',override_reason='Confirming the requested plan change').status_code==200
    db.session.refresh(preference)
    assert preference.last_contact_at==previous and AdminReminderBatch.query.count()==0
    assert service.candidate(owner,'trial',NOW)[1]=='recent_contact'


@pytest.mark.parametrize('values',[{'message':'{password}'},{'owner':'not-a-number'},{'owner':'9'*100},{'owner':'-1'},{'feedback':'on'},{'override':'true'},{'message':' '},{'message':'x'*5001}])
def test_preview_validates_form_without_creating_delivery(client,records,values):
    admin_login(client)
    assert preview(client,records[1],**values).status_code in (400,409)
    assert AdminReminderBatch.query.count()==AdminReminderDelivery.query.count()==0


def test_preview_requires_admin_session_and_csrf(client,records):
    assert preview(client,records[1]).status_code==302
    admin_login(client);client.application.config['WTF_CSRF_ENABLED']=True
    assert preview(client,records[1]).status_code==400


def test_preview_rechecks_revoked_admin_session(client,records):
    admin_login(client);records[0].admin_auth_version+=1;db.session.commit()
    assert preview(client,records[1]).status_code==302


def test_recipient_pages_include_exclusions_without_duplicates_or_hidden_employees(client,records,approved):
    for i in range(27):db.session.add(User(full_name=f'Excluded {i}',email=f'excluded{i}@example.com',password_hash='unused',role='user',staff_only=False))
    db.session.commit();admin_login(client)
    first=client.get('/admin/reminders/trial').get_data(as_text=True)
    second=client.get('/admin/reminders/trial?page=2').get_data(as_text=True)
    a,b=Inputs(first).rows,Inputs(second).rows
    assert len(a)==25 and len(b)==3
    assert len({row['value'] for row in a+b})==28
    assert a[0]['value']==str(records[1].id) and 'checked' in a[0]
    assert all('disabled' in row for row in a[1:]+b)
    assert 'disabled' not in Inputs(client.get('/admin/reminders/trial?owner='+str(records[1].id)).get_data(as_text=True)).rows[0]


def test_huge_page_number_returns_empty_without_database_overflow(client,records):
    admin_login(client)
    response=client.get('/admin/reminders/trial?page='+('9'*100))
    assert response.status_code==200 and not Inputs(response.get_data(as_text=True)).rows


def test_preview_accepts_valid_csrf_without_sending(client,records,approved):
    admin_login(client);client.application.config['WTF_CSRF_ENABLED']=True
    html=client.get('/admin/reminders/trial').get_data(as_text=True)
    csrf=re.search(r'name="csrf_token" value="([^"]+)"',html).group(1)
    assert preview(client,records[1],csrf_token=csrf).status_code==200
    assert not approved and AdminReminderDelivery.query.count()==0


def test_winback_preview_stops_immediately_when_renewal_resumes(client,records,approved):
    sub=subscription(records);admin_login(client)
    assert preview(client,records[1],'winback',feedback='on').status_code==200
    sub.cancel_at_period_end=False;sub.cancelled_at=None;db.session.commit()
    assert preview(client,records[1],'winback',override='on',override_reason='Reviewed the billing change').status_code==409
    html=client.get('/admin/reminders/winback').get_data(as_text=True)
    assert '<span class="recipient-reason">Renewal is on</span>' in html
    assert 'disabled' in Inputs(html).rows[0] and not approved
