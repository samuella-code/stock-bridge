"""Disconnected tests of bounded failure evidence; no requests or database calls."""
import json
import pytest
from tests.support.postgres_staff_fixtures import require_staff_statuses
from tests.support.postgres_staff_guard import Rejected


def test_expected_codes_still_pass():
    assert require_staff_statuses('I01',[200,400],[200,400]) is None


@pytest.mark.parametrize('actual',[[400,400],[500,400],[302,400],[200,409]])
def test_unexpected_codes_retain_failure_and_bounded_evidence(actual):
    with pytest.raises(Rejected) as failure:
        require_staff_statuses('I01',actual,[200,400])
    prefix,payload=str(failure.value).split(':',1)
    assert prefix=='unexpected-staff-status'
    assert json.loads(payload)==dict(case='I01',expected=[200,400],actual=actual)
    assert len(str(failure.value))<150


@pytest.mark.parametrize('actual',[
    None,{},[200],(200,400),[True,400],[99,400],[600,400],
    ['secret-url',400],[{'password':'secret'},400],[200,400,500]])
def test_untrusted_values_are_never_in_failure_message(actual):
    with pytest.raises(Rejected,match='^invalid-staff-status-evidence$'):
        require_staff_statuses('I01',actual,[200,400])


@pytest.mark.parametrize('case',['foreign','I01-secret','',None,{}])
def test_untrusted_case_rejected_without_echo(case):
    with pytest.raises(Rejected,match='^invalid-staff-status-evidence$'):
        require_staff_statuses(case,[500,400],[200,400])


def test_evidence_is_retained_in_exception_for_safe_reporting():
    with pytest.raises(Rejected) as failure:
        require_staff_statuses('I01',[500,400],[200,400])
    # A safe reporter can use the exception without request/response contents.
    assert '"actual":[500,400]' in str(failure.value)
