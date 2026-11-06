"""State-bound I01 only. No cleanup, migrations, automatic retries or approvals."""
from collections import Counter
import hashlib
import json
import re
from tests.support.postgres_staff_guard import V18, require


def encode(row):
    return json.dumps(row,sort_keys=True,default=str,separators=(',',':'))


def checked_snapshot(rt):
    rows=rt.snapshot()
    require(type(rows) is dict and bool(rows) and all(type(k) is str and type(v) is list
        and all(type(row) is dict for row in v) for k,v in rows.items()),'continuation-snapshot-invalid')
    require(sum(map(len,rows.values()))<=10000,'continuation-snapshot-too-large')
    return rows


def digest(rows):
    # Match the existing Manager state_digest serialization exactly.
    return hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()


def namespace(auth):
    value=auth.get('continuation_namespace')
    require(type(value) is str and re.fullmatch(r'i01-cont-[a-f0-9]{8}',value),'continuation-namespace-rejected')
    return value


def require_unused(rows,label):
    require(not any(type(row.get('email')) is str and row['email'].lower().startswith(label+'-')
        for row in rows.get('user',[])),'continuation-namespace-already-used')


def inspect(rt):
    require(rt.auth.get('mode')=='i01-state' and rt.auth.get('cases')==[],'continuation-mode-rejected')
    rt.guards.check_clean();rt.check_source();label=namespace(rt.auth)
    rt.preflight(V18)
    rows=checked_snapshot(rt);require_unused(rows,label)
    rt.guards.check_clean();rt.check_source()
    return dict(starting_state_sha256=digest(rows),continuation_namespace=label,
        namespace_unused=True,schema=V18,rows=sum(map(len,rows.values())))


def require_preserved(before,after,label):
    require(set(before)==set(after),'continuation-table-inventory-changed')
    additions={}
    for table,original in before.items():
        old=Counter(map(encode,original));new=Counter(map(encode,after[table]))
        require(not old-new,'continuation-existing-record-changed')
        remaining=new-old
        additions[table]=[]
        for row in after[table]:
            key=encode(row)
            if remaining[key]>0:
                additions[table].append(row);remaining[key]-=1
    users=additions.get('user',[])
    allowed_emails={label+'-owner@example.invalid'}|{f'{label}-{i}@example.invalid' for i in range(4)}
    require(len(users)==5 and {r.get('email') for r in users}==allowed_emails,'continuation-user-scope')
    owners=[r for r in users if r.get('email')==label+'-owner@example.invalid']
    require(len(owners)==1,'continuation-owner-scope');owner=owners[0]['id']
    user_ids={r['id'] for r in users}
    business_ids={r['id'] for r in additions.get('business',[])}
    member_ids={r['id'] for r in additions.get('staff_membership',[])}
    invitation_ids={r['id'] for r in additions.get('staff_invitation',[])}
    audit_ids={r['id'] for r in additions.get('audit_log',[])}
    checks={
        'user':lambda r:r.get('email') in allowed_emails,
        'business':lambda r:r.get('user_id')==owner,
        'account_billing':lambda r:r.get('user_id')==owner,
        'recurring_subscription':lambda r:r.get('user_id')==owner,
        'staff_role':lambda r:r.get('owner_id')==owner,
        'staff_membership':lambda r:r.get('owner_id')==owner and r.get('user_id') in user_ids,
        'staff_membership_business':lambda r:r.get('membership_id') in member_ids and r.get('business_id') in business_ids,
        'staff_invitation':lambda r:r.get('owner_id')==owner and r.get('email') in {f'{label}-{i}-invite@example.invalid' for i in (0,1)},
        'staff_invitation_business':lambda r:r.get('invitation_id') in invitation_ids and r.get('business_id') in business_ids,
        'audit_log':lambda r:r.get('actor_id')==owner and r.get('business_id') in business_ids|{None},
        'staff_audit_event':lambda r:r.get('owner_id')==owner and r.get('audit_log_id') in audit_ids,
    }
    for table,rows in additions.items():
        require(not rows or (table in checks and all(checks[table](r) for r in rows)), 'continuation-added-record-outside-scope')


def status_diagnostic(error):
    # Do not print arbitrary exception messages, even for unexpected failures.
    if not isinstance(error,ValueError) or len(error.args)!=1 or type(error.args[0]) is not str:return None
    text=error.args[0];prefix='unexpected-staff-status:'
    if not text.startswith(prefix) or len(text)>150:return None
    try:data=json.loads(text[len(prefix):])
    except (ValueError,TypeError):return None
    if (type(data) is not dict or set(data)!={'case','expected','actual'} or data['case']!='I01'
            or data['expected']!=[200,400] or type(data['actual']) is not list or len(data['actual'])!=2
            or any(type(code) is not int or not 100<=code<=599 for code in data['actual'])):return None
    return dict(case='I01',expected=[200,400],actual=data['actual'])


def run(rt):
    require(rt.auth.get('mode')=='i01-continuation' and rt.auth.get('cases')==['I01'],'continuation-mode-rejected')
    rt.guards.check_clean();rt.check_source();label=namespace(rt.auth)
    rt.preflight(V18)
    before=checked_snapshot(rt)
    require(digest(before)==rt.auth.get('starting_state_sha256'),'continuation-starting-state-differs')
    require_unused(before,label)
    from tests.support.postgres_staff_fixtures import race_case
    try:
        race_case(rt,'I01',namespace=label)
    except BaseException as error:
        # A completed HTTP-status mismatch still gets read-only preservation checks.
        # Other failures (including uncertain worker shutdown or guard latches) stop.
        if status_diagnostic(error) is not None:
            rt.guards.check_clean();rt.check_source()
            require_preserved(before,checked_snapshot(rt),label)
            rt.preflight(V18)
        raise
    rt.guards.check_clean();rt.check_source()
    after=checked_snapshot(rt);require_preserved(before,after,label)
    rt.preflight(V18)
    return dict(status='PASS',case='I01',continuation_namespace=label,
        existing_records_preserved=True,end_state_sha256=digest(after))
