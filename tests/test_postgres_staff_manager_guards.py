"""Disconnected Manager harness/selection tests; never import the application."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import ast
import pytest
from tests.support import postgres_staff_guard as guard
from tests.support import postgres_staff_manager as manager
from tests.support import postgres_staff_races as races
from tests.test_postgres_staff_guards import approval,harness


@pytest.mark.parametrize('mode',['staff','manager'])
@pytest.mark.parametrize('field,value',[('schema',1),('staff_integration_commit','0'*40),('scenario_revision','staff-only-v1'),('source_commit',guard.INTEGRATION),('files',{}),('target',{}),('schema_before','unknown')])
def test_old_source_approval_and_missing_manager_review_fail_closed(tmp_path,mode,field,value):
    auth,files=approval(tmp_path,mode);auth[field]=value
    with pytest.raises(guard.Rejected):guard.authorize(auth,mode,tmp_path,files,source_commit=auth['source_commit'])


def test_manager_stage_requires_exact_case_list_and_starting_snapshot(tmp_path):
    auth,files=approval(tmp_path,'manager')
    guard.authorize(auth,'manager',tmp_path,files,source_commit='b'*40)
    for field,value in [('cases',list(guard.CASES)),('cases',list(reversed(guard.MANAGER_CASES))),('starting_state_sha256',''),('starting_state_sha256',True),('schema_after',guard.V17)]:
        bad=deepcopy(auth);bad[field]=value
        with pytest.raises(guard.Rejected):guard.authorize(bad,'manager',tmp_path,files,source_commit='b'*40)


@pytest.mark.parametrize('path,valid',[('/staff/team/9',True),('/staff/team/8',False),('/staff/team/09',False),('/staff/team/9?owner=8',False),('/staff/team/9/',False),('/staff/team/9/export',False),('/staff/manage',True),('/staff/invite',True)])
def test_owner_bound_route_allowlist(path,valid):assert races.staff_path(path,9) is valid


@pytest.mark.parametrize('rollback',[False,True])
def test_manager_paths_keep_observed_overlap_and_rollback_barrier(monkeypatch,rollback):
    engine,clients,observer,cleanup=harness(monkeypatch,rollback=rollback);inspected=[];proof={}
    result=races.concurrent_posts(engine,clients,['/staff/team/9']*2,[{},{}],observer,owner_id=9,rollback=rollback,baseline_check=lambda:inspected.append('after-rollback-before-commit'),timeout=1,evidence=proof)
    assert result==([400,302] if rollback else [302,302])
    assert inspected==(['after-rollback-before-commit'] if rollback else [])
    assert len(set(proof['workers']+[proof['observer']]))==3 and not engine.hooks


@pytest.mark.parametrize('case',guard.MANAGER_CASES)
def test_default_skip_before_fixtures_even_for_manager_cases(case):
    with pytest.raises(pytest.skip.Exception):guard.authorized_case(case,0,{})
    with pytest.raises(guard.Rejected):guard.authorized_case(case,1,{'STOCKBRIDGE_STAFF_PG_ACK':guard.ACK})


def test_foundation_and_manager_modules_have_guard_as_first_operation():
    for name,count in [('test_postgres_staff_foundation.py',18),('test_postgres_staff_manager.py',16)]:
        tree=ast.parse((Path(__file__).parent/name).read_text());fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef))
        assert fn.body[0].value.func.id=='authorized_case' and isinstance(fn.body[1],ast.ImportFrom)
    assert len(guard.CASES)==18 and len(guard.MANAGER_CASES)==16
    assert not set(guard.CASES)&set(guard.MANAGER_CASES)
    assert guard.MODES['staff'][2]==guard.CASES[2:]
    assert guard.MODES['manager'][2]==guard.MANAGER_CASES


@pytest.mark.parametrize('case',guard.MANAGER_CASES)
def test_manager_dispatch_preflight_and_no_fixture_when_first_gate_rejects(monkeypatch,case):
    calls=[];rt=NS(guards=NS(check_clean=lambda:calls.append('clean')),check_source=lambda:calls.append('source'),auth={})
    class Environment:
        def __enter__(self):return self
        def __exit__(self,*args):return False
    rt.environment=Environment
    rt.preflight=lambda phase:calls.append(phase)
    monkeypatch.setattr(manager,'check_case',lambda *a:calls.append('check'))
    monkeypatch.setattr(manager,'race_case',lambda *a:calls.append('race'))
    manager.execute_case(rt,case)
    assert calls==['clean','source',guard.V18,'check' if case.startswith('G') else 'race','clean',guard.V18]
    calls.clear()
    def reject(phase):raise guard.Rejected('schema-rejected')
    rt.preflight=reject
    with pytest.raises(guard.Rejected):manager.execute_case(rt,case)
    assert calls==['clean','source']


def data():return dict(owner=1,users=[2,3,4],members=[20,30,40],subscription=77,shops=[10,11],core_permissions=['sales_view'])


@pytest.mark.parametrize('case,actors,statuses',[
    ('C01',[1,2],(302,403)),('C02',[2,1],(302,302)),('C03',[2,3],(200,400)),('C04',[2,3],(302,400)),
    ('C05',[1,2],(302,400)),('C06',[1,2],(302,400)),('B01',[2,3],(400,200)),('B02',[2,3],(400,302)),('B03',[1,3],(400,200)),('B04',[2,1],(400,302))])
def test_fixed_race_actor_order_and_final_http_statuses(case,actors,statuses):
    actual,actions=manager.race_plan(case,data());assert actual==actors
    assert manager.RACES[case]==statuses and len(actions)==2
    if case in ('C01','C02','B03'):
        removal=actions[actual.index(1)]
        assert removal['authority_permissions']==['records_export'] and removal['role_key']=='manager'


def test_actual_actor_or_extra_audit_is_not_accepted():
    before={'audit_rows':[(1,1,'OLD',None)]}
    after={'audit_rows':[(1,1,'OLD',None),(2,2,'STAFF_INVITED',7)]}
    manager.expect_audits(before,after,{(2,'STAFF_INVITED'):1})
    with pytest.raises(guard.Rejected):manager.expect_audits(before,after,{(1,'STAFF_INVITED'):1})
    after['audit_rows'].append((3,2,'UNEXPECTED',7))
    with pytest.raises(guard.Rejected):manager.expect_audits(before,after,{(2,'STAFF_INVITED'):1})


def test_seat_or_role_overflow_and_stale_change_fail_independent_oracles():
    base={'audit_rows':[],'staff_role':[],'staff_invitation':[],'staff_membership':[],'seats':4,**{key:[] for key in ('users','identity_business','identity_account_billing','identity_billing_event')},'memberships_assigned':[]}
    from tests.support.postgres_staff_fixtures import T
    from datetime import timedelta
    invitation=dict(id=99,state='pending',role='salesperson',delivery_status='disabled',owner_id=1,grant_event_id=0,grant_started_at=T,grant_pro_start=T-timedelta(days=1),expires_at=T+timedelta(days=2),grant_subscription_id=77,created_at=T,membership_id=None,delivery_attempted_at=None,email='c03-first@example.invalid',token_digest='a'*64);good=dict(base,staff_invitation=[invitation],seats=5,audit_rows=[(1,2,'STAFF_INVITED',99),(2,2,'STAFF_DELIVERY_RESULT',99)])
    manager.assert_race('C03',base,good,data())
    with pytest.raises(guard.Rejected):manager.assert_race('C03',base,dict(good,seats=6),data())
    roles=[dict(key=str(n),archived=False) for n in range(4)];base=dict(base,staff_role=roles)
    good=dict(base,staff_role=roles+[dict(key='5',archived=False,owner_id=1,name='C04 first',permissions=['sales_view'])],audit_rows=[(1,2,'STAFF_ROLE_CREATED',None)])
    manager.assert_race('C04',base,good,data())
    with pytest.raises(guard.Rejected):manager.assert_race('C04',base,dict(good,staff_role=good['staff_role']+[dict(key='6',archived=False)]),data())


def test_bounded_helper_budgets_keep_stages_separate():
    # Each case execute_case has one pre + one post check; entry/exit add two.
    foundation=2+2*len(guard.MODES['staff'][2]);delegated=2+2*len(guard.MODES['manager'][2])
    assert (foundation,delegated)==(34,34) and foundation*2==68 and delegated*2==68
    assert foundation<=64 and delegated<=64
    assert foundation+delegated>64  # Combining stages violates existing helper cap.
