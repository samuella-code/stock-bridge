"""Synthetic template checks only: no Flask, ORM, application or database imports."""
import ast
from datetime import datetime
from html.parser import HTMLParser
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from jinja2 import Environment, FileSystemLoader, DictLoader, ChoiceLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
STATES = ('manage','empty','roles-full','delivery-sent','delivery-disabled','delivery-failed',
          'delivery-unknown','delivery-configuration_required','delivery-suppressed',
          'invite','new-credentials','existing-identity','home','revoked-home','verify-email','restocking-only','error')


def environment(template_root=None, overrides=None):
    # Isolate page forms from unrelated account menus and Help composer forms.
    shell="""<form method="POST" action="{{ url_for('auth.logout') }}"><input type="hidden" name="csrf_token" value="{{ csrf_token() }}"><button>Log out</button></form>{% block content %}{% endblock %}"""
    env=Environment(loader=ChoiceLoader([DictLoader({'base.html':shell,'staff/base.html':shell,**(overrides or {})}),FileSystemLoader(template_root or ROOT/'app/templates')]),
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
        role_names={(1,k):r['name'] for k,r in roles.items()},role_permissions={(1,k):r['permissions'] for k,r in roles.items()},owner_contact=None)
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
        if tag=='form':self.current=[];self.forms.append([{k:v for k,v in values.items() if k in ('method','action')},self.current])
        elif self.current is not None and tag in ('input','select','option','button'):
            # Unnamed controls (for example, the read-only copy-link display)
            # are not submitted and must not affect the security form contract.
            if tag == 'input' and not values.get('name'):
                return
            self.current.append([tag,{k:v for k,v in values.items() if k in ('name','value','type','maxlength','minlength','required','disabled','checked','selected','autocomplete','data-review-invite')}])
    def handle_endtag(self,tag):
        if tag=='form':self.current=None


def contract(html):
    parser=FormContract();parser.feed(html);return parser.forms


def submission_contract(forms):
    """Ignore layout order and equivalent select/radio or submit-button markup."""
    normalized=[]
    for attributes, fields in forms:
        result=[]; radios={}; i=0
        while i<len(fields):
            tag,a=fields[i]; i+=1
            if tag=='button' and not a.get('name'): continue
            if tag=='input' and a.get('type')=='radio':
                radios.setdefault(a['name'],[]).append(a); continue
            if tag=='select':
                options=[]
                while i<len(fields) and fields[i][0]=='option':
                    options.append(fields[i][1]); i+=1
                chosen=next((o.get('value','') for o in options if 'selected' in o), options[0].get('value','') if options else '')
                result.append(['choice',dict(a,options=sorted(o.get('value','') for o in options),chosen=chosen)])
            else: result.append([tag,a])
        for name, options in radios.items():
            chosen=next((o['value'] for o in options if 'checked' in o),options[0]['value'])
            result.append(['choice',dict(name=name,options=sorted(o['value'] for o in options),chosen=chosen)])
        normalized.append([attributes,sorted(result,key=lambda v:json.dumps(v,sort_keys=True))])
    return sorted(normalized,key=lambda v:json.dumps(v,sort_keys=True))


@pytest.mark.parametrize('state',STATES)
def test_form_contract_preserves_every_field_except_approved_manager_finance_default(state):
    template,data=context(state);html=environment().get_template(template).render(**data)
    expected=json.loads((ROOT/'tests/fixtures/staff_ui_form_contract.json').read_text())
    assert expected['baseline_commit']=='6a4593c61c852440dfb1fdc2cbf7c7b6b87eba62'
    forms=contract(html)
    # Compare against the reviewed original form contract. Layout order, CSS
    # classes and equivalent radio/select controls are presentation details.
    for attributes,fields in forms:
        if (any(values.get('name')=='role_key' and values.get('value')=='manager' for tag,values in fields)
                and any(values.get('name')=='action' and values.get('value')=='role_save' for tag,values in fields)):
            finance=[values for tag,values in fields if values.get('name')=='permissions' and values.get('value')=='financial_view']
            assert len(finance)==1 and 'checked' in finance[0]
            finance[0].pop('checked')
    original=subprocess.check_output(['git','show',expected['baseline_commit']+':app/templates/'+template],cwd=ROOT,text=True)
    baseline=contract(environment(overrides={template:original}).get_template(template).render(**data))
    for attributes,fields in baseline:
        if (any(v.get('name')=='role_key' and v.get('value')=='manager' for tag,v in fields)
                and any(v.get('name')=='action' and v.get('value')=='role_save' for tag,v in fields)):
            for tag,v in fields:
                if v.get('name')=='permissions' and v.get('value')=='financial_view':v.pop('checked',None)
    assert submission_contract(forms)==submission_contract(baseline)
    assert 'original coverage anchor' not in html and 'grant for the current' not in html
    assert 'synthetic-inert' in html  # CSRF fields retained.


def test_separate_limits_shared_impact_and_individual_assignments():
    template,data=context('manage');html=environment().get_template(template).render(**data)
    for text in ('Employee seats','2 of 5','Active roles','4 of 5','Your owner account counts toward neither limit',
        'Roles do not use employee seats','Editing a role changes it for every employee and pending invitation',
        'Edit assignments','Restore access','Removed employees need a new invitation','Staff setup only'):
        assert text in html
    assert 'Archive this role' in html and 'No private link is shown here' not in html


@pytest.mark.parametrize('state,label',[('delivery-disabled','Email delivery is off'),('delivery-unknown','Email delivery is uncertain'),
    ('delivery-configuration_required','Email setup needs attention'),('delivery-failed','Email could not be sent'),('delivery-sent','Email sent'),('delivery-suppressed','Email not sent')])
def test_manage_invitation_screen_does_not_claim_email_delivery_status(state,label):
    template,data=context(state);html=environment().get_template(template).render(**data)
    assert label not in html
    assert 'No private link is shown here' not in html
    assert '#token=' not in html and 'A'*43 not in html
    assert 'Copy invite link' in html
    assert 'The employee gets a link to accept. You can also copy the link and send it yourself.' in html
    assert 'An invitation holds a seat until it is accepted or expires.' in html


def test_copy_retains_owner_only_permissions_and_disabled_workflows():
    template,data=context('manage');html=environment().get_template(template).render(**data)
    assert 'Saving permissions does not enable these workflows' in html
    assert 'New Manager roles include financial-report access' in html
    assert 'Billing, subscriptions, voids and manual stock adjustments stay owner-only' in html
    assert 'Only you can grant team-administration or export authority' in html
    assert 'Leave product creation, product editing, sales, expenses and financial reports unchecked' in html
    for label in data['catalogue'].values():assert label in html
    css=(ROOT/'app/static/css/staff.css').read_text()
    assert 'minmax(0,1fr)' in css and '@media(max-width:480px)' in css
    assert 'overflow:hidden' not in css


@pytest.mark.parametrize('change', ['csrf','permission','required','target'])
def test_submission_contract_detects_security_relevant_changes(change):
    import copy
    forms=[[{'method':'POST','action':'/fixed'},[
        ['input',{'name':'csrf_token','value':'synthetic'}],
        ['input',{'name':'email','type':'email','required':None}],
        ['input',{'name':'permissions','value':'sales_create','type':'checkbox','checked':None}],
    ]]]
    changed=copy.deepcopy(forms)
    if change=='csrf':changed[0][1][0][1]['value']='different'
    elif change=='permission':changed[0][1][2][1]['value']='financial_view'
    elif change=='required':changed[0][1][1][1].pop('required')
    else:changed[0][0]['action']='/different'
    assert submission_contract(forms)!=submission_contract(changed)



def test_employee_home_uses_requested_empty_state_and_owner_contact():
    template,data=context('home')
    data.update(rows=[],owner_contact=NS(email='owner@example.invalid'))
    html=environment().get_template(template).render(**data)
    for phrase in ('No businesses assigned yet','Ask the business owner to check:',
        'Your business assignments','That their Pro plan is active',
        'That your access has been restored','Message the owner','Check again'):
        assert phrase in html
    assert 'mailto:owner%40example.invalid' in html


def test_employee_business_cards_show_role_and_permission_chips():
    template,data=context('home')
    data.update(sales_enabled=True,staff_stock_enabled=True,staff_operations_enabled=True)
    html=environment().get_template(template).render(**data)
    for phrase in ('Role: Manager','Record sales','View stock','Restock','Expenses','Open'):
        assert phrase in html
    assert '<span class="sb-staff-access-chip">' in html
