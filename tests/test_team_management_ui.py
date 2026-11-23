"""Isolated team presentation and one-time invitation-link checks; no live mail."""
from datetime import datetime, timedelta
from html.parser import HTMLParser
import hashlib
import json
import re
from unittest.mock import patch

from sqlalchemy import event
from app import db
from app.models import User, StaffMembershipBusiness, StaffInvitation, StaffAuditEvent, AuditLog
from app.staff import service, presentation, roles
from tests.test_staff_foundation import app, seed, issue, accept_new, login, NOW, staff_clock
from tests.test_business_insights import subscription


class Fields(HTMLParser):
    def __init__(self,html):super().__init__();self.inputs=[];self.ids=[];self.feed(html)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if 'id' in attrs:self.ids.append(attrs['id'])
        if tag=='input':self.inputs.append(attrs)


def test_team_chooser_and_header_use_current_authority_without_writes(app):
    owner,shops=seed('pro');client=app.test_client();login(client,owner)
    before=AuditLog.query.count();statements=[]
    def capture(conn,cursor,statement,*a):
        if statement.lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE'):statements.append(statement)
    event.listen(db.engine,'before_cursor_execute',capture)
    try:response=client.get('/staff/teams')
    finally:event.remove(db.engine,'before_cursor_execute',capture)
    html=response.get_data(as_text=True)
    assert response.status_code==200 and html.count('<h1>')==1
    for label in ('Choose a team to manage.','You are the owner','Seats 0 of 5','Roles 3 of 5','Pro active','Open team','Owner dashboard','My access'):assert label in html
    assert 'My owner dashboard' not in html and 'My staff management' not in html
    assert not statements and AuditLog.query.count()==before


def test_regular_employee_has_no_team_management_or_owner_header_links(app):
    owner,shops=seed('pro');_,raw=issue(owner,shops);person,_=accept_new(raw)
    client=app.test_client();login(client,person)
    html=client.get('/staff/').get_data(as_text=True)
    header=html.split('</header>',1)[0]
    assert 'My access' in header and 'Owner dashboard' not in header and '>Team management<' not in header


def test_fresh_link_copy_does_not_persist_secret_or_reveal_it_on_get(app):
    owner,shops=seed('pro');client=app.test_client();login(client,owner)
    with patch('app.email_service._send_email',side_effect=AssertionError('Live email forbidden')):
        response=client.post(f'/staff/team/{owner.id}',data={'action':'invite','email':'new@example.invalid','role':'salesperson','business_ids':str(shops[0].id)})
    html=response.get_data(as_text=True)
    assert response.status_code==200 and 'Copy invite link' in html
    raw=re.search(r'#token=([A-Za-z0-9_-]{43})',html).group(1)
    invitation=StaffInvitation.query.one()
    assert invitation.token_digest==hashlib.sha256(raw.encode()).hexdigest()
    assert response.cache_control.no_store and response.cache_control.private
    assert response.headers['Referrer-Policy']=='same-origin'
    for row in StaffAuditEvent.query.all():assert raw not in json.dumps(row.details)
    for column in invitation.__table__.columns:assert raw not in str(getattr(invitation,column.name))
    with client.session_transaction() as state:assert raw not in json.dumps(dict(state))
    assert raw not in client.get(f'/staff/team/{owner.id}').get_data(as_text=True)
    assert '#token=' not in client.get('/staff/teams').get_data(as_text=True)


def test_resending_exposes_only_new_link_without_another_seat(app):
    owner,shops=seed('pro');old,raw=issue(owner,shops);client=app.test_client();login(client,owner)
    with patch('app.email_service._send_email',side_effect=AssertionError('Live email forbidden')):
        response=client.post(f'/staff/team/{owner.id}',data={'action':'resend','id':str(old.id)})
    html=response.get_data(as_text=True);new_raw=re.search(r'#token=([A-Za-z0-9_-]{43})',html).group(1)
    assert raw not in html and new_raw!=raw and old.state=='revoked'
    assert service.seat_count(owner.id,now=NOW)==1
    assert service.invitation_for(new_raw,now=NOW).id!=old.id


def test_restoration_button_only_for_a_pro_continuity_pause(app,monkeypatch):
    owner,shops=seed('pro');_,raw=issue(owner,shops);person,member=accept_new(raw)
    client=app.test_client();login(client,owner)
    html=client.get(f'/staff/team/{owner.id}').get_data(as_text=True)
    assert '>Active</span>' in html and 'value="reconfirm"' not in html and person.email not in html
    assert not presentation.member_status(owner,member,person,shops,NOW)['paused_due_pro']
    original=subscription(owner);original.current_period_end=NOW+timedelta(seconds=1);member.access_until=original.current_period_end
    db.session.commit();original.current_period_end=NOW+timedelta(days=30);db.session.commit();later=NOW+timedelta(seconds=2)
    status=presentation.member_status(owner,member,person,shops,later)
    assert status['paused_due_pro'] and status['can_restore'] and status['label']=='Access paused'
    class Clock(datetime):
        @classmethod
        def utcnow(cls):return later
    monkeypatch.setattr('app.staff.routes.datetime',Clock)
    assert 'value="reconfirm"' in client.get(f'/staff/team/{owner.id}').get_data(as_text=True)
    service.reconfirm(owner,member.id,now=later);db.session.commit()
    assert presentation.member_status(owner,member,person,shops,later)['label']=='Active'
    assert 'value="reconfirm"' not in client.get(f'/staff/team/{owner.id}').get_data(as_text=True)
    person.suspended_at=NOW;db.session.commit()
    assert not presentation.member_status(owner,member,person,shops,later)['can_restore']


def test_accepted_invitations_show_accepted_time_not_expiry_and_no_duplicate_switch_ids(app):
    owner,shops=seed('pro');row,raw=issue(owner,shops);accept_new(raw)
    client=app.test_client();login(client,owner)
    html=client.get(f'/staff/team/{owner.id}').get_data(as_text=True)
    assert '<p>Accepted ' in html and '<p>Expires ' not in html
    assert owner.email not in html and row.email not in html
    assert 'Expired invitations do not use a seat.' in html
    fields=Fields(html)
    assert len(fields.ids)==len(set(fields.ids))
    switches=[field for field in fields.inputs if field.get('role')=='switch']
    assert switches and all(field.get('value') and field.get('aria-checked') in ('true','false') for field in switches)
    assert any(field.get('name')=='authority_permissions' for field in switches)


def test_copy_flow_cannot_bypass_team_scope_or_csrf(app):
    owner,shops=seed('pro');row,raw=issue(owner,shops);other,_=seed('pro',email='other@example.invalid')
    client=app.test_client();login(client,other)
    assert client.post(f'/staff/team/{owner.id}',data={'action':'resend','id':str(row.id)}).status_code==403
    login(client,owner);app.config['WTF_CSRF_ENABLED']=True
    assert client.post(f'/staff/team/{owner.id}',data={'action':'resend','id':str(row.id)}).status_code==400
    assert row.state=='pending' and raw not in client.get(f'/staff/team/{owner.id}').get_data(as_text=True)


def test_delegated_team_page_does_not_leak_other_businesses_or_owner_grants(app):
    owner,shops=seed('pro',businesses=2)
    manager_role=roles.save(owner,None,'Scoped team manager',['sales_view','sales_create','inventory_view'],now=NOW,authority_permissions=['staff_admin']);db.session.commit()
    _,raw=issue(owner,shops,email='manager@example.invalid',role=manager_role.key);manager,_=accept_new(raw);manager.full_name='Scope manager'
    _,raw=issue(owner,shops,email='allowed@example.invalid');allowed,_=accept_new(raw);allowed.full_name='Allowed person'
    _,raw=issue(owner,[shops[1]],email='private@example.invalid');hidden,_=accept_new(raw);hidden.full_name='Private person';db.session.commit()
    client=app.test_client();login(client,manager)
    html=client.get(f'/staff/team/{owner.id}').get_data(as_text=True)
    assert 'Allowed person' in html and 'Private person' not in html and shops[1].name not in html
    assert 'name="authority_permissions"' not in html and 'value="reconfirm"' not in html
    assert allowed.email not in html and hidden.email not in html
    choices=client.get('/staff/teams').get_data(as_text=True)
    assert 'You help manage this team' in choices and shops[1].name not in choices
    assert 'Owner dashboard' not in choices.split('</header>',1)[0]
    rejected=client.post(f'/staff/team/{owner.id}',data={'action':'invite','email':'forged@example.invalid','role':'salesperson','business_ids':str(shops[1].id)})
    assert rejected.status_code==400 and b'#token=' not in rejected.data


def test_paused_flag_is_not_a_restore_option_for_removed_staff(app):
    owner,shops=seed('pro');_,raw=issue(owner,shops);person,member=accept_new(raw)
    service.revoke(owner,'membership',member.id,now=NOW);db.session.commit()
    status=presentation.member_status(owner,member,person,shops,NOW)
    assert status['label']=='Access removed' and not status['paused_due_pro'] and not status['can_restore']
