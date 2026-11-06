"""Disconnected state/namespace/scope/dispatch tests; no live authorization."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock
import json
import pytest
from tests.support import postgres_staff_i01_continuation as c
from tests.support import postgres_staff_fixtures as f
from tests.support.postgres_staff_guard import Rejected,V18
from tests.test_postgres_staff_guards import approval
from tests.support import postgres_staff_guard as policy

LABEL='i01-cont-00000001'


def rows():
    return {name:[] for name in ('user','business','account_billing','recurring_subscription',
        'staff_role','staff_membership','staff_membership_business','staff_invitation',
        'staff_invitation_business','audit_log','staff_audit_event','payment')}


def fixture_rows():
    data=rows();data['user']=[dict(id=1,email=LABEL+'-owner@example.invalid')]+[
        dict(id=n+2,email=f'{LABEL}-{n}@example.invalid') for n in range(4)]
    data['business']=[dict(id=10,user_id=1)]
    data['staff_membership']=[dict(id=n+20,owner_id=1,user_id=n+2) for n in range(4)]
    data['staff_membership_business']=[dict(membership_id=n+20,business_id=10) for n in range(4)]
    data['staff_invitation']=[dict(id=30,owner_id=1,email=LABEL+'-0-invite@example.invalid')]
    data['staff_invitation_business']=[dict(invitation_id=30,business_id=10)]
    data['audit_log']=[dict(id=40,actor_id=1)]
    data['staff_audit_event']=[dict(owner_id=1,audit_log_id=40)]
    return data


def runtime(before,after=None,mode='i01-continuation'):
    auth=dict(mode=mode,cases=['I01'] if mode=='i01-continuation' else [],continuation_namespace=LABEL,
        starting_state_sha256=c.digest(before))
    return SimpleNamespace(auth=auth,guards=SimpleNamespace(check_clean=MagicMock()),
        check_source=MagicMock(),preflight=MagicMock(),snapshot=MagicMock(side_effect=[before] if after is None else [before,after]))


def test_inventory_is_read_only_and_emits_only_digest_metadata(monkeypatch):
    monkeypatch.setattr(f,'race_case',MagicMock(side_effect=AssertionError('must not dispatch')))
    rt=runtime(rows(),mode='i01-state');result=c.inspect(rt)
    assert result==dict(starting_state_sha256=c.digest(rows()),continuation_namespace=LABEL,
        namespace_unused=True,schema=V18,rows=0)
    rt.preflight.assert_called_once_with(V18)
    f.race_case.assert_not_called()


def test_continuation_dispatches_exact_case_and_preserves_existing(monkeypatch):
    before=rows();before['user']=[dict(id=99,email='i01-owner@example.invalid',name='preserved')]
    after=fixture_rows();after['user']+=deepcopy(before['user'])
    race=MagicMock();monkeypatch.setattr(f,'race_case',race)
    rt=runtime(before,after);result=c.run(rt)
    race.assert_called_once_with(rt,'I01',namespace=LABEL)
    assert result['status']=='PASS' and result['existing_records_preserved'] is True
    assert result['end_state_sha256']==c.digest(after)
    assert [call.args for call in rt.preflight.call_args_list]==[(V18,),(V18,)]


def test_changed_state_fails_before_any_write(monkeypatch):
    race=MagicMock();monkeypatch.setattr(f,'race_case',race)
    rt=runtime(rows());rt.auth['starting_state_sha256']='0'*64
    with pytest.raises(Rejected,match='starting-state-differs'):c.run(rt)
    race.assert_not_called()


@pytest.mark.parametrize('mode',['i01-state','i01-continuation'])
def test_used_namespace_fails_before_dispatch(monkeypatch,mode):
    race=MagicMock();monkeypatch.setattr(f,'race_case',race)
    data=rows();data['user']=[dict(email=LABEL+'-owner@example.invalid')]
    rt=runtime(data,mode=mode)
    with pytest.raises(Rejected,match='namespace-already-used'):
        (c.inspect if mode=='i01-state' else c.run)(rt)
    race.assert_not_called()


@pytest.mark.parametrize('namespace',[None,'I01','i01','i01-cont-00000001-extra','i01-cont-../../secret',1,{}])
def test_bad_namespace_rejected_without_echo(namespace):
    with pytest.raises(Rejected,match='namespace-rejected'):c.namespace(dict(continuation_namespace=namespace))


@pytest.mark.parametrize('mutation',['changed','deleted','foreign_user','foreign_business','foreign_audit','foreign_table','foreign_assignment','changed_schema'])
def test_preservation_and_new_row_scope_reject_unrelated_changes(mutation):
    before=rows();before['payment']=[dict(id=99,status='success')]
    after=fixture_rows();after['payment']=deepcopy(before['payment'])
    if mutation=='changed':after['payment'][0]['status']='changed'
    if mutation=='deleted':after['payment']=[]
    if mutation=='foreign_user':after['user'][0]['email']='foreign@example.invalid'
    if mutation=='foreign_business':after['business'][0]['user_id']=99
    if mutation=='foreign_audit':after['audit_log'][0]['actor_id']=99
    if mutation=='foreign_table':after['payment'].append(dict(id=100,status='new'))
    if mutation=='foreign_assignment':after['staff_membership_business'][0]['business_id']=999
    if mutation=='changed_schema':after['extra']=[]
    with pytest.raises(Rejected):c.require_preserved(before,after,LABEL)


@pytest.mark.parametrize('mode',['i01-state','i01-continuation'])
def test_exact_authorization_selection_and_hashes_remain_required(tmp_path,mode):
    auth,files=approval(tmp_path,mode)
    assert policy.authorize(auth,mode,tmp_path,files,source_commit='b'*40) is auth
    for key,value in [('cases',['I02']),('continuation_namespace','I01'),('schema_before',policy.V17),('target',{}),('files',{})]:
        bad=deepcopy(auth);bad[key]=value
        with pytest.raises(Rejected):policy.authorize(bad,mode,tmp_path,files,source_commit='b'*40)
    if mode=='i01-continuation':
        auth.pop('starting_state_sha256')
        with pytest.raises(Rejected):policy.authorize(auth,mode,tmp_path,files,source_commit='b'*40)


def test_status_report_contains_only_validated_numeric_codes():
    error=Rejected('unexpected-staff-status:'+json.dumps(dict(case='I01',expected=[200,400],actual=[500,400])))
    assert c.status_diagnostic(error)==dict(case='I01',expected=[200,400],actual=[500,400])
    for error in [ValueError('password=secret'),RuntimeError('unexpected-staff-status:{}'),
        Rejected('unexpected-staff-status:'+json.dumps(dict(case='I01',expected=[200,400],actual=['secret',400]))),
        Rejected('unexpected-staff-status:'+json.dumps(dict(case='I02',expected=[200,400],actual=[200,400])))]:
        assert c.status_diagnostic(error) is None


def test_new_namespace_fixture_commits_five_distinct_users_and_four_seats(setup):
    # Reuse the synthetic session fixture; no actual StockBridge import.
    rt,session,models=setup
    result=f.seed_case(rt,'I01',namespace=LABEL)
    assert result['email']==LABEL+'-owner@example.invalid'
    users=[r for r in session.committed if type(r) is models.User]
    assert len(users)==5 and all(r.email.startswith(LABEL+'-') for r in users)
    assert len([r for r in session.committed if type(r) is models.StaffMembership])==4
    assert len([r for r in session.committed if type(r) is models.StaffMembershipBusiness])==4
    assert not session.pending


from tests.test_postgres_staff_seed_commit import setup


@pytest.mark.parametrize('seats,members,invitations',[(0,[],[]),(4,[],[]),(5,[{}]*4,[{}])])
def test_bad_starting_seat_count_stops_before_race(monkeypatch,setup,seats,members,invitations):
    from tests.support import postgres_staff_races
    dispatch=MagicMock();monkeypatch.setattr(postgres_staff_races,'concurrent_posts',dispatch)
    monkeypatch.setattr(f,'seed_case',lambda *a,**k:dict(owner=1,email='synthetic',shop=2))
    monkeypatch.setattr(f,'login',lambda *a:object())
    monkeypatch.setattr(f,'csrf',lambda *a:'synthetic')
    monkeypatch.setattr(f,'state',lambda *a:dict(seats=seats,staff_membership=members,staff_invitation=invitations))
    with pytest.raises(Rejected,match='starting-seats-invalid'):
        f.race_case(SimpleNamespace(app=lambda:None),'I01',namespace=LABEL)
    dispatch.assert_not_called()


def test_completed_status_failure_is_preserved_and_checked_without_retry(monkeypatch):
    before=rows();after=fixture_rows();rt=runtime(before,after)
    error=Rejected('unexpected-staff-status:'+json.dumps(dict(case='I01',expected=[200,400],actual=[200,200])))
    race=MagicMock(side_effect=error);monkeypatch.setattr(f,'race_case',race)
    with pytest.raises(Rejected) as failure:c.run(rt)
    assert failure.value is error and race.call_count==1
    assert rt.snapshot.call_count==2
    assert [call.args for call in rt.preflight.call_args_list]==[(V18,),(V18,)]


def test_unknown_race_failure_stops_without_postfailure_reads(monkeypatch):
    rt=runtime(rows());race=MagicMock(side_effect=RuntimeError('synthetic failure'))
    monkeypatch.setattr(f,'race_case',race)
    with pytest.raises(RuntimeError):c.run(rt)
    assert race.call_count==1 and rt.snapshot.call_count==1
