"""No database/application execution: pure policy and disconnected adapter tests."""
import ast
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('staff_delegation_policy_test',ROOT/'app/staff/delegation.py')
policy=importlib.util.module_from_spec(spec);sys.modules[spec.name]=policy;spec.loader.exec_module(policy)
NOW=datetime(2030,1,1)


def manager(**changes):
    values=dict(owner_id=1,actor_id=2,businesses=frozenset({10,11}),permissions=frozenset({'staff_admin','sales_view','sales_create','inventory_view'}),role='shared-manager')
    values.update(changes);return policy.Authority(**values)


def test_manager_assigns_only_nonprivileged_subset_and_cannot_restore_or_operate():
    manager().assignment(['sales_view'],{10},target_user=3)
    manager().shared_role('cashier',['sales_view'],['sales_view','sales_create'],[(3,{10}),(None,{11})])
    assert not policy.can_export()  # No new operational gate exists.


@pytest.mark.parametrize('permissions,businesses,target_user',[
    (['sales_view'],{99},3),(['sales_view'],{10,99},3),(['sales_view'],set(),3),
    (['sales_view'],{10},2),(['sales_view'],{10},1),(['financial_view'],{10},3),
    (['staff_admin'],{10},3),(['records_export'],{10},3),(['void_sales'],{10},3),
    (['manual_stock_adjust'],{10},3),(['billing_manage'],{10},3)])
def test_manager_assignment_rejects_escalation_self_owner_and_scope(permissions,businesses,target_user):
    with pytest.raises(policy.Denied):manager().assignment(permissions,businesses,target_user=target_user)


@pytest.mark.parametrize('changes',[{'permissions':frozenset()}, {'businesses':frozenset()}, {'actor_id':True}, {'owner_id':0}])
def test_missing_or_malformed_authority_fails(changes):
    with pytest.raises(policy.Denied):manager(**changes).assignment(['sales_view'],{10},target_user=3)


def test_self_invitation_identity_is_case_insensitive():
    with pytest.raises(policy.Denied):manager().assignment(['sales_view'],{10},target_email='MANAGER@example.invalid',actor_email='manager@example.invalid')


@pytest.mark.parametrize('key,old,new,references',[
    ('shared-manager',[],[],[]),('cashier',['staff_admin'],[],[]),('cashier',[],['records_export'],[]),
    ('cashier',['financial_view'],[],[]),('cashier',[],['financial_view'],[]),
    ('cashier',[],[],[(3,{10,99})]),('cashier',[],[],[(2,{10})]),
    ('cashier',[],[],[(1,{10})]),('cashier',[],[],[(None,{99})]),('cashier',[],[],[(3,set())])])
def test_shared_roles_cannot_change_self_privileged_or_outside_references(key,old,new,references):
    with pytest.raises(policy.Denied):manager().shared_role(key,old,new,references)


def test_only_owner_can_grant_authorities_even_if_manager_already_has_them():
    privileged=manager(permissions=frozenset({'staff_admin','records_export','sales_view'}))
    with pytest.raises(policy.Denied):privileged.assignment(['records_export'],{10},target_user=3)
    owner=manager(actor_id=1)
    owner.assignment(['staff_admin','records_export'],{10},target_user=2)
    owner.shared_role('shared-manager',[],['staff_admin','records_export'],[(2,{10})])


@pytest.mark.parametrize('dataset,view,fields,allowed',[
    ('sales','sales_view',['sale_id','quantity','unit_cost','revenue'],('sale_id','quantity')),
    ('stock','inventory_view',['product_id','stock_quantity','unit_cost'],('product_id','stock_quantity')),
    ('restocks','restocking_view',['receipt_id','quantity','total_cost'],('receipt_id','quantity')),
    ('expenses','expenses_view',['expense_id','description','amount'],('expense_id','description'))])
def test_exports_require_view_and_export_and_remove_foreign_businesses_and_financial_fields(dataset,view,fields,allowed):
    auth=manager(permissions=frozenset({'records_export',view}))
    assert policy.export_scope(auth,dataset,{10,99},fields)==(frozenset({10}),allowed)
    for perms in ({view},{'records_export'}):
        with pytest.raises(policy.Denied):policy.export_scope(manager(permissions=frozenset(perms)),dataset,{10},fields)


@pytest.mark.parametrize('dataset,businesses,fields',[
    ('unknown',{10},['password_hash']),('sales',{99},['sale_id']),('sales',{10},['password_hash']),
    ('sales',{10},['unit_cost']),('reports',{10},['revenue']),('sales',{True},['sale_id'])])
def test_export_projection_fail_closed(dataset,businesses,fields):
    with pytest.raises(policy.Denied):policy.export_scope(manager(permissions=frozenset({'records_export','sales_view'})),dataset,businesses,fields)


def test_financial_export_needs_financial_permission_and_view_and_remains_disabled():
    auth=manager(permissions=frozenset({'records_export','sales_view','financial_view'}))
    assert policy.export_scope(auth,'sales',{10},['sale_id','unit_cost'])==(frozenset({10}),('sale_id','unit_cost'))
    assert policy.can_export(auth,'sales') is False


class Query:
    def __init__(self,rows):self.rows=rows
    def filter_by(self,**kw):return Query([r for r in self.rows if all(getattr(r,k,None)==v for k,v in kw.items())])
    def first(self):return self.rows[0] if self.rows else None
    def all(self):return self.rows
    def __iter__(self):return iter(self.rows)
    def order_by(self,*args):return self


def function(path,name,scope):
    # Execute the real function body against explicitly disconnected collaborators.
    # Application/module imports remain blocked by the external runner.
    tree=ast.parse((ROOT/path).read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    node.decorator_list=[]
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(ROOT/path),'exec'),scope)
    return scope[name]


def fake_imports(modules):
    import builtins
    def imports(name,*args,**kwargs):
        if name in modules:return modules[name]
        raise AssertionError('Unexpected import in disconnected test: '+name)
    return dict(vars(builtins),__import__=imports)


def adapter_setup(*,permitted=True):
    owner=NS(id=1);user=NS(id=2,email='manager@example.invalid')
    member=NS(owner_id=1,user_id=2,state='active',role='shared-manager')
    role=NS(key='shared-manager',permissions=['staff_admin','sales_view'])
    service=NS(owner_access=Mock(return_value=(object(),[NS(id=10),NS(id=11)])),
        member_businesses=Mock(return_value=[NS(id=10)] if permitted else []))
    roles=NS(resolve=Mock(return_value=role),CATALOGUE={'sales_view':'View sales'})
    scope=dict(policy=policy,StaffMembership=NS(query=Query([member])),__builtins__=fake_imports({'app.staff':NS(service=service,roles=roles)}))
    auth=function('app/staff/delegation_access.py','authority',scope)
    return auth,owner,user,member,service,roles


def test_adapter_uses_current_member_businesses_and_owner_entitlements_at_one_instant():
    auth,owner,user,member,service,roles=adapter_setup()
    result,choices=auth(owner,user,now=NOW)
    assert result.businesses==frozenset({10}) and result.permissions==frozenset({'staff_admin','sales_view'})
    service.owner_access.assert_called_once_with(owner,now=NOW)
    service.member_businesses.assert_called_once_with(member,user,now=NOW)
    roles.resolve.assert_called_once_with(1,'shared-manager')


def test_adapter_rejects_absent_authoritative_member_access():
    auth,owner,user,member,service,roles=adapter_setup(permitted=False)
    # Actual authoritative service supplies []; do not recreate lifecycle rules.
    with pytest.raises(policy.Denied):auth(owner,user,now=NOW)
    assert not roles.resolve.called


def test_rechecking_after_lock_discards_old_authority():
    auth,owner,user,member,service,roles=adapter_setup()
    first,_=auth(owner,user,now=NOW);assert first.permissions==frozenset({'staff_admin','sales_view'})
    roles.resolve.return_value=NS(key='shared-manager',permissions=['sales_view'])
    second,_=auth(owner,user,now=NOW)
    with pytest.raises(policy.Denied):second.check('staff_admin')
    assert first is not second and service.member_businesses.call_count==2


def test_actual_role_and_delivery_audits_use_acting_user():
    for path,name,params,extra in [
        ('app/staff/roles.py','record',(1,'STAFF_ROLE_CHANGED',{},{}),{}),
        ('app/staff/delivery.py','audit',(NS(id=9,owner_id=1),'disabled'),{})]:
        logs=[];events=[]
        scope=dict(AuditLog=lambda **kw:logs.append(kw) or NS(id=77),StaffAuditEvent=lambda **kw:events.append(kw) or NS(),db=NS(session=NS(add=lambda x:None,flush=lambda:None)))
        function(path,name,scope)(*params,actor_id=2)
        assert logs[0]['actor_id']==2 and events[0]['owner_id']==1


def test_restoration_and_exports_are_denied_before_database_work():
    restore=function('app/staff/service.py','reconfirm',{})
    with pytest.raises(ValueError):restore(NS(id=1),9,now=NOW,actor=NS(id=2))
    export=function('app/staff/delegation_access.py','export_records',dict(policy=policy))
    with pytest.raises(policy.Denied):export()
    operate=function('app/staff/service.py','can_operate',{})
    assert operate() is False


def test_manager_finance_default_does_not_grant_team_admin_or_exports():
    tree=ast.parse((ROOT/'app/staff/roles.py').read_text())
    values={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('MAX_ACTIVE_ROLES','TEMPLATES')}
    assert values['MAX_ACTIVE_ROLES']==5
    assert 'financial_view' in values['TEMPLATES']['manager'][1]
    assert not (set(values['TEMPLATES']['manager'][1])&policy.OWNER_GRANTED)
    service=ast.parse((ROOT/'app/staff/service.py').read_text())
    assert next(ast.literal_eval(n.value) for n in service.body if isinstance(n,ast.Assign) and n.targets[0].id=='SEATS')==5


@pytest.mark.parametrize('case',['valid','wrong-user','revoked','suspended','unverified','admin','admin-enabled','archived-role','expired-or-gap','locked-or-downgraded','foreign-assignment'])
def test_authoritative_member_businesses_is_reused_without_relaxing_its_gates(case):
    user=NS(id=2,suspended_at=None,email_verified_at=NOW,role='user',admin_enabled=False)
    member=NS(user_id=2,state='active',owner_id=1,role='manager',id=7)
    if case=='wrong-user':member.user_id=9
    if case=='revoked':member.state='revoked'
    if case=='suspended':user.suspended_at=NOW
    if case=='unverified':user.email_verified_at=None
    if case=='admin':user.role='admin'
    if case=='admin-enabled':user.admin_enabled=True
    resolve=Mock(side_effect=ValueError() if case=='archived-role' else None)
    rows=[NS(membership_id=7,business_id=99 if case=='foreign-assignment' else 10)]
    scope=dict(role_service=NS(resolve=resolve),db=NS(session=NS(get=lambda *args:NS(id=1))),User=object,
        grant_matches=Mock(return_value=(case!='expired-or-gap',[] if case=='locked-or-downgraded' else [NS(id=10)])),
        StaffMembershipBusiness=NS(query=Query(rows)))
    result=function('app/staff/service.py','member_businesses',scope)(member,user,now=NOW)
    assert [b.id for b in result]==([10] if case=='valid' else [])


@pytest.mark.parametrize('allowed',[True,False])
def test_revoke_rechecks_under_owner_lock_before_mutating_and_audits_actor(allowed):
    trace=[];owner=NS(id=1);actor=NS(id=2);row=NS(id=7,owner_id=1,user_id=3,state='active',role='cashier')
    auth=manager()
    delegated=NS(mutation=lambda *args,**kw:(trace.append('authority') or (auth,[])),
        target=lambda *args:trace.append('target') if allowed else policy.require(False))
    model=NS(query=Query([row]));audit=Mock()
    scope=dict(datetime=datetime,lock_owner=lambda x:trace.append('lock'),owner_access=lambda *a,**kw:None,
        StaffMembership=model,StaffInvitation=model,audit=audit,__builtins__=fake_imports({'app.staff':NS(delegation_access=delegated)}))
    action=function('app/staff/service.py','revoke',scope)
    if allowed:
        action(owner,'membership',7,now=NOW,actor=actor)
        assert row.state=='revoked' and trace==['lock','authority','target']
        assert audit.call_args.args==(1,2,'STAFF_REVOKED')
    else:
        with pytest.raises(policy.Denied):action(owner,'membership',7,now=NOW,actor=actor)
        assert row.state=='active' and not audit.called and trace==['lock','authority']



def test_manager_context_hides_whole_records_and_roles_outside_scope():
    auth=manager(businesses=frozenset({10}))
    member_rows=[NS(id=3,owner_id=1,user_id=3,role='cashier',businesses={10}),NS(id=4,owner_id=1,user_id=4,role='cashier',businesses={10,99}),NS(id=5,owner_id=1,user_id=5,role='privileged',businesses={10})]
    invite_rows=[NS(id=6,owner_id=1,role='cashier',businesses={10}),NS(id=7,owner_id=1,role='cashier',businesses={99})]
    class Column:
        def desc(self):return self
    roles={'cashier':dict(name='Cashier',permissions=['sales_view']),
           'outside':dict(name='Shared elsewhere',permissions=['sales_view']),
           'privileged':dict(name='Privileged',permissions=['records_export']),
           'shared-manager':dict(name='Manager',permissions=['staff_admin','sales_view'])}
    def target(auth,row):auth.assignment(roles[row.role]['permissions'],row.businesses,target_user=getattr(row,'user_id',None))
    def shared(auth,key,old,new):auth.shared_role(key,old,new,[(9,{99})] if key=='outside' else [])
    adapter=NS(mutation=lambda *a,**k:(auth,[NS(id=10,name='Allowed shop')]),target=target,shared=shared,assignments=lambda row:row.businesses)
    scope=dict(StaffMembership=NS(query=Query(member_rows),id=Column()),StaffInvitation=NS(query=Query(invite_rows),id=Column()),User=object,
        role_service=NS(options=lambda id:roles,CATALOGUE={'sales_view':'View sales'}),service=NS(seat_count=lambda *a,**k:5),
        db=NS(session=NS(get=lambda model,id:NS(full_name='Allowed employee '+str(id)))),
        __builtins__=fake_imports({'app.staff':NS(delegation_access=adapter,delegation=policy)}))
    result=function('app/staff/routes.py','delegated_context',scope)(NS(id=1),NS(id=2),NOW)
    assert [m.id for m in result['memberships']]==[3] and [i.id for i in result['invitations']]==[6]
    assert set(result['roles'])=={'cashier'} and result['names']=={3:'Allowed employee 3'}
    assert [b.id for b in result['choices']]==[10]


@pytest.mark.parametrize('action',['invite','role_save','revoke','reconfirm'])
def test_forged_manager_authority_fields_fail_before_dispatch(action):
    class Form(dict):
        def getlist(self,key):return self.get(key,[])
    owner=NS(id=1);user=NS(id=2);commit=Mock();rollback=Mock();services=Mock()
    scope=dict(db=NS(session=NS(get=lambda *a:owner,commit=commit,rollback=rollback)),User=object,actor=lambda:user,datetime=datetime,
        delegated_context=lambda *a:{'safe':True},request=NS(method='POST',form=Form(action=action,authority_permissions=['staff_admin'])),
        role_service=services,service=services,IntegrityError=type('FakeIntegrityError',(Exception,),{}),
        abort=lambda code:(_ for _ in ()).throw(AssertionError(code)),render_template=lambda template,**ctx:ctx)
    result=function('app/staff/routes.py','team_manage',scope)(1)
    assert result[1]==400 and rollback.call_count==1 and not commit.called and not services.mock_calls


def test_manager_invite_dispatch_uses_actual_actor_and_does_not_render_token():
    class Form(dict):
        def getlist(self,key):return self.get(key,[])
    owner=NS(id=1);user=NS(id=2);invite=Mock(return_value=(NS(id=8),'synthetic-raw-link'));deliver=Mock(return_value='disabled')
    scope=dict(db=NS(session=NS(get=lambda *a:owner,commit=Mock(),rollback=Mock())),User=object,actor=lambda:user,datetime=datetime,
        delegated_context=lambda *a:{'safe':True},request=NS(method='POST',form=Form(action='invite',email='new@example.invalid',role='cashier',business_ids=['10'])),
        service=NS(invite=invite),delivery=NS(deliver=deliver),IntegrityError=type('FakeIntegrityError',(Exception,),{}),
        render_template=lambda template,**ctx:ctx)
    result=function('app/staff/routes.py','team_manage',scope)(1)
    assert invite.call_args.kwargs['actor'] is user and invite.call_args.args[0] is owner
    deliver.assert_called_once_with(8,'synthetic-raw-link',actor_id=2)
    assert result=={'safe':True,'delivery_result':'disabled'}


@pytest.mark.parametrize('owner',[True,False])
def test_new_template_shows_sensitive_grants_and_restoration_only_to_owner(owner):
    from tests.test_staff_presentation import environment,context
    _,data=context('manage');auth=manager(actor_id=1 if owner else 2)
    data.update(authority=auth,team_owner=NS(id=1,full_name='Synthetic Owner'),authority_catalogue=policy.CATALOGUE)
    if not owner:
        data['roles']={'salesperson':data['roles']['salesperson']};data['memberships']=data['memberships'][:1];data['choices']=data['choices'][:1]
    html=environment().get_template('staff/team_manage.html').render(**data)
    assert ('name="authority_permissions"' in html) is owner
    assert ('value="reconfirm"' in html) is owner
    assert 'Exports are disabled' in html
    assert 'Acceptance transfers that occupied seat to the employee; it does not free it.' in html
    if not owner:assert 'Synthetic Corner Shop' not in html
    assert 'action="export"' not in html


def test_first_owner_role_edit_initializes_templates_before_resolving_and_attributes_audit():
    trace=[];row=NS(key='manager',permissions=['sales_view'],name='Manager');owner=NS(id=1)
    def initialize(id):trace.append('initialize')
    def resolve(*args):
        assert 'initialize' in trace
        trace.append('resolve');return row
    delegated=NS(mutation=lambda *a,**k:(trace.append('authority') or (manager(actor_id=1),[])),shared=lambda *args:trace.append('policy'))
    scope=dict(datetime=datetime,resolve=resolve,_initialize_locked=initialize,validate_permissions=lambda x:sorted(x),
        db=NS(session=NS(flush=lambda:trace.append('flush'))),record=Mock(),facts=lambda r:dict(name=r.name,permissions=r.permissions),
        __builtins__=fake_imports({'app.staff.service':NS(lock_owner=lambda id:trace.append('lock'),owner_access=lambda *a,**kw:None),
            'app.staff':NS(delegation_access=delegated),'app.staff.delegation':policy}))
    result=function('app/staff/roles.py','save',scope)(owner,'manager','Updated Manager',['sales_view'],now=NOW,authority_permissions=['staff_admin'])
    assert result.permissions==['sales_view','staff_admin'] and trace[:3]==['lock','authority','initialize']
    assert scope['record'].call_args.kwargs['actor_id']==1


def test_shared_role_references_include_invited_actor_and_owner_identities():
    rows=[NS(owner_id=1,role='cashier',email='MANAGER@example.invalid',businesses={10}),
          NS(owner_id=1,role='cashier',email='owner@example.invalid',businesses={10}),
          NS(owner_id=1,role='cashier',email='new@example.invalid',businesses={10})]
    users={1:NS(email='owner@example.invalid'),2:NS(email='manager@example.invalid')}
    scope=dict(StaffMembership=NS(query=Query([])),StaffInvitation=NS(query=Query(rows)),
        assignments=lambda row:row.businesses,db=NS(session=NS(get=lambda model,id:users[id])),User=object,policy=policy)
    refs=function('app/staff/delegation_access.py','references',scope)(1,'cashier',actor_id=2)
    assert refs==[(2,{10}),(1,{10}),(None,{10})]
    with pytest.raises(policy.Denied):manager().shared_role('cashier',['sales_view'],['sales_view'],refs)


@pytest.mark.parametrize('actor_id',[1,2])
def test_target_adapter_rejects_foreign_account_before_role_lookup(actor_id):
    roles=NS(resolve=Mock())
    scope=dict(policy=policy,__builtins__=fake_imports({'app.staff':NS(roles=roles)}))
    target=function('app/staff/delegation_access.py','target',scope)
    with pytest.raises(policy.Denied):target(manager(actor_id=actor_id),NS(owner_id=99))
    assert not roles.resolve.called
