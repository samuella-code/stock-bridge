"""Disconnected fixed-selection, preservation, namespace and fail-fast coverage."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest
from tests.support import postgres_staff_remaining as r
from tests.support import postgres_staff_i01_continuation as shared
from tests.support import postgres_staff_fixtures as f
from tests.support import postgres_staff_guard as g
from tests.test_postgres_staff_i01_continuation import rows
from tests.test_postgres_staff_seed_commit import setup
from tests.test_postgres_staff_guards import approval

BASE='staff-cont-00000001'


def added(case,index):
    data=rows();label=BASE+'-'+case.lower();owner=100*index+1
    occupied=4 if case in ('I02','F01') else 3 if case in ('A01','A02') else 0
    emails=[label+'-owner@example.invalid']+[f'{label}-{n}@example.invalid' for n in range(occupied)]
    if case not in ('F01','X01'):emails.append(label+'-employee@example.invalid')
    data['user']=[dict(id=owner+n,email=email) for n,email in enumerate(emails)]
    data['business']=[dict(id=owner+50,user_id=owner)]
    data['account_billing']=[dict(user_id=owner)]
    data['recurring_subscription']=[dict(id=owner+51,user_id=owner)]
    data['staff_role']=[dict(owner_id=owner,key='salesperson')]
    data['staff_membership']=[dict(id=owner+60+n,owner_id=owner,user_id=user['id']) for n,user in enumerate(data['user'][1:])]
    data['staff_membership_business']=[dict(membership_id=row['id'],business_id=owner+50) for row in data['staff_membership']]
    data['staff_invitation']=[dict(id=owner+70,owner_id=owner,email=label+('-0-invite@example.invalid' if case=='F01' else '-employee@example.invalid'))]
    data['staff_invitation_business']=[dict(invitation_id=owner+70,business_id=owner+50)]
    actor=owner if case in ('F01','X01') else data['user'][-1]['id']
    data['audit_log']=[dict(id=owner+80,actor_id=actor)]
    data['staff_audit_event']=[dict(owner_id=owner,audit_log_id=owner+80)]
    return data


def combine(before,addition):
    return {key:deepcopy(before[key])+deepcopy(addition[key]) for key in before}


def runtime():
    data=rows();data['user']=[dict(id=9999,email='i01-owner@example.invalid')]
    data['staff_invitation']=[dict(id=9999,owner_id=9999,email='preserved@example.invalid',state='pending')]
    rt=SimpleNamespace(auth=dict(mode='staff-remaining',cases=list(g.REMAINING_CASES),
        continuation_namespace=BASE,starting_state_sha256=shared.digest(data)),
        guards=SimpleNamespace(check_clean=MagicMock()),check_source=MagicMock(),preflight=MagicMock())
    rt.data=data;rt.snapshot=lambda:deepcopy(rt.data)
    return rt


def test_exact_remaining_list_excludes_every_completed_case():
    assert g.REMAINING_CASES==('I02','A01','A02','X01','X02','D01','D02','D03','F01','F02','F03','F04')
    assert not set(g.REMAINING_CASES)&{'M00','M01','S01','R01','R02','I01'}


def test_fixed_batch_reuses_real_assertion_dispatch_and_preserves_all_prior_rows(monkeypatch):
    rt=runtime();calls=[];reports=[]
    def race(runtime,case,*,namespace):
        calls.append((case,namespace));runtime.data=combine(runtime.data,added(case,len(calls)))
    monkeypatch.setattr(f,'race_case',race)
    result=r.run(rt,lambda kind,data:reports.append((kind,data)))
    assert [case for case,label in calls]==list(g.REMAINING_CASES)
    assert [label for case,label in calls]==[BASE+'-'+case.lower() for case in g.REMAINING_CASES]
    assert result['status']=='PASS' and result['cases']==list(g.REMAINING_CASES)
    assert result['end_state_sha256']==shared.digest(rt.data)
    assert rt.preflight.call_count==24 and all(call.args==(g.V18,) for call in rt.preflight.call_args_list)
    assert [data['case'] for kind,data in reports if kind=='CASE']==list(g.REMAINING_CASES)
    assert rt.data['user'][0]['id']==9999 and rt.data['staff_invitation'][0]['state']=='pending'


@pytest.mark.parametrize('index',[0,1,5,11])
def test_any_case_failure_stops_without_retry_or_later_case(monkeypatch,index):
    rt=runtime();calls=[];reports=[]
    def race(runtime,case,*,namespace):
        calls.append(case)
        if case==g.REMAINING_CASES[index]:raise RuntimeError('synthetic')
        runtime.data=combine(runtime.data,added(case,len(calls)))
    monkeypatch.setattr(f,'race_case',race)
    with pytest.raises(RuntimeError):r.run(rt,lambda kind,data:reports.append((kind,data)))
    assert calls==list(g.REMAINING_CASES[:index+1])
    assert len([data for kind,data in reports if kind=='CASE'])==index


def test_hash_drift_and_used_base_fail_before_writes(monkeypatch):
    race=MagicMock();monkeypatch.setattr(f,'race_case',race)
    rt=runtime();rt.auth['starting_state_sha256']='0'*64
    with pytest.raises(g.Rejected,match='starting-state-differs'):r.run(rt,MagicMock())
    rt=runtime();rt.data['user'].append(dict(email=BASE+'-f04-owner@example.invalid'))
    rt.auth['starting_state_sha256']=shared.digest(rt.data)
    with pytest.raises(g.Rejected,match='namespace-already-used'):r.run(rt,MagicMock())
    race.assert_not_called()


def test_drift_between_cases_fails_before_second_seed(monkeypatch):
    rt=runtime();calls=[]
    def race(runtime,case,*,namespace):
        calls.append(case);runtime.data=combine(runtime.data,added(case,1))
    monkeypatch.setattr(f,'race_case',race)
    source=rt.snapshot;count=0
    def snapshot():
        nonlocal count
        count+=1
        result=source()
        if count==3:result['user'][0]['email']='changed@example.invalid'
        return result
    rt.snapshot=snapshot
    with pytest.raises(g.Rejected,match='between-case-state-differs'):r.run(rt,MagicMock())
    assert calls==['I02']


@pytest.mark.parametrize('case',g.REMAINING_CASES)
def test_each_case_has_exact_new_user_scope_and_acceptance_audit_actor(case):
    before=rows();after=added(case,1)
    shared.require_preserved(before,after,BASE+'-'+case.lower(),case=case)
    after['user'].append(dict(id=5000,email='foreign@example.invalid'))
    with pytest.raises(g.Rejected):shared.require_preserved(before,after,BASE+'-'+case.lower(),case=case)


@pytest.mark.parametrize('case',g.REMAINING_CASES)
def test_seed_uses_fresh_namespace_and_commits_all_initial_memberships(setup,case):
    rt,session,models=setup;label=BASE+'-'+case.lower()
    result=f.seed_case(rt,case,namespace=label)
    users=[row for row in session.committed if type(row) is models.User]
    assert all(row.email.startswith(label+'-') for row in users)
    assert result['email']==label+'-owner@example.invalid'
    assert len([row for row in session.committed if type(row) is models.StaffMembership])==result['occupied']
    assert not session.pending


@pytest.mark.parametrize('mode',['staff-remaining-state','staff-remaining'])
def test_new_modes_bind_exact_selection_namespace_state_and_hashes(tmp_path,mode):
    auth,files=approval(tmp_path,mode)
    assert g.authorize(auth,mode,tmp_path,files,source_commit='b'*40) is auth
    for key,value in [('cases',['I01']),('continuation_namespace','i01-cont-00000001'),('files',{}),('target',{})]:
        bad=deepcopy(auth);bad[key]=value
        with pytest.raises(g.Rejected):g.authorize(bad,mode,tmp_path,files,source_commit='b'*40)


def test_state_inventory_has_no_case_dispatch(monkeypatch):
    rt=runtime();rt.auth.update(mode='staff-remaining-state',cases=[])
    race=MagicMock();monkeypatch.setattr(f,'race_case',race)
    result=r.inspect(rt)
    assert result['namespace_unused'] is True and result['starting_state_sha256']==shared.digest(rt.data)
    assert result['cases']==list(g.REMAINING_CASES)
    race.assert_not_called()


def test_expected_http_statuses_are_byte_equivalent_in_value_to_reviewed_parent():
    # Assert the unchanged protocol explicitly, independent of its new shared location.
    assert f.EXPECTED_STATUSES=={'R01':[302,302],'R02':[302,400],'I01':[200,400],
        'I02':[302,400],'A01':[302,400],'A02':[302,400],'X01':[200,400],
        'X02':[302,400],'D01':[302,302],'D02':[302,302],'D03':[400,302],
        'F01':[400,200],'F02':[400,302],'F03':[400,302],'F04':[400,302]}


@pytest.mark.parametrize('case',g.REMAINING_CASES)
def test_safe_status_diagnostic_is_case_specific_and_numeric(case):
    import json
    payload=dict(case=case,expected=f.EXPECTED_STATUSES[case],actual=[500,400])
    error=g.Rejected('unexpected-staff-status:'+json.dumps(payload))
    assert shared.status_diagnostic(error,case=case)==payload
    assert shared.status_diagnostic(g.Rejected('secret'),case=case) is None


def test_completed_status_mismatch_is_reported_and_no_case_retry_occurs(monkeypatch):
    import json
    rt=runtime();calls=[];reports=[]
    payload=dict(case='I02',expected=[302,400],actual=[500,400])
    def race(runtime,case,*,namespace):
        calls.append(case);runtime.data=combine(runtime.data,added(case,1))
        raise g.Rejected('unexpected-staff-status:'+json.dumps(payload))
    monkeypatch.setattr(f,'race_case',race)
    with pytest.raises(g.Rejected):r.run(rt,lambda kind,data:reports.append((kind,data)))
    assert calls==['I02']
    assert ('HTTP STATUS',payload) in reports
    assert not any(kind=='CASE' for kind,data in reports)
    assert rt.preflight.call_count==2
