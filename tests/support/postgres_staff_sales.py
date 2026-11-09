"""Three bounded sales families; inert until the separately authorized launcher."""
from collections import Counter
from datetime import datetime, timedelta
import re
from unittest.mock import patch
from tests.support.postgres_staff_guard import V18, SALES_CASES, require
from tests.support.postgres_staff_i01_continuation import checked_snapshot, digest, encode


def namespace(auth):
    value=auth.get('continuation_namespace')
    require(type(value) is str and re.fullmatch(r'sales-cont-[a-f0-9]{8}',value),'sales-namespace-rejected')
    return value


def require_unused(rows,label):
    require(not any(type(row.get('email')) is str and row['email'].startswith(label+'-')
                    for row in rows.get('user',[])),'sales-namespace-used')


def inspect(rt,report=None):
    require(rt.auth.get('mode')=='sales-state' and rt.auth.get('cases')==[], 'sales-mode-rejected')
    label=namespace(rt.auth)
    rt.guards.check_clean();rt.check_source();rt.preflight(V18)
    rows=checked_snapshot(rt);require_unused(rows,label)
    rt.guards.check_clean();rt.check_source()
    return dict(schema=V18,starting_state_sha256=digest(rows),continuation_namespace=label,
                namespace_unused=True,rows=sum(map(len,rows.values())),cases=list(SALES_CASES))


def require_preserved(before,after,label,*,allow_empty=False):
    if allow_empty and before==after:return
    require(set(before)==set(after),'sales-table-inventory-changed')
    additions={}
    for table,original in before.items():
        old=Counter(map(encode,original));new=Counter(map(encode,after[table]))
        require(not old-new,'sales-earlier-record-changed')
        remaining=new-old;additions[table]=[]
        for row in after[table]:
            key=encode(row)
            if remaining[key]:additions[table].append(row);remaining[key]-=1
    users=additions.get('user',[])
    require({u.get('email') for u in users}=={label+'-owner@example.invalid',label+'-staff@example.invalid'}
            and len(users)==2,'sales-added-user-scope')
    owner=next(u['id'] for u in users if u['email']==label+'-owner@example.invalid')
    actor_ids={u['id'] for u in users}
    shops=additions.get('business',[])
    require(len(shops)==1 and shops[0].get('user_id')==owner,'sales-added-business-scope')
    business=shops[0]['id']
    members={m['id'] for m in additions.get('staff_membership',[])}
    products={p['id'] for p in additions.get('product',[])}
    audits={a['id'] for a in additions.get('audit_log',[])}
    sales={s['id'] for s in additions.get('sale',[])}
    checks={
        'user':lambda row:row.get('id') in actor_ids,
        'business':lambda row:row.get('id')==business,
        'account_billing':lambda row:row.get('user_id')==owner,
        'recurring_subscription':lambda row:row.get('user_id')==owner and row.get('business_id')==business,
        'staff_role':lambda row:row.get('owner_id')==owner,
        'staff_membership':lambda row:row.get('owner_id')==owner and row.get('user_id') in actor_ids-{owner},
        'staff_membership_business':lambda row:row.get('membership_id') in members and row.get('business_id')==business,
        'staff_audit_event':lambda row:row.get('owner_id')==owner and row.get('audit_log_id') in audits,
        'audit_log':lambda row:row.get('actor_id') in actor_ids and row.get('business_id') in (None,business),
        'product':lambda row:row.get('business_id')==business,
        'sale':lambda row:row.get('business_id')==business,
        'sale_item':lambda row:row.get('sale_id') in sales and row.get('product_id') in products,
        'stock_movement':lambda row:row.get('business_id')==business and row.get('product_id') in products,
        'restock':lambda row:row.get('business_id')==business and row.get('product_id') in products,
        'transaction_submission':lambda row:row.get('business_id')==business and row.get('user_id') in actor_ids,
        'notification':lambda row:row.get('business_id')==business,
        'notification_preference':lambda row:row.get('business_id')==business,
        'email_outbox':lambda row:row.get('business_id')==business,
    }
    for table,rows in additions.items():
        require(not rows or table in checks and all(checks[table](row) for row in rows),'sales-added-row-outside-scope')


def application(rt):
    from tests.support.postgres_staff_fixtures import T
    app=rt.app()
    require(rt.auth['mode']=='sales','sales-write-mode-required')
    app.config['STAFF_SALES_ENABLED']='true'  # This isolated process only.
    class Clock(datetime):
        @classmethod
        def utcnow(cls):return T
    if not getattr(rt,'sales_clocks_installed',False):
        for module in ('app.staff.sales_access','app.staff.sales_routes','app.sales.ledger',
                       'app.subscriptions.entitlements'):
            rt.stack.enter_context(patch(module+'.datetime',Clock))
        rt.sales_clocks_installed=True
    return app


def seed(rt,label):
    from tests.support.postgres_staff_fixtures import T,PASSWORD
    from app import db
    from app.models import (User,Business,AccountBilling,RecurringSubscription,Product,
                            StaffMembership,StaffMembershipBusiness)
    from app.staff import service,roles
    app=application(rt)
    with app.app_context():
        owner=User(full_name='Synthetic sales owner',email=label+'-owner@example.invalid',email_verified_at=T)
        staff=User(full_name='Synthetic sales staff',email=label+'-staff@example.invalid',email_verified_at=T,staff_only=True)
        owner.set_password(PASSWORD);staff.set_password(PASSWORD)
        db.session.add_all([owner,staff]);db.session.flush()
        shop=Business(user_id=owner.id,name=label+' shop');db.session.add(shop);db.session.flush()
        db.session.add(AccountBilling(user_id=owner.id,primary_business_id=shop.id))
        sub=RecurringSubscription(user_id=owner.id,business_id=shop.id,plan_code='pro',billing_interval='monthly',
            amount_kobo=1000000,provider_plan_code='PLN_synthetic',status='active',
            current_period_start=T-timedelta(days=1),current_period_end=T+timedelta(days=30))
        db.session.add(sub);db.session.flush();service.lock_owner(owner.id);roles._initialize_locked(owner.id)
        member=StaffMembership(owner_id=owner.id,user_id=staff.id,role='salesperson',state='active',duty_label='',
            grant_event_id=0,grant_started_at=T,grant_pro_start=sub.current_period_start,
            grant_subscription_id=sub.id,access_until=sub.current_period_end)
        db.session.add(member);db.session.flush()
        db.session.add(StaffMembershipBusiness(membership_id=member.id,business_id=shop.id))
        item=Product(business_id=shop.id,name=label+' product',stock_quantity=1,opening_quantity=1,
                     selling_price=20,buying_price=8)
        db.session.add(item);db.session.flush()
        data=dict(owner=owner.id,staff=staff.id,shop=shop.id,member=member.id,product=item.id,
                  owner_email=owner.email,staff_email=staff.email)
        db.session.commit()
    return data


def form(client,path):
    response=client.get(path)
    require(response.status_code==200,'sales-form-rejected')
    output={}
    for name in ('csrf_token','submission_token'):
        match=re.search(('name="'+name+'"[^>]*value="([^"]+)"').encode(),response.data)
        require(match is not None,'sales-form-reference-missing');output[name]=match[1].decode()
    return output


def assert_result(rt,data,variant,statuses):
    expected={'same':[303,200],'owner-sale':[303,200],'owner-restock':[303,302],'revoke':[302,403]}
    require(statuses==expected[variant],'sales-worker-status-rejected')
    # Fresh read-only backend after both workers have committed/rolled back.
    with rt.connection() as conn:
        rows=conn.execute('SELECT id FROM public.sale WHERE business_id=%s',(data['shop'],)).fetchall()
        stock=conn.execute('SELECT stock_quantity FROM public.product WHERE id=%s',(data['product'],)).fetchone()[0]
        receipts=conn.execute('SELECT user_id,operation,result_reference,completed_at FROM public.transaction_submission WHERE business_id=%s',(data['shop'],)).fetchall()
        audits=conn.execute("SELECT actor_id,target_id FROM public.audit_log WHERE business_id=%s AND action='SALE_CREATED'",(data['shop'],)).fetchall()
        moves=conn.execute('SELECT kind,quantity_change FROM public.stock_movement WHERE business_id=%s',(data['shop'],)).fetchall()
        membership=conn.execute('SELECT state FROM public.staff_membership WHERE id=%s',(data['member'],)).fetchone()[0]
        if variant=='revoke':
            require(not rows and not receipts and not audits and not moves and stock==1 and membership=='revoked','sales-after-revocation-mutated')
        else:
            require(len(rows)==1 and stock==(1 if variant=='owner-restock' else 0),'sales-count-or-stock-rejected')
            sale=rows[0][0]
            items=conn.execute('SELECT product_id,quantity,unit_price,unit_cost FROM public.sale_item WHERE sale_id=%s',(sale,)).fetchall()
            require(items==[(data['product'],1,20,8)],'sales-line-snapshot-rejected')
            require(audits==[(data['staff'],sale)],'sales-actor-attribution-rejected')
            require(len(receipts)==(2 if variant=='owner-restock' else 1)
                    and all(row[2] and row[3] for row in receipts),'sales-receipt-completion-rejected')
            require(sum(kind=='sale' and delta==-1 for kind,delta in moves)==1,'sales-movement-repeated')
            require(any(uid==data['staff'] and op=='sale' and ref=='sale:'+str(sale) for uid,op,ref,_ in receipts),'sales-receipt-actor-rejected')
            if variant=='owner-restock':
                require(sum(kind=='restock' and delta==1 for kind,delta in moves)==1,'sales-restock-movement-rejected')
                require(len(moves)==2 and any(uid==data['owner'] and op=='restock' for uid,op,_,_ in receipts),'sales-restock-receipt-rejected')
            else:require(len(moves)==1,'sales-unexpected-movement')


def race(rt,label,variant):
    from tests.support.postgres_staff_fixtures import login,csrf
    from tests.support.postgres_staff_sales_races import sales_paths,concurrent_sales_posts
    from app import db
    data=seed(rt,label);app=rt.app()
    staff_path=sales_paths('same',data['member'],data['shop'])[0]
    first=login(app,data['owner_email'] if variant=='revoke' else data['staff_email'])
    second=login(app,data['staff_email'] if variant in ('same','revoke') else data['owner_email'])
    payload=dict(product_id=str(data['product']),quantity='1',payment_method='Cash',note='Synthetic race')
    first_payload=dict(payload,**form(first,staff_path)) if variant!='revoke' else dict(
        action='revoke',kind='membership',id=str(data['member']),csrf_token=csrf(first,'/staff/manage'))
    if variant=='same':second_payload=dict(first_payload,csrf_token=form(second,staff_path)['csrf_token'])
    elif variant=='revoke':second_payload=dict(payload,**form(second,staff_path))
    else:
        owner_form='/sales/' if variant=='owner-sale' else '/restocking/'
        second_payload=dict(payload,**form(second,owner_form))
        if variant=='owner-restock':
            second_payload.pop('payment_method');second_payload['unit_cost']='8.00'
    evidence={}
    with app.app_context(),rt.connection() as observer:
        statuses=concurrent_sales_posts(db.engine,[first,second],sales_paths(variant,data['member'],data['shop']),
            [first_payload,second_payload],observer,owner_id=data['owner'],membership_id=data['member'],
            business_id=data['shop'],variant=variant,evidence=evidence)
    assert_result(rt,data,variant,statuses)
    return dict(variant=variant,status='PASS',independent_backends=True,owner_lock_contention_observed=True)


def run(rt,report):
    require(rt.auth.get('mode')=='sales' and rt.auth.get('cases')==list(SALES_CASES),'sales-selection-rejected')
    rt.guards.check_clean();rt.check_source();rt.preflight(V18)
    current=checked_snapshot(rt);base=namespace(rt.auth)
    require(digest(current)==rt.auth.get('starting_state_sha256'),'sales-starting-state-differs')
    require_unused(current,base)
    variants={'SLS01':('same',),'SLS02':('owner-sale','owner-restock'),'SLS03':('revoke',)}
    for case in SALES_CASES:
        report('RUNNING',dict(case=case))
        for variant in variants[case]:
            label=base+'-'+case.lower()+'-'+variant
            require_unused(current,label)
            try:
                result=race(rt,label,variant)
            except BaseException:
                rt.guards.check_clean();rt.check_source()
                require_preserved(current,checked_snapshot(rt),label,allow_empty=True)
                raise  # No next variant, automatic retry or cleanup.
            rt.guards.check_clean();rt.check_source()
            after=checked_snapshot(rt);require_preserved(current,after,label)
            rt.preflight(V18);current=after
            report('VARIANT',dict(case=case,existing_records_preserved=True,**result))
    return dict(status='PASS',cases=list(SALES_CASES),variants=4,existing_records_preserved=True,
                continuation_namespace=base,end_state_sha256=digest(current))
