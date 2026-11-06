"""Manager PostgreSQL cases. All app/DB imports occur only after launcher gates."""
from datetime import timedelta
import hashlib
import json
import re
from tests.support.postgres_staff_guard import MANAGER_CASES,V18,require

# Expected final HTTP responses are from team_manage (including its post-failure
# context rebuild), not assumed from the old owner-only manage endpoint.
RACES = {
    'C01': (302,403), 'C02': (302,302), 'C03': (200,400),
    'C04': (302,400), 'C05': (302,400), 'C06': (302,400),
    'B01': (400,200), 'B02': (400,302), 'B03': (400,200), 'B04': (400,302),
}


def state_digest(runtime):
    return hashlib.sha256(json.dumps(runtime.snapshot(),sort_keys=True,default=str).encode()).hexdigest()


def seed(runtime,case):
    from app import db
    from app.models import User,Business,AccountBilling,RecurringSubscription,StaffMembership,StaffMembershipBusiness
    from app.staff import roles,service
    from tests.support.postgres_staff_fixtures import T,PASSWORD
    app=runtime.app()
    with app.app_context():
        owner=User(full_name=case+' owner',email=case.lower()+'-owner@example.invalid',email_verified_at=T)
        owner.set_password(PASSWORD);db.session.add(owner);db.session.flush()
        shops=[Business(user_id=owner.id,name=case+' permitted '+str(n)) for n in (1,2)]
        foreign=User(full_name=case+' foreign',email=case.lower()+'-foreign@example.invalid',email_verified_at=T)
        foreign.set_password(PASSWORD);db.session.add_all(shops+[foreign]);db.session.flush()
        foreign_shop=Business(user_id=foreign.id,name=case+' forbidden foreign shop');db.session.add(foreign_shop)
        db.session.add(AccountBilling(user_id=owner.id,primary_business_id=shops[0].id))
        sub=RecurringSubscription(user_id=owner.id,business_id=shops[0].id,plan_code='pro',billing_interval='monthly',amount_kobo=1000000,provider_plan_code='PLN_synthetic',status='active',current_period_start=T-timedelta(days=1),current_period_end=T+timedelta(days=30))
        db.session.add(sub);db.session.commit()
        # Explicit owner grant, never implicit Manager template promotion.
        roles.save(owner,'manager','Manager',list(roles.TEMPLATES['manager'][1]),now=T,authority_permissions=['staff_admin','records_export']);db.session.commit()
        users=[];members=[]
        occupied=4 if case in ('G01','C03','B01') else 3
        for n in range(occupied):
            user=User(full_name=case+' person '+str(n),email=case.lower()+'-person-'+str(n)+'@example.invalid',email_verified_at=T,staff_only=True)
            user.set_password(PASSWORD);db.session.add(user);db.session.flush()
            role='manager' if n<2 else 'salesperson' if n==2 else 'inventory_staff'
            member=StaffMembership(owner_id=owner.id,user_id=user.id,role=role,state='active',duty_label='',grant_event_id=service.event_watermark(owner.id),grant_started_at=T,grant_pro_start=sub.current_period_start,grant_subscription_id=sub.id,access_until=sub.current_period_end)
            db.session.add(member);db.session.flush()
            bid=shops[1].id if case=='G01' and n==3 else shops[0].id
            db.session.add(StaffMembershipBusiness(membership_id=member.id,business_id=bid));users.append(user);members.append(member)
        if case in ('C04','B02'):roles.save(owner,None,'Fourth',[],now=T)
        db.session.commit()
        data=dict(owner=owner.id,owner_email=owner.email,shops=[b.id for b in shops],foreign=foreign.id,foreign_shop=foreign_shop.id,
            users=[u.id for u in users],emails=[u.email for u in users],members=[m.id for m in members],names=[u.full_name for u in users],
            subscription=sub.id,core_permissions=list(roles.TEMPLATES['manager'][1]))
    # No ORM instances/sessions cross workers or escape fixture construction.
    return data


def snapshot(runtime,data):
    from tests.support.postgres_staff_fixtures import state
    result=state(runtime,data['owner'])
    with runtime.connection() as conn:
        result['audit_pid']=conn.info.backend_pid
        # Include generic rows too, so orphaned AuditLog writes cannot escape the
        # rollback assertion merely because their structured row is absent.
        result['audit_rows']=conn.execute('SELECT id,actor_id,action,target_id FROM audit_log WHERE actor_id IN (SELECT id FROM public."user" WHERE lower(email) LIKE %s) ORDER BY id',(data['owner_email'].split('-owner@')[0]+'-%',)).fetchall()
        result['audit_details']=conn.execute('SELECT s.audit_log_id,s.owner_id,s.details FROM staff_audit_event s WHERE s.owner_id=%s ORDER BY s.id',(data['owner'],)).fetchall()
    return result


def comparable(value):return {k:v for k,v in value.items() if k not in ('pid','audit_pid')}


def new_audits(before,after):
    ids={row[0] for row in before['audit_rows']}
    return [(row[1],row[2]) for row in after['audit_rows'] if row[0] not in ids]


def expect_audits(before,after,wanted):
    actual={}
    for actor,action in new_audits(before,after):actual[(actor,action)]=actual.get((actor,action),0)+1
    require(actual==wanted,'manager-audit-count-or-actor-differs')


def payload(client,path,**values):
    from tests.support.postgres_staff_fixtures import csrf
    return dict(values,csrf_token=csrf(client,path))


def post(client,path,status,**values):
    response=client.post(path,data=payload(client,path,**values))
    require(response.status_code==status,'manager-response-differs')
    return response


def assert_identity_unchanged(before,after):
    for key in ('users','identity_business','identity_account_billing','identity_billing_event'):
        require(before[key]==after[key],'manager-created-or-mutated-owner-identity')
    previous={m['id']:m for m in before['staff_membership']}
    require(set(previous)=={m['id'] for m in after['staff_membership']},'unexpected-membership-count')
    for row in after['staff_membership']:
        for key in ('owner_id','user_id','grant_event_id','grant_started_at','grant_pro_start','grant_subscription_id','access_until','created_at'):
            require(row[key]==previous[row['id']][key],'manager-mutated-grant-anchor')


def assert_no_changes(before,after):require(comparable(before)==comparable(after),'denial-mutated-records')


def check_case(runtime,case):
    from tests.support.postgres_staff_fixtures import login,T
    data=seed(runtime,case);app=runtime.app();path='/staff/team/'+str(data['owner'])
    owner=login(app,data['owner_email']);manager=login(app,data['emails'][0])
    before=snapshot(runtime,data)
    change=dict(action='change',id=data['members'][2],role='salesperson',duty_label='Allowed change',business_ids=[data['shops'][0]])
    if case=='G01':
        response=manager.get(path);require(response.status_code==200,'manager-page-unavailable')
        html=response.get_data(as_text=True)
        for text in (data['names'][3],case+' permitted 2',case+' forbidden foreign shop'):
            require(text not in html,'manager-scope-name-leak')
        post(manager,path,400,**dict(change,id=data['members'][3]))
        post(manager,path,400,**dict(change,business_ids=data['shops']))
        post(manager,path,400,action='invite',email='g01-new@example.invalid',role='salesperson',business_ids=[data['foreign_shop']])
        require(manager.get('/staff/team/'+str(data['foreign'])).status_code==403,'foreign-account-access')
    elif case=='G02':
        post(manager,path,400,**dict(change,id=data['members'][0]))
        post(manager,path,400,action='role_save',role_key='manager',role_name='Self',permissions=['sales_view'])
        post(manager,path,400,action='role_remove',role_key='manager',replacement='salesperson')
        post(manager,path,400,action='revoke',kind='membership',id=data['members'][0])
        post(manager,path,400,action='reconfirm',confirm='on',id=data['members'][0])
        post(manager,path,400,action='invite',email=data['emails'][0],role='salesperson',business_ids=[data['shops'][0]])
    elif case=='G03':
        for values in (['financial_view'],['staff_admin'],['records_export']):
            post(manager,path,400,action='role_save',role_name='Escalation',permissions=values)
        post(manager,path,400,action='role_save',role_key='salesperson',role_name='Forged',authority_permissions=['staff_admin'])
        post(manager,path,400,**dict(change,role='manager'))
        post(manager,path,400,action='invite',email='g03-new@example.invalid',role='manager',business_ids=[data['shops'][0]])
        post(manager,path,400,action='owner_change',id=data['owner'])
    elif case=='G04':
        post(owner,path,302,action='role_save',role_key='salesperson',role_name='Cashier',permissions=['sales_view'],authority_permissions=['staff_admin','records_export'])
        granted=snapshot(runtime,data)
        row=next(r for r in granted['staff_role'] if r['key']=='salesperson')
        require(set(row['permissions'])=={'sales_view','staff_admin','records_export'},'owner-grant-differs')
        post(manager,path,400,action='role_save',role_key='salesperson',role_name='Forbidden',permissions=['sales_view'])
        assert_no_changes(granted,snapshot(runtime,data))
        post(owner,path,302,action='role_save',role_key='salesperson',role_name='Cashier',permissions=['sales_view'],authority_permissions=[])
        after=snapshot(runtime,data);require(next(r for r in after['staff_role'] if r['key']=='salesperson')['permissions']==['sales_view'],'owner-removal-differs')
        assert_identity_unchanged(before,after)
        expect_audits(before,after,{(data['owner'],'STAFF_ROLE_CHANGED'):2});return
    elif case=='G05':
        from app import db
        from app.models import User
        from app.staff import delegation_access,delegation,service
        with app.app_context():
            owning=db.session.get(User,data['owner']);acting=db.session.get(User,data['users'][0])
            ids,fields=delegation_access.export_plan(owning,acting,'sales',data['shops'],['sale_id','quantity','unit_cost','revenue'],now=T)
            require(ids==frozenset({data['shops'][0]}) and fields==('sale_id','quantity'),'export-view-or-scope-leak')
            for dataset,businesses,columns in [('reports',data['shops'],['revenue']),('sales',[data['foreign_shop']],['sale_id']),('sales',data['shops'],['password_hash'])]:
                try:delegation_access.export_plan(owning,acting,dataset,businesses,columns,now=T)
                except ValueError:pass
                else:raise AssertionError('Export projection failed open')
            require(service.can_operate() is False and delegation.can_export() is False,'workflow-enabled')
            try:delegation_access.export_records()
            except ValueError:pass
            else:raise AssertionError('Staff exports enabled')
    elif case=='G06':
        post(manager,path,200,action='invite',email='g06-new@example.invalid',role='salesperson',business_ids=[data['shops'][0]])
        post(manager,path,302,**dict(change,role='inventory_staff'))
        post(manager,path,302,action='revoke',kind='membership',id=data['members'][2])
        post(manager,path,302,action='role_save',role_name='Ordinary custom',permissions=['sales_view'])
        middle=snapshot(runtime,data);custom=next(r for r in middle['staff_role'] if r['name']=='Ordinary custom')
        post(manager,path,302,action='role_save',role_key=custom['key'],role_name='Renamed ordinary',permissions=['sales_view'])
        post(manager,path,302,action='role_remove',role_key=custom['key'],replacement='')
        after=snapshot(runtime,data)
        require(after['seats']==before['seats'] and len(after['staff_invitation'])==1,'manager-seat-transfer-differs')
        require(next(m for m in after['staff_membership'] if m['id']==data['members'][2])['state']=='revoked','manager-removal-differs')
        wanted={action:1 for action in ('STAFF_INVITED','STAFF_DELIVERY_RESULT','STAFF_CHANGE_BEFORE','STAFF_CHANGED','STAFF_REVOKED','STAFF_ROLE_CREATED','STAFF_ROLE_CHANGED','STAFF_ROLE_REMOVED')}
        assert_identity_unchanged(before,after)
        expect_audits(before,after,{(data['users'][0],action):count for action,count in wanted.items()});return
    else:raise AssertionError('Unknown Manager check')
    assert_no_changes(before,snapshot(runtime,data))


def race_plan(case,data):
    """Plain request descriptions only. No clients, ORM objects or token payloads."""
    shop=data['shops'][0];owner=data['owner'];m1,m2=data['users'][:2]
    remove=dict(action='role_save',role_key='manager',role_name='Manager',permissions=data['core_permissions'],authority_permissions=['records_export'])
    change=dict(action='change',id=data['members'][2],role='salesperson',duty_label='Manager won',business_ids=[shop])
    invite=lambda label:dict(action='invite',email=case.lower()+'-'+label+'@example.invalid',role='salesperson',business_ids=[shop])
    role=lambda label:dict(action='role_save',role_name=case+' '+label,permissions=['sales_view'])
    if case in ('C01','C02'):actors=[owner,m1];actions=[remove,change]
    elif case in ('C03','B01'):actors=[m1,m2];actions=[invite('first'),invite('second')]
    elif case in ('C04','B02'):actors=[m1,m2];actions=[role('first'),role('second')]
    elif case=='C05':actors=[owner,m1];actions=[dict(action='change',id=data['members'][0],role='manager',duty_label='',business_ids=[data['shops'][1]]),change]
    elif case=='C06':actors=[owner,m1];actions=[dict(change,business_ids=data['shops'],duty_label='Owner expanded scope'),dict(action='role_save',role_key='salesperson',role_name='Denied shared change',permissions=['sales_view'])]
    elif case=='B03':actors=[owner,m2];actions=[remove,invite('second')]
    elif case=='B04':actors=[m1,owner];actions=[dict(change,role='inventory_staff',duty_label='Rolled back'),dict(change,duty_label='Owner after rollback')]
    else:raise AssertionError('Unknown Manager race')
    if case=='C02':actors.reverse();actions.reverse()
    return actors,actions


def assert_race(case,before,after,data):
    from tests.support.postgres_staff_fixtures import T
    assert_identity_unchanged(before,after)
    owner=data['owner'];m1,m2=data['users'][:2]
    wanted={}
    if case in ('C01','C02'):
        require('staff_admin' not in next(r for r in after['staff_role'] if r['key']=='manager')['permissions'],'permission-removal-missing')
        wanted[(owner,'STAFF_ROLE_CHANGED')]=1
        row=next(m for m in after['staff_membership'] if m['id']==data['members'][2])
        require(row['duty_label']==('Manager won' if case=='C02' else ''),'stale-manager-change')
        if case=='C02':wanted.update({(m1,'STAFF_CHANGE_BEFORE'):1,(m1,'STAFF_CHANGED'):1})
    elif case in ('C03','B01','B03'):
        require(len(after['staff_invitation'])==1 and after['seats']==before['seats']+1<=5,'concurrent-seat-overflow')
        row=after['staff_invitation'][0]
        require(row['state']=='pending' and row['role']=='salesperson' and row['delivery_status']=='disabled'
                and row['owner_id']==owner and row['grant_event_id']==0 and row['grant_started_at']==T
                and row['grant_pro_start']==T-timedelta(days=1) and row['expires_at']==T+timedelta(days=2)
                and row['grant_subscription_id']==data['subscription'] and row['created_at']==T
                and row['membership_id'] is None and row['delivery_attempted_at'] is None
                and row['email']==case.lower()+('-first' if case=='C03' else '-second')+'@example.invalid'
                and re.fullmatch('[a-f0-9]{64}',row['token_digest']),'pending-invitation-grant-differs')
        actor=m1 if case=='C03' else m2
        wanted={(actor,'STAFF_INVITED'):1,(actor,'STAFF_DELIVERY_RESULT'):1}
        if case=='B03':require('staff_admin' in next(r for r in after['staff_role'] if r['key']=='manager')['permissions'],'rollback-lost-authority')
    elif case in ('C04','B02'):
        require(len(after['staff_role'])==len(before['staff_role'])+1 and sum(not r['archived'] for r in after['staff_role'])==5,'concurrent-role-overflow')
        wanted={(m1 if case=='C04' else m2,'STAFF_ROLE_CREATED'):1}
    elif case in ('C05','C06','B04'):
        wanted={(owner,'STAFF_CHANGE_BEFORE'):1,(owner,'STAFF_CHANGED'):1}
        member_id=data['members'][0] if case=='C05' else data['members'][2]
        ids={r['business_id'] for r in after['memberships_assigned'] if r['membership_id']==member_id}
        require(ids==({data['shops'][1]} if case=='C05' else set(data['shops']) if case=='C06' else {data['shops'][0]}),'scope-or-rollback-assignments-differ')
        if case=='C06':require(next(r for r in after['staff_role'] if r['key']=='salesperson')['name']=='Salesperson/Cashier','out-of-scope-role-mutated')
        if case=='B04':require(next(r for r in after['staff_membership'] if r['id']==member_id)['duty_label']=='Owner after rollback','rollback-duty-differs')
    old_members={m['id']:m for m in before['staff_membership']}
    for member in after['staff_membership']:
        expected=dict(old_members[member['id']])
        if member['id']==data['members'][2] and case in ('C02','C06','B04'):
            expected['duty_label']={'C02':'Manager won','C06':'Owner expanded scope','B04':'Owner after rollback'}[case]
        require(member==expected,'unexpected-member-mutation')
    expected_assignments={m['id']:{a['business_id'] for a in before['memberships_assigned'] if a['membership_id']==m['id']} for m in before['staff_membership']}
    if case=='C05':expected_assignments[data['members'][0]]={data['shops'][1]}
    if case=='C06':expected_assignments[data['members'][2]]=set(data['shops'])
    require({m['id']:{a['business_id'] for a in after['memberships_assigned'] if a['membership_id']==m['id']} for m in after['staff_membership']}==expected_assignments,'unexpected-business-assignment')
    old_roles={r['key']:r for r in before['staff_role']};new_roles={r['key']:r for r in after['staff_role']}
    for key,row in old_roles.items():
        expected=dict(row)
        if key=='manager' and case in ('C01','C02'):
            expected['permissions']=sorted(data['core_permissions']+['records_export'])
        require(new_roles.get(key)==expected,'unexpected-existing-role-mutation')
    if case in ('C04','B02'):
        extra=[row for key,row in new_roles.items() if key not in old_roles]
        require(len(extra)==1 and extra[0]['owner_id']==owner and extra[0]['permissions']==['sales_view']
                and extra[0]['name']==case+(' first' if case=='C04' else ' second'),'winning-role-differs')
    else:require(set(new_roles)==set(old_roles),'unexpected-role-count')
    expect_audits(before,after,wanted)
    # No unexpected invitations, seats or membership changes may hide in races.
    if case not in ('C03','B01','B03'):require(after['staff_invitation']==before['staff_invitation'] and after['seats']==before['seats'],'unexpected-seat-change')


def race_case(runtime,case):
    from app import db
    from tests.support.postgres_staff_fixtures import login
    from tests.support.postgres_staff_races import concurrent_posts
    data=seed(runtime,case);app=runtime.app();path='/staff/team/'+str(data['owner'])
    actors,actions=race_plan(case,data)
    emails={data['owner']:data['owner_email'],**dict(zip(data['users'],data['emails']))}
    clients=[login(app,emails[actor]) for actor in actors]
    payloads=[payload(client,path,**action) for client,action in zip(clients,actions)]
    before=snapshot(runtime,data);proof={}
    def baseline():
        fresh=snapshot(runtime,data)
        require(all(fresh[k] not in proof['workers']+[proof['observer']] for k in ('pid','audit_pid')),'manager-verifier-backend-reused')
        assert_no_changes(before,fresh)
    with app.app_context(),runtime.connection() as observer:
        statuses=concurrent_posts(db.engine,clients,[path,path],payloads,observer,owner_id=data['owner'],rollback=case.startswith('B'),baseline_check=baseline if case.startswith('B') else None,fail_audit_number=2 if case=='B04' else 1,evidence=proof)
    require(tuple(statuses)==RACES[case],'manager-race-response-differs')
    after=snapshot(runtime,data)
    require(all(after[k] not in proof['workers']+[proof['observer']] for k in ('pid','audit_pid')),'manager-final-backend-reused')
    assert_race(case,before,after,data)


def execute_case(runtime,case):
    import threading
    require(case in MANAGER_CASES,'manager-case-rejected')
    runtime.guards.check_clean();runtime.check_source()
    with runtime.environment():
        runtime.preflight(V18)
        if case.startswith('G'):check_case(runtime,case)
        else:race_case(runtime,case)
        require(not any(t.name.startswith('stockbridge-race-') and t.is_alive() for t in threading.enumerate()),'manager-shutdown-unconfirmed')
        runtime.guards.check_clean();runtime.preflight(V18)
