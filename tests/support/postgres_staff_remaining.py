"""Fixed remaining twelve Staff cases. No migrations, cleanup or automatic retry."""
import re
from tests.support.postgres_staff_guard import V18,REMAINING_CASES,require
from tests.support import postgres_staff_i01_continuation as shared


def namespace(auth):
    value=auth.get('continuation_namespace')
    require(type(value) is str and re.fullmatch(r'staff-cont-[a-f0-9]{8}',value),'continuation-namespace-rejected')
    return value


def inspect(rt):
    require(rt.auth.get('mode')=='staff-remaining-state' and rt.auth.get('cases')==[],'continuation-mode-rejected')
    rt.guards.check_clean();rt.check_source();label=namespace(rt.auth)
    rt.preflight(V18);rows=shared.checked_snapshot(rt);shared.require_unused(rows,label)
    rt.guards.check_clean();rt.check_source()
    return dict(starting_state_sha256=shared.digest(rows),continuation_namespace=label,
        namespace_unused=True,schema=V18,rows=sum(map(len,rows.values())),cases=list(REMAINING_CASES))


def run(rt,report):
    require(rt.auth.get('mode')=='staff-remaining' and rt.auth.get('cases')==list(REMAINING_CASES),'continuation-selection-rejected')
    rt.guards.check_clean();rt.check_source();base=namespace(rt.auth)
    rt.preflight(V18);current=shared.checked_snapshot(rt)
    require(shared.digest(current)==rt.auth.get('starting_state_sha256'),'continuation-starting-state-differs')
    shared.require_unused(current,base)
    from tests.support.postgres_staff_fixtures import race_case
    completed=[]
    for index,case in enumerate(REMAINING_CASES):
        rt.guards.check_clean();rt.check_source()
        if index:
            rt.preflight(V18)
            fresh=shared.checked_snapshot(rt)
            require(shared.digest(fresh)==shared.digest(current),'continuation-between-case-state-differs')
            current=fresh
        label=base+'-'+case.lower();shared.require_unused(current,label)
        report('RUNNING',dict(case=case))
        try:
            race_case(rt,case,namespace=label)
        except BaseException as error:
            safe=shared.status_diagnostic(error,case=case)
            if safe is not None:
                report('HTTP STATUS',safe)
                rt.guards.check_clean();rt.check_source()
                shared.require_preserved(current,shared.checked_snapshot(rt),label,case=case)
                rt.preflight(V18)
            raise  # Never try another case after any failure.
        rt.guards.check_clean();rt.check_source()
        after=shared.checked_snapshot(rt);shared.require_preserved(current,after,label,case=case)
        rt.preflight(V18);current=after;completed.append(case)
        report('CASE',dict(case=case,status='PASS',existing_records_preserved=True,end_state_sha256=shared.digest(current)))
    rt.guards.check_clean();rt.check_source()
    return dict(status='PASS',cases=completed,continuation_namespace=base,
        existing_records_preserved=True,end_state_sha256=shared.digest(current))
