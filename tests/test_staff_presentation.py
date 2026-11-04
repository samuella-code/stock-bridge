"""Synthetic template checks only: no Flask, ORM, application or database imports."""
import ast
import hashlib
from datetime import datetime
from html.parser import HTMLParser
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
STATES = ('manage','empty','roles-full','delivery-sent','delivery-disabled','delivery-failed',
          'delivery-unknown','delivery-configuration_required','delivery-suppressed',
          'invite','new-credentials','existing-identity','home','revoked-home','verify-email','restocking-only','error')


def environment(template_root=None):
    env=Environment(loader=FileSystemLoader(template_root or ROOT/'app/templates'),
        autoescape=select_autoescape(['html']),undefined=StrictUndefined)
    links={'staff.home':'staff-home.html','staff.manage':'staff-manage.html',
           'staff.invite_page':'staff-invite.html','auth.verification_pending':'staff-verify-email.html'}
    env.globals.update(url_for=lambda endpoint,**kw:'assets/'+kw['filename'] if endpoint=='static' else links.get(endpoint,'#preview-only'),
                       csrf_token=lambda:'synthetic-inert')
    return env


def context(state):
    # Read literal catalogue/templates without importing the application.
    tree=ast.parse((ROOT/'app/staff/roles.py').read_text())
    constants={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body
        if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('CATALOGUE','TEMPLATES')}
    roles={key:dict(name=name,permissions=list(perms)) for key,(name,perms) in constants['TEMPLATES'].items()}
    roles['a'*32]=dict(name='Restocking only',permissions=['inventory_view','restocking_create','restocking_view'])
    shops=[NS(id=101,name='Synthetic First Shop'),NS(id=102,name='Synthetic Corner Shop')]
    member=NS(id=1,owner_id=1,user_id=201,role='manager',state='active',duty_label='Evening stock checks',access_until=datetime(2026,11,27,12))
    old=NS(id=2,owner_id=1,user_id=202,role='inventory_staff',state='revoked',duty_label='Former staff',access_until=datetime(2026,11,27,12))
    invitation=NS(id=3,email='invitee@example.invalid',role='salesperson',state='pending',delivery_status='sent',expires_at=datetime(2026,11,1,12))
    data=dict(current_user=NS(is_authenticated=True,staff_only=False),roles=roles,role_count=len(roles),catalogue=constants['CATALOGUE'],seats=2,
        choices=shops,memberships=[member,old],invitations=[invitation],names={201:'Synthetic Manager',202:'Synthetic Former Employee'},assignments={1:{101,102},2:{101}},
        role_names={(1,k):r['name'] for k,r in roles.items()},role_permissions={(1,k):r['permissions'] for k,r in roles.items()})
    template='staff/manage.html'
    if state=='empty':data.update(memberships=[],invitations=[],seats=0,choices=[])
    elif state=='roles-full':data['roles']['b'*32]=dict(name='Weekend desk',permissions=['sales_view']);data['role_count']=5
    elif state.startswith('delivery-'):data['delivery_result']=state.removeprefix('delivery-')
    elif state in ('invite','new-credentials','existing-identity'):
        template='staff/invite.html';data['current_user']=NS(is_authenticated=False,staff_only=False)
        if state!='invite':data.update(invitation=invitation,token='A'*43,existing=state=='existing-identity')
    elif state in ('home','revoked-home'):
        template='staff/home.html';data.update(current_user=NS(is_authenticated=True,staff_only=True),rows=[(member,shops)] if state=='home' else [])
    elif state=='verify-email':template='staff/verify_pending.html';data['current_user']=NS(is_authenticated=True,staff_only=True,email='invitee@example.invalid')
    elif state=='restocking-only':member.role='a'*32
    elif state=='error':data['error']='Synthetic change rejected. Check the role, businesses and available seats.'
    return template,data


class FormContract(HTMLParser):
    """Compare names/values/validation/selection/targets, ignoring visible wording."""
    def __init__(self):super().__init__();self.forms=[];self.current=None
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='form':self.current=[];self.forms.append([values,self.current])
        elif self.current is not None and tag in ('input','select','option','button'):
            self.current.append([tag,{k:v for k,v in values.items() if k in ('name','value','type','maxlength','minlength','required','disabled','checked','selected','autocomplete','data-review-invite')}])
    def handle_endtag(self,tag):
        if tag=='form':self.current=None


def contract(html):
    parser=FormContract();parser.feed(html);return parser.forms


@pytest.mark.parametrize('state',STATES)
def test_wording_changes_preserve_reviewed_form_contract(state):
    template,data=context(state);html=environment().get_template(template).render(**data)
    expected=json.loads((ROOT/'tests/fixtures/staff_ui_form_contract.json').read_text())
    assert expected['baseline_commit']=='6a4593c61c852440dfb1fdc2cbf7c7b6b87eba62'
    assert hashlib.sha256(json.dumps(contract(html),sort_keys=True).encode()).hexdigest()==expected['states'][state]
    assert 'original coverage anchor' not in html and 'grant for the current' not in html
    assert 'synthetic-inert' in html  # CSRF fields retained.


def test_separate_limits_shared_impact_and_individual_assignments():
    template,data=context('manage');html=environment().get_template(template).render(**data)
    for text in ('2 of 5 employee seats occupied','4 of 5 active roles','Your owner account counts toward neither limit',
        'Roles do not use employee seats','Editing a role changes it for every employee and pending invitation',
        'affects that employee only','Restore access','Removed employees need a new invitation','Staff setup only'):
        assert text in html
    assert 'Archive this role' in html and 'No private link is shown here' not in html


@pytest.mark.parametrize('state,label',[('delivery-disabled','Email delivery is off'),('delivery-unknown','Email delivery is uncertain'),
    ('delivery-configuration_required','Email setup needs attention'),('delivery-failed','Email could not be sent'),('delivery-sent','Email sent'),('delivery-suppressed','Email not sent')])
def test_email_statuses_use_plain_labels_without_private_links(state,label):
    template,data=context(state);html=environment().get_template(template).render(**data)
    assert '<h2>'+label+'</h2>' in html and 'No private link is shown here' in html
    assert '#token=' not in html and 'A'*43 not in html
    assert 'Acceptance transfers that occupied seat to the employee; it does not free it.' in html
    assert 'until it expires, is accepted or is cancelled' not in html


def test_copy_retains_owner_only_permissions_and_disabled_workflows():
    template,data=context('manage');html=environment().get_template(template).render(**data)
    assert 'Saving permissions does not enable these workflows' in html
    assert 'Financial visibility starts off' in html
    assert 'Billing, subscriptions, voids and manual stock adjustments stay owner-only' in html
    assert 'Only you can grant team-administration or export authority' in html
    assert 'Leave product creation, product editing, sales, expenses and financial reports unchecked' in html
    for label in data['catalogue'].values():assert label in html
    css=(ROOT/'app/static/css/staff.css').read_text()
    assert 'minmax(0,1fr)' in css and '@media(max-width:480px)' in css
    assert 'overflow:hidden' not in css
