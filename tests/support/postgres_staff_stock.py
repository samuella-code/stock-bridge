"""Three bounded stock families; inert until the separately authorized launcher."""
from datetime import datetime, timedelta
from decimal import Decimal
import re
from unittest.mock import patch
from tests.support.postgres_staff_guard import V18, STOCK_CASES, require
from tests.support.postgres_staff_i01_continuation import checked_snapshot, digest
from tests.support.postgres_staff_sales import require_preserved


def namespace(auth):
    value=auth.get('continuation_namespace')
    require(type(value) is str and re.fullmatch(r'stock-cont-[a-f0-9]{8}',value),'stock-namespace-rejected')
    return value


def require_unused(rows,label):
    require(not any(type(row.get('email')) is str and row['email'].startswith(label+'-')
                    for row in rows.get('user',[])),'stock-namespace-used')


def inspect(rt,report=None):
    require(rt.auth.get('mode')=='stock-state' and rt.auth.get('cases')==[], 'stock-mode-rejected')
    label=namespace(rt.auth)
    rt.guards.check_clean();rt.check_source();rt.preflight(V18)
    rows=checked_snapshot(rt);require_unused(rows,label)
    rt.guards.check_clean();rt.check_source()
    return dict(schema=V18,starting_state_sha256=digest(rows),continuation_namespace=label,
                namespace_unused=True,rows=sum(map(len,rows.values())),cases=list(STOCK_CASES))


def application(rt):
    from tests.support.postgres_staff_fixtures import T
    app=rt.app()
    require(rt.auth['mode']=='stock','stock-write-mode-required')
    app.config['STAFF_STOCK_ENABLED']='true'  # This isolated process only.
    class Clock(datetime):
        @classmethod
        def utcnow(cls):return T
    if not getattr(rt,'stock_clocks_installed',False):
        for module in ('app.staff.stock_access','app.restocking.ledger',
                       'app.subscriptions.entitlements'):
            rt.stack.enter_context(patch(module+'.datetime',Clock))
        rt.stock_clocks_installed=True
    return app


def seed(rt,label):
    from tests.support.postgres_staff_fixtures import T,PASSWORD
    from app import db
    from app.models import (User,Business,AccountBilling,RecurringSubscription,Product,
                            StaffMembership,StaffMembershipBusiness)
    from app.staff import service,roles
    app=application(rt)
    with app.app_context():
        owner=User(full_name='Synthetic stock owner',email=label+'-owner@example.invalid',email_verified_at=T)
        staff=User(full_name='Synthetic stock staff',email=label+'-staff@example.invalid',email_verified_at=T,staff_only=True)
        owner.set_password(PASSWORD);staff.set_password(PASSWORD)
        db.session.add_all([owner,staff]);db.session.flush()
        shop=Business(user_id=owner.id,name=label+' shop');db.session.add(shop);db.session.flush()
        db.session.add(AccountBilling(user_id=owner.id,primary_business_id=shop.id))
        sub=RecurringSubscription(user_id=owner.id,business_id=shop.id,plan_code='pro',billing_interval='monthly',
            amount_kobo=1000000,provider_plan_code='PLN_synthetic',status='active',
            current_period_start=T-timedelta(days=1),current_period_end=T+timedelta(days=30))
        db.session.add(sub);db.session.flush();service.lock_owner(owner.id);roles._initialize_locked(owner.id)
        member=StaffMembership(owner_id=owner.id,user_id=staff.id,role='inventory_staff',state='active',duty_label='',
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
    require(response.status_code==200,'stock-form-rejected')
    output={}
    for name in ('csrf_token','submission_token'):
        match=re.search(('name="'+name+'"[^>]*value="([^"]+)"').encode(),response.data)
        require(match is not None,'stock-form-reference-missing');output[name]=match[1].decode()
    return output


def assert_result(rt,data,variant,statuses):
    expected={'same':[303,200],'owner-sale':[303,302],'owner-restock':[303,302],'revoke':[302,403]}
    require(statuses==expected[variant],'stock-worker-status-rejected')
    with rt.connection() as conn:
        restocks=conn.execute('SELECT id,quantity,unit_cost,batch_id FROM public.restock WHERE business_id=%s ORDER BY id',(data['shop'],)).fetchall()
        stock,cost=conn.execute('SELECT stock_quantity,buying_price FROM public.product WHERE id=%s',(data['product'],)).fetchone()
        receipts=conn.execute('SELECT user_id,operation,result_reference,completed_at FROM public.transaction_submission WHERE business_id=%s',(data['shop'],)).fetchall()
        audits=conn.execute("SELECT actor_id FROM public.audit_log WHERE business_id=%s AND action='RESTOCK_CREATED' ORDER BY id",(data['shop'],)).fetchall()
        moves=conn.execute('SELECT kind,quantity_change FROM public.stock_movement WHERE business_id=%s ORDER BY id',(data['shop'],)).fetchall()
        sales=conn.execute('SELECT id FROM public.sale WHERE business_id=%s',(data['shop'],)).fetchall()
        membership=conn.execute('SELECT state FROM public.staff_membership WHERE id=%s',(data['member'],)).fetchone()[0]
        if variant=='revoke':
            require(not restocks and not receipts and not audits and not moves and not sales
                    and stock==1 and cost==8 and membership=='revoked','stock-after-revocation-mutated')
            return
        final_stock={'same':3,'owner-sale':2,'owner-restock':4}[variant]
        require(stock==final_stock and cost==(8 if variant=='owner-restock' else Decimal('12.50')),'stock-final-balance-rejected')
        require(len(restocks)==(2 if variant=='owner-restock' else 1),'stock-receipt-count-rejected')
        first=restocks[0]
        require(first[1:3]==(2,Decimal('12.50')) and bool(first[3]),'stock-line-snapshot-rejected')
        require(audits==([(data['staff'],),(data['owner'],)] if variant=='owner-restock' else [(data['staff'],)]),'stock-actor-attribution-rejected')
        expected_count=1 if variant=='same' else 2
        require(len(receipts)==expected_count and all(row[2] and row[3] for row in receipts),'stock-submission-completion-rejected')
        require(sum(uid==data['staff'] and op=='restock' and ref==first[3] for uid,op,ref,_ in receipts)==1,'stock-receipt-actor-rejected')
        if variant=='same':
            require(moves==[('restock',2)] and not sales,'stock-duplicate-mutated')
        elif variant=='owner-restock':
            require(restocks[1][1:3]==(1,8) and restocks[1][3]!=first[3] and not sales
                    and moves==[('restock',2),('restock',1)],'stock-owner-restock-rejected')
            require(any(uid==data['owner'] and op=='restock' and ref==restocks[1][3] for uid,op,ref,_ in receipts),'stock-owner-receipt-rejected')
        else:
            require(len(sales)==1 and moves==[('restock',2),('sale',-1)],'stock-owner-sale-rejected')
            sale=sales[0][0]
            items=conn.execute('SELECT product_id,quantity,unit_price,unit_cost FROM public.sale_item WHERE sale_id=%s',(sale,)).fetchall()
            require(items==[(data['product'],1,20,Decimal('12.50'))],'stock-sale-cost-snapshot-rejected')
            sale_audit=conn.execute("SELECT actor_id,target_id FROM public.audit_log WHERE business_id=%s AND action='SALE_CREATED'",(data['shop'],)).fetchall()
            require(sale_audit==[(data['owner'],sale)] and any(uid==data['owner'] and op=='sale' and ref=='sale:'+str(sale) for uid,op,ref,_ in receipts),'stock-sale-actor-rejected')


def race(rt,label,variant):
    from tests.support.postgres_staff_fixtures import login,csrf
    from tests.support.postgres_staff_stock_races import stock_paths,concurrent_stock_posts
    from app import db
    data=seed(rt,label);app=rt.app()
    staff_path=stock_paths('same',data['member'],data['shop'])[0]
    first=login(app,data['owner_email'] if variant=='revoke' else data['staff_email'])
    second=login(app,data['staff_email'] if variant in ('same','revoke') else data['owner_email'])
    payload=dict(product_id=str(data['product']),quantity='2',unit_cost='12.50',note='Synthetic stock race')
    first_payload=dict(payload,**form(first,staff_path)) if variant!='revoke' else dict(
        action='revoke',kind='membership',id=str(data['member']),csrf_token=csrf(first,'/staff/manage'))
    if variant=='same':second_payload=dict(first_payload,csrf_token=form(second,staff_path)['csrf_token'])
    elif variant=='revoke':second_payload=dict(payload,**form(second,staff_path))
    elif variant=='owner-sale':
        second_payload=dict(product_id=str(data['product']),quantity='1',payment_method='Cash',note='Synthetic owner sale',**form(second,'/sales/'))
    else:
        second_payload=dict(product_id=str(data['product']),quantity='1',unit_cost='8.00',note='Synthetic owner restock',**form(second,'/restocking/'))
    evidence={}
    with app.app_context(),rt.connection() as observer:
        statuses=concurrent_stock_posts(db.engine,[first,second],stock_paths(variant,data['member'],data['shop']),
            [first_payload,second_payload],observer,owner_id=data['owner'],membership_id=data['member'],
            business_id=data['shop'],variant=variant,evidence=evidence)
    assert_result(rt,data,variant,statuses)
    return dict(variant=variant,status='PASS',independent_backends=True,owner_lock_contention_observed=True)


def run(rt,report):
    require(rt.auth.get('mode')=='stock' and rt.auth.get('cases')==list(STOCK_CASES),'stock-selection-rejected')
    rt.guards.check_clean();rt.check_source();rt.preflight(V18)
    current=checked_snapshot(rt);base=namespace(rt.auth)
    require(digest(current)==rt.auth.get('starting_state_sha256'),'stock-starting-state-differs')
    require_unused(current,base)
    variants={'STK01':('same',),'STK02':('owner-sale','owner-restock'),'STK03':('revoke',)}
    for case in STOCK_CASES:
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
    return dict(status='PASS',cases=list(STOCK_CASES),variants=4,existing_records_preserved=True,
                continuation_namespace=base,end_state_sha256=digest(current))
