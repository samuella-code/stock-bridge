"""Executable Staff cases, inert until the separately authorized launcher calls.

No SQLite fixtures, create_all/drop_all, cleanup, provider or email transport.
"""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import os
import re
from unittest.mock import patch
from tests.support.postgres_staff_guard import TARGET, RUN, V17, V18, ACK, require, target_url

T = datetime(2030, 1, 15, 12)
PASSWORD = 'synthetic-staff-fixture-password'
STAFF_TABLES = ('staff_role','staff_membership','staff_invitation','staff_membership_business','staff_invitation_business','staff_audit_event')
BASE_TABLES = ('user','business','account_billing','payment','recurring_subscription','billing_event','product','sale','sale_item','expense','transaction_submission','audit_log')


EXPECTED_STATUSES = {'R01':[302,302],'R02':[302,400],'I01':[200,400],'I02':[302,400],'A01':[302,400],
        'A02':[302,400],'X01':[200,400],'X02':[302,400],'D01':[302,302],'D02':[302,302],
        'D03':[400,302],'F01':[400,200],'F02':[400,302],'F03':[400,302],'F04':[400,302]}


class Runtime:
    def __init__(self, auth, password, guards, source_check):
        self.auth,self.password,self.guards,self.check_source=auth,password,guards,source_check
        self.root=Path(auth['repository']);self.instance=None;self.stack=ExitStack()
        from sqlalchemy.engine import URL
        url=URL.create('postgresql+psycopg',username=TARGET['user'],password=password,
                       host=TARGET['host'],port=TARGET['port'],database=TARGET['dbname'])
        self.env=dict(PATH='/usr/bin:/bin',HOME='/tmp',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',
            STOCKBRIDGE_TEST_POSTGRES_ACK='disposable-local-only',STOCKBRIDGE_TEST_POSTGRES_RUN_ID=RUN,
            STOCKBRIDGE_TEST_POSTGRES_MODE='ubuntu-isolated-cluster',
            STOCKBRIDGE_TEST_POSTGRES_UBUNTU_ACK='16/stockbridge-test:55432:loopback-only',
            STOCKBRIDGE_TEST_POSTGRES_OS_EVIDENCE='privileged-socket',
            STOCKBRIDGE_TEST_POSTGRES_HELPER_ACK='approved-local-read-only-helper',
            STOCKBRIDGE_TEST_POSTGRES_DB_EVIDENCE='scoped-function',
            STOCKBRIDGE_TEST_POSTGRES_SQL_EVIDENCE_ACK='approved-isolated-read-only-function',
            STOCKBRIDGE_TEST_POSTGRES_URL=url.render_as_string(hide_password=False),
            STOCKBRIDGE_STAFF_PG_ACK=ACK)
        target_url(self.env)

    @contextmanager
    def environment(self):
        with patch.object(os,'environ',self.env): yield

    def preflight(self, phase):
        self.guards.check_clean();self.check_source()
        from tests.support.postgres_staff_identity import preflight
        return preflight(self.env,phase=phase,expected_tables=self.auth['relations'][phase],
            expected_identity=(self.auth['database_oid'],self.auth['role_oid']))

    @contextmanager
    def connection(self, *, observer=True):
        from tests.support.postgres_connections import connection_policy
        import psycopg
        self.guards.check_clean()
        with psycopg.connect(**TARGET,password=self.password,**connection_policy(self.env,observer=observer)) as conn:
            with conn.transaction():
                if observer:
                    conn.execute('SET TRANSACTION READ ONLY')
                    require(conn.execute('SHOW transaction_read_only').fetchone()[0]=='on','readonly-required')
                yield conn

    def app(self):
        if self.instance is None:
            # Mocks installed before application imports; real transports stay blocked.
            deny=self.guards.denier('external-service-attempt')
            for path in ('app.email_service._send_email','app.subscriptions.provider.call',
                         'app.subscriptions.provider.verify','app.subscriptions.provider.fetch_subscription',
                         'app.subscriptions.provider.disable_subscription'):
                # Patch actual provider APIs only, never manufacture missing attributes.
                if path.startswith('app.subscriptions.provider.'):
                    import app.subscriptions.provider as provider
                    if not hasattr(provider,path.rsplit('.',1)[1]):continue
                self.stack.enter_context(patch(path,side_effect=deny))
            from app import create_app
            from sqlalchemy.pool import NullPool
            from tests.support.postgres_staff_schema import staff_application_policy
            config=dict(TESTING=True,SECRET_KEY='staff-pg-synthetic-only',SQLALCHEMY_DATABASE_URI=target_url(self.env),
                SQLALCHEMY_BINDS={},SQLALCHEMY_ENGINE_OPTIONS=dict(poolclass=NullPool,connect_args=staff_application_policy(self.env)),
                SUBSCRIPTIONS_ENABLED=True,BILLING_PROVIDER_ENABLED=False,INVENTORY_EMAILS_ENABLED=False,
                INVENTORY_EMAIL_TRIGGER_URL='',INVENTORY_EMAIL_TRIGGER_SECRET='',STAFF_INVITATION_EMAIL_ENABLED='',
                BUSINESS_IMAGE_STORAGE='disabled',SMTP_HOST='',SMTP_FROM_EMAIL='',SMTP_PASSWORD='',
                PAYSTACK_SECRET_KEY='',PAYSTACK_PUBLIC_KEY='',GOOGLE_CLIENT_ID='',APPLE_CLIENT_ID='',
                SERVER_NAME='localhost',PROPAGATE_EXCEPTIONS=False)
            with self.environment():self.instance=create_app(config)
            self.instance.config['WTF_CSRF_ENABLED']=True
            class Clock(datetime):
                @classmethod
                def utcnow(cls):return T
            for module in ('service','routes','roles','delivery'):
                self.stack.enter_context(patch('app.staff.'+module+'.datetime',Clock))
        return self.instance

    def close(self):
        if self.instance is not None:
            from app import db
            with self.instance.app_context():db.session.remove();db.engine.dispose()
        self.stack.close()  # SQL records intentionally retained.

    def snapshot(self, *, original_only=False):
        from psycopg.rows import dict_row
        output={}
        with self.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cursor:
                tables=conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
                for (name,) in tables:
                    if name=='alembic_version' or original_only and name in STAFF_TABLES:continue
                    require(bool(re.fullmatch('[a-z_][a-z_0-9]*',name)),'table-name-rejected')
                    cursor.execute('SELECT * FROM public."'+name+'"')
                    rows=[dict(row) for row in cursor.fetchall()]
                    if original_only and name=='user':
                        for row in rows:row.pop('staff_only',None)
                    output[name]=sorted(rows,key=lambda row:json.dumps(row,sort_keys=True,default=str))
        return output

    def baseline(self):
        rows=self.snapshot(original_only=True)
        digest=hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()
        require(digest==self.auth.get('baseline_sha256'),'baseline-differs')
        require(all(rows.get(name) for name in BASE_TABLES),'baseline-missing')
        return rows

    def migrate17(self):
        self.preflight('empty');app=self.app()
        from flask_migrate import upgrade
        with app.app_context():upgrade(directory=str((self.root/'migrations').resolve()),revision=V17)
        self.preflight(V17)

    def seed_baseline(self):
        self.preflight(V17);require(not any(self.snapshot().values()),'baseline-target-not-empty')
        app=self.app()
        from app import db
        from app.models import User,Business,AccountBilling,Payment,RecurringSubscription,BillingEvent,Product,Sale,SaleItem,Expense,TransactionSubmission,AuditLog
        from sqlalchemy import MetaData,Table
        with app.app_context(),db.engine.begin() as conn:
            def insert(model,**values):
                # Reflection excludes0018 columns. Reuse explicit model defaults;
                # no ORM query or create_all against the0017 schema.
                table=Table(model.__tablename__,MetaData(),autoload_with=conn)
                for column in table.c:
                    source=model.__table__.c.get(column.name)
                    if column.name not in values and source is not None and source.default is not None:
                        default=source.default
                        values[column.name]=default.arg(None) if default.is_callable else default.arg
                return conn.execute(table.insert().values(**values).returning(*table.primary_key.columns)).scalar_one()
            u=insert(User,full_name='Preservation owner',email='preservation@example.invalid',password_hash='synthetic-unused',created_at=T,email_verified_at=T)
            b=insert(Business,user_id=u,name='Preservation business',created_at=T,trial_started_at=T-timedelta(days=14),trial_ends_at=T,subscription_plan='lifetime',subscription_status='active')
            p=insert(Payment,user_id=u,business_id=b,customer_email='preservation@example.invalid',reference='staff-preservation-payment',amount_kobo=300000,status='success',paid_at=T,created_at=T)
            insert(AccountBilling,user_id=u,legacy_payment_id=p,legacy_business_id=b,legacy_granted_at=T,primary_business_id=b,created_at=T)
            s=insert(RecurringSubscription,user_id=u,business_id=b,plan_code='pro',billing_interval='monthly',amount_kobo=1000000,provider_plan_code='PLN_synthetic',status='active',current_period_start=T-timedelta(days=1),current_period_end=T+timedelta(days=30),created_at=T)
            insert(BillingEvent,user_id=u,subscription_id=s,event_key='staff-preservation-event',kind='provider_event',message='Historical synthetic evidence',created_at=T)
            product=insert(Product,business_id=b,name='Preservation stock',buying_price=12,selling_price=20,stock_quantity=9,opening_quantity=10,created_at=T,updated_at=T)
            sale=insert(Sale,business_id=b,sold_at=T)
            insert(SaleItem,sale_id=sale,product_id=product,quantity=1,unit_price=20,unit_cost=11)
            insert(Expense,business_id=b,description='Preservation expense',amount=3,spent_at=T)
            insert(TransactionSubmission,business_id=b,user_id=u,operation='sale',key='preservation-key',payload_hash='a'*64,result_reference='preserved-sale',created_at=T)
            insert(AuditLog,actor_id=u,action='PRESERVATION',description='X'*300,created_at=T)
        self.preflight(V17)
        digest=hashlib.sha256(json.dumps(self.snapshot(original_only=True),sort_keys=True,default=str).encode()).hexdigest()
        print('STAFF BASELINE SHA256: '+digest)  # Evidence only; no approval generated.



def require_staff_statuses(case, actual, expected):
    """Bounded HTTP-code evidence only; never render request/response contents."""
    from tests.support.postgres_staff_guard import CASES
    require(type(case) is str and case in CASES
            and all(type(values) is list and len(values)==2
                and all(type(code) is int and 100<=code<=599 for code in values)
                for values in (actual,expected)), 'invalid-staff-status-evidence')
    if actual!=expected:
        evidence=dict(case=case,expected=expected,actual=actual)
        require(False,'unexpected-staff-status:'+json.dumps(evidence,separators=(',',':')))

def migration_case(rt, case):
    rt.preflight(V17);before=rt.baseline();app=rt.app()
    from app import db
    from alembic.operations import Operations
    from flask_migrate import upgrade
    rollback=[]
    from sqlalchemy import event
    with app.app_context():
        def mark(conn):rollback.append(True)
        event.listen(db.engine,'rollback',mark)
        try:
            if case=='M00':
                original=Operations.create_table
                def fail(ops,name,*args,**kwargs):
                    result=original(ops,name,*args,**kwargs)
                    if name=='staff_role':raise ValueError('Synthetic migration rollback')
                    return result
                try:
                    with patch.object(Operations,'create_table',fail):upgrade(directory=str((rt.root/'migrations').resolve()),revision=V18)
                except ValueError:pass
                else:raise AssertionError('Expected migration failure')
                require(bool(rollback),'migration-rollback-unconfirmed')
                rt.preflight(V17)
                with rt.connection() as conn:
                    require(conn.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='user' AND column_name='staff_only'").fetchone()[0]==0,'migration-column-survived')
            else:
                upgrade(directory=str((rt.root/'migrations').resolve()),revision=V18);rt.preflight(V18)
                with rt.connection() as conn:
                    require(conn.execute('SELECT count(*) FROM public."user" WHERE staff_only IS NOT FALSE').fetchone()[0]==0,'legacy-user-changed')
                    for table in STAFF_TABLES:require(conn.execute('SELECT count(*) FROM public.'+table).fetchone()[0]==0,'staff-schema-not-empty')
        finally:event.remove(db.engine,'rollback',mark)
    require(rt.snapshot(original_only=True)==before,'migration-records-changed')


def csrf(client,path):
    data=client.get(path).data
    token=re.search(rb'name="csrf_token"[^>]*value="([^"]+)"',data)
    require(token is not None,'csrf-form-missing');return token[1].decode()


def login(app,email):
    client=app.test_client()
    response=client.post('/auth/login',data=dict(email=email,password=PASSWORD,csrf_token=csrf(client,'/auth/login')))
    require(response.status_code==302,'synthetic-login-failed');return client


def seed_case(rt, case, *, namespace=None):
    from tests.support.postgres_staff_guard import REMAINING_CASES
    require(namespace is None or (type(namespace) is str and (
        (case=='I01' and re.fullmatch(r'i01-cont-[a-f0-9]{8}',namespace))
        or (case in REMAINING_CASES and re.fullmatch(r'staff-cont-[a-f0-9]{8}-'+case.lower(),namespace)))), 'continuation-namespace-rejected')
    label=case if namespace is None else namespace
    from app import db
    from app.models import User,Business,AccountBilling,RecurringSubscription,StaffMembership,StaffMembershipBusiness
    from app.staff import service,roles
    app=rt.app()
    with app.app_context():
        owner=User(full_name='Staff race owner',email=label.lower()+'-owner@example.invalid',email_verified_at=T);owner.set_password(PASSWORD)
        db.session.add(owner);db.session.flush()
        shop=Business(user_id=owner.id,name=label+' shop');db.session.add(shop);db.session.flush()
        db.session.add(AccountBilling(user_id=owner.id,primary_business_id=shop.id))
        sub=RecurringSubscription(user_id=owner.id,business_id=shop.id,plan_code='pro',billing_interval='monthly',amount_kobo=1000000,provider_plan_code='PLN_synthetic',status='active',current_period_start=T-timedelta(days=1),current_period_end=T+timedelta(days=30))
        db.session.add(sub);db.session.commit()
        if case!='R01':
            service.lock_owner(owner.id);service.owner_access(owner,now=T);roles._initialize_locked(owner.id);db.session.commit()
        if case=='R02':roles.save(owner,None,'Fourth',[],now=T);db.session.commit()
        occupied=4 if case in ('I01','I02','F01') else 3 if case in ('A01','A02') else 0
        for n in range(occupied):
            user=User(full_name='Occupied staff',email=f'{label}-{n}@example.invalid',password_hash='synthetic-unused',staff_only=True,email_verified_at=T)
            db.session.add(user);db.session.flush()
            member=StaffMembership(owner_id=owner.id,user_id=user.id,role='salesperson',state='active',duty_label='',grant_event_id=0,grant_started_at=T,grant_pro_start=sub.current_period_start,grant_subscription_id=sub.id,access_until=sub.current_period_end)
            db.session.add(member);db.session.flush();db.session.add(StaffMembershipBusiness(membership_id=member.id,business_id=shop.id))
        if occupied: db.session.commit()  # Starting seats must survive setup context teardown.
        invitation=None;raw=None;invitee=None
        if case not in ('R01','R02','I01','F01','S01'):
            email=label.lower()+'-employee@example.invalid'
            if case=='A01':
                invitee=User(full_name='Existing staff',email=email,email_verified_at=T,staff_only=True);invitee.set_password(PASSWORD);db.session.add(invitee)
            db.session.commit()
            invitation,raw=service.invite(owner,email,'salesperson','',False,[shop.id],now=T);db.session.commit()
        state=dict(owner=owner.id,shop=shop.id,subscription=sub.id,invitation=invitation.id if invitation else None,
                   token=raw,email=owner.email,employee=invitee.email if invitee else None,occupied=occupied)
    # No live ORM object escapes the setup context.
    return state


def state(rt, owner):
    from psycopg.rows import dict_row
    with rt.connection() as conn:
        pid=conn.info.backend_pid;output=dict(pid=pid)
        with conn.cursor(row_factory=dict_row) as c:
            for name in ('staff_role','staff_membership','staff_invitation'):
                c.execute('SELECT * FROM '+name+' WHERE owner_id=%s ORDER BY '+('key' if name=='staff_role' else 'id'),(owner,));output[name]=[dict(row) for row in c.fetchall()]
            c.execute('SELECT * FROM staff_membership_business WHERE membership_id IN (SELECT id FROM staff_membership WHERE owner_id=%s) ORDER BY membership_id,business_id',(owner,));output['memberships_assigned']=[dict(row) for row in c.fetchall()]
            c.execute('SELECT * FROM staff_invitation_business WHERE invitation_id IN (SELECT id FROM staff_invitation WHERE owner_id=%s) ORDER BY invitation_id,business_id',(owner,));output['invitations_assigned']=[dict(row) for row in c.fetchall()]
        output['audit']=conn.execute('SELECT a.action,count(*) FROM audit_log a JOIN staff_audit_event s ON s.audit_log_id=a.id WHERE s.owner_id=%s GROUP BY a.action ORDER BY a.action',(owner,)).fetchall()
        email=conn.execute('SELECT email FROM public."user" WHERE id=%s',(owner,)).fetchone()[0]
        prefix=email.split('-owner@')[0].lower()+'-%'
        output['users']=conn.execute('SELECT id,email,email_verified_at,staff_only FROM public."user" WHERE lower(email) LIKE %s ORDER BY id',(prefix,)).fetchall()
        for table in ('business','account_billing','billing_event'):
            output['identity_'+table]=conn.execute('SELECT b.* FROM '+table+' b JOIN public."user" u ON u.id=b.user_id WHERE lower(u.email) LIKE %s ORDER BY u.id',(prefix,)).fetchall()
        output['seats']=sum(m['state']=='active' for m in output['staff_membership'])+sum(i['state']=='pending' and i['expires_at']>T for i in output['staff_invitation'])
        return output


def comparable(value):return {k:v for k,v in value.items() if k!='pid'}


def race_case(rt, case, *, namespace=None):
    from app import db
    from tests.support.postgres_staff_races import concurrent_posts
    seed=seed_case(rt,case,namespace=namespace);app=rt.app();owner=seed['owner']
    label=case if namespace is None else namespace
    owner_client=login(app,seed['email']); second_owner=login(app,seed['email'])
    def acceptance():
        client=login(app,seed['employee']) if seed['employee'] else app.test_client()
        return client,dict(action='accept',token=seed['token'],full_name='New staff',password=PASSWORD,confirm=PASSWORD,csrf_token=csrf(client,'/staff/invite'))
    def manage(client,**values):return dict(values,csrf_token=csrf(client,'/staff/manage'))
    invite=lambda client,email:manage(client,action='invite',email=email,role='salesperson',business_ids=[seed['shop']])
    if case.startswith('R'):
        clients=[owner_client,second_owner];paths=['/staff/manage']*2
        payloads=[manage(c,action='role_save',role_name='Custom '+str(n)) for n,c in enumerate(clients)]
    elif case in ('I01','F01'):
        clients=[owner_client,second_owner];paths=['/staff/manage']*2
        payloads=[invite(c,f'{label}-{n}-invite@example.invalid') for n,c in enumerate(clients)]
    elif case=='I02':
        c,p=acceptance();clients=[c,owner_client];paths=['/staff/invite','/staff/manage'];payloads=[p,invite(owner_client,(label.lower()+'-extra@example.invalid') if namespace is not None else 'i02-extra@example.invalid')]
    elif case in ('A01','A02','F02'):
        c,p=acceptance();d,q=acceptance();clients=[c,d];paths=['/staff/invite']*2;payloads=[p,q]
    else:
        c,p=acceptance();action='resend' if case in ('X01','X02','F03') else 'role_remove'
        operation=manage(owner_client,action=action,id=seed['invitation']) if action=='resend' else manage(owner_client,action=action,role_key='salesperson',replacement='' if case=='D03' else 'inventory_staff')
        clients=[owner_client,c];paths=['/staff/manage','/staff/invite'];payloads=[operation,p]
        if case in ('X02','D02'):clients.reverse();paths.reverse();payloads.reverse()
    before=state(rt,owner);rollback=case.startswith('F');proof={}
    if namespace is not None:
        occupied=seed['occupied'] if case!='I01' else 4
        pending=0 if case in ('I01','F01') else 1
        require(before['seats']==occupied+pending and len(before['staff_membership'])==occupied
            and len(before['staff_invitation'])==pending, 'continuation-starting-seats-invalid')
    def baseline():
        fresh=state(rt,owner)
        require(fresh['pid'] not in proof['workers']+[proof['observer']],'verifier-backend-not-independent')
        require(comparable(fresh)==comparable(before),'rollback-baseline-changed')
    with app.app_context():
        with rt.connection() as observer:
            statuses=concurrent_posts(db.engine,clients,paths,payloads,observer,owner_id=owner,
                rollback=rollback,baseline_check=baseline if rollback else None,fail_audit_number=2 if case=='F03' else 1,evidence=proof)
    require_staff_statuses(case,statuses,EXPECTED_STATUSES[case])
    after=state(rt,owner)
    require(after['pid'] not in proof['workers']+[proof['observer']],'fresh-backend-not-independent')
    roles=after['staff_role'];members=after['staff_membership'];invitations=after['staff_invitation']
    audits={action:count for action,count in after['audit']};old=dict(before['audit'])
    delta={k:v-old.get(k,0) for k,v in audits.items() if v!=old.get(k,0)}
    if case=='R01':
        require(sum(not r['archived'] for r in roles)==5 and after['seats']==0 and delta=={'STAFF_ROLE_CREATED':5},'role-initialization-count')
    elif case=='R02':require(sum(not r['archived'] for r in roles)==5 and delta=={'STAFF_ROLE_CREATED':1},'fifth-role-count')
    elif case in ('I01','F01'):
        require(len(members)==4 and sum(i['state']=='pending' for i in invitations)==1 and after['seats']==5,'fifth-seat-count')
        require(delta=={'STAFF_INVITED':1,'STAFF_DELIVERY_RESULT':1},'invitation-audit-count')
    elif case=='X01':
        require([i['state'] for i in invitations]==['revoked','pending'] and after['seats']==before['seats'] and not members,'replacement-state')
        require(invitations[0]['token_digest']!=invitations[1]['token_digest'],'replacement-digest')
        require(delta=={'STAFF_INVITE_REPLACED':1,'STAFF_INVITED':1,'STAFF_DELIVERY_RESULT':1},'replacement-audit-count')
    else:
        require(len(members)==seed['occupied']+1 and sum(i['state']=='accepted' for i in invitations)==1
                and not any(i['state']=='pending' for i in invitations) and after['seats']==before['seats'],'acceptance-count')
        wanted={'STAFF_ACCEPTED':1}
        if case in ('D01','D02'):wanted.update(STAFF_ROLE_REASSIGNED=1,STAFF_ROLE_REMOVED=1)
        require(delta==wanted,'acceptance-audit-count')
        member=members[-1]
        require(member['role']==('inventory_staff' if case in ('D01','D02') else 'salesperson'),'accepted-role')
        require(member['access_until']==T+timedelta(days=30) and member['grant_started_at']==T,'grant-changed')
        require([a['business_id'] for a in after['memberships_assigned'] if a['membership_id']==member['id']]==[seed['shop']],'assignment-changed')
        with rt.connection() as conn:
            row=conn.execute('SELECT email_verified_at,staff_only FROM public."user" WHERE id=%s',(member['user_id'],)).fetchone()
            if case!='A01':require(row==(None,True),'new-identity-verification')
            for table,column in (('business','user_id'),('account_billing','user_id'),('billing_event','user_id')):
                require(conn.execute('SELECT count(*) FROM '+table+' WHERE '+column+'=%s',(member['user_id'],)).fetchone()[0]==0,'staff-owner-entity-created')
        if case in ('D01','D02'):
            require(next(r for r in roles if r['key']=='salesperson')['archived'],'role-not-archived')
            require(invitations[0]['role']==('inventory_staff' if case=='D01' else 'salesperson'),'invitation-history-role')
    return after


def schema_case(rt):
    from app import db
    from app.models import StaffRole,StaffMembership,StaffInvitation,StaffMembershipBusiness,StaffAuditEvent,AuditLog,Business,User
    from app.staff import service
    from sqlalchemy import inspect
    from sqlalchemy.exc import IntegrityError,DataError
    seed=seed_case(rt,'S01');app=rt.app()
    with app.app_context():
        inspector=inspect(db.engine)
        for name in STAFF_TABLES:
            table=db.metadata.tables[name];columns={c['name']:c for c in inspector.get_columns(name)}
            require(set(columns)==set(table.c.keys()),'model-columns-differ')
            for col in table.c:
                actual=columns[col.name]
                require(actual['nullable']==col.nullable and str(actual['type'].compile(dialect=db.engine.dialect))==str(col.type.compile(dialect=db.engine.dialect)),'model-type-differs')
            expected={tuple(c.name for c in constraint.columns) for constraint in table.constraints if constraint.__class__.__name__=='UniqueConstraint'}
            require(expected=={tuple(c['column_names']) for c in inspector.get_unique_constraints(name)},'model-unique-differs')
            require({c.name for c in table.constraints if c.__class__.__name__=='CheckConstraint'}=={c['name'] for c in inspector.get_check_constraints(name)},'model-check-differs')
            expected_foreign={(tuple(f.column_keys),tuple(e.column.table.name for e in f.elements),tuple(e.column.name for e in f.elements)) for f in table.foreign_key_constraints}
            actual_foreign={(tuple(f['constrained_columns']),tuple(f['referred_table'] for _ in f['referred_columns']),tuple(f['referred_columns'])) for f in inspector.get_foreign_keys(name)}
            require(expected_foreign==actual_foreign,'model-foreign-keys-differ')
            require(tuple(c.name for c in table.primary_key.columns)==tuple(inspector.get_pk_constraint(name)['constrained_columns']),'model-primary-key-differs')
            require({i.name for i in table.indexes}<={i['name'] for i in inspector.get_indexes(name)},'model-index-differs')
        staff_only=next(c for c in inspector.get_columns('user') if c['name']=='staff_only')
        require(staff_only['nullable'] is False and staff_only['default'] in ('false','false::boolean'),'staff-identity-default-differs')
        user=User(full_name='Schema staff',email='s01-employee@example.invalid',password_hash='unused',staff_only=True);db.session.add(user);db.session.flush()
        owner=db.session.get(User,seed['owner'])
        from app.staff import roles
        custom=roles.save(owner,None,'N'*80,list(roles.CATALOGUE),now=T)
        extra=[Business(user_id=owner.id,name='Schema extra '+str(n)) for n in range(4)]
        db.session.add_all(extra);db.session.flush()
        member=StaffMembership(owner_id=seed['owner'],user_id=user.id,role=custom.key,duty_label='X'*100,state='active',grant_event_id=2147483647,grant_started_at=T,grant_pro_start=T-timedelta(days=1),grant_subscription_id=seed['subscription'],access_until=datetime.max)
        db.session.add(member);db.session.flush();db.session.add(StaffMembershipBusiness(membership_id=member.id,business_id=seed['shop']))
        for shop in extra:db.session.add(StaffMembershipBusiness(membership_id=member.id,business_id=shop.id))
        service.audit(seed['owner'],seed['owner'],'STAFF_SCHEMA_MAX',membership=member);db.session.commit()
        values={c.name:getattr(member,c.name) for c in member.__table__.c if c.name!='id'}
        invalid_users=[User(full_name='Constraint identity',email=f'schema-invalid-{n}@example.invalid',password_hash='unused',staff_only=True) for n in range(4)]
        foreign=User(full_name='Foreign schema owner',email='schema-foreign@example.invalid',password_hash='unused')
        db.session.add_all(invalid_users+[foreign]);db.session.flush()
        db.session.add(StaffRole(owner_id=foreign.id,key='f'*32,name='Foreign',permissions=[],archived=False));db.session.commit()
        def rejected_insert(table,values,code):
            try:
                with db.engine.begin() as connection:connection.execute(table.insert().values(**values))
            except (IntegrityError,DataError) as error:
                require(error.orig.sqlstate==code,'wrong-constraint-rejection')
            else:raise AssertionError('Staff constraint accepted invalid data')
        variants=[({},'23505'),({'user_id':seed['owner']},'23514'),
            ({'user_id':invalid_users[0].id,'state':'invalid'},'23514'),
            ({'user_id':invalid_users[1].id,'role':'f'*32},'23503'),
            ({'user_id':invalid_users[2].id,'duty_label':'X'*101},'22001')]
        for changes,code in variants:rejected_insert(StaffMembership.__table__,dict(values,**changes),code)
        invitation,_=service.invite(owner,'schema-pending@example.invalid','salesperson','',False,[seed['shop']],now=T);db.session.commit()
        original={c.name:getattr(invitation,c.name) for c in invitation.__table__.c if c.name!='id'}
        rejected_insert(StaffInvitation.__table__,original,'23505')
        for field in ('state','delivery_status'):
            revised=dict(original,token_digest=hashlib.sha256(field.encode()).hexdigest());revised[field]='invalid'
            rejected_insert(StaffInvitation.__table__,revised,'23514')
    with rt.connection() as conn:
        row=conn.execute("SELECT s.details,a.description FROM staff_audit_event s JOIN audit_log a ON a.id=s.audit_log_id WHERE a.action='STAFF_SCHEMA_MAX'").fetchone()
        require(isinstance(row[0],dict) and len(json.dumps(row[0]))>300 and len(row[1])<=300,'structured-audit-persistence')
        require(conn.execute("SELECT description FROM audit_log WHERE action='PRESERVATION'").fetchone()[0]=='X'*300,'legacy-audit-changed')


def execute_case(rt, case):
    rt.guards.check_clean();rt.check_source()
    with rt.environment():
        if case in ('M00','M01'):
            migration_case(rt,case)  # Exactly one pre + one post preflight.
        else:
            rt.preflight(rt.auth['schema_before'])
            if case=='S01':schema_case(rt)
            else:race_case(rt,case)
            import threading
            require(not any(t.name.startswith('stockbridge-race-') and t.is_alive() for t in threading.enumerate()),'shutdown-unconfirmed')
            rt.guards.check_clean();rt.preflight(rt.auth['schema_after'])
