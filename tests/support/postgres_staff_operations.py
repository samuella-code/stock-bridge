"""Inert, state-bound product/expense races and scoped financial accounting check."""
from datetime import datetime,timedelta
from decimal import Decimal
import re
from unittest.mock import patch
from tests.support.postgres_staff_guard import V18,OPERATIONS_CASES,require
from tests.support.postgres_staff_i01_continuation import checked_snapshot,digest
from collections import Counter
from tests.support.postgres_staff_i01_continuation import encode

VARIANTS={'OPS01':('create',),'OPS02':('edit','stale'),'OPS03':('expense',),
          'OPS04':('revoke-product','revoke-expense','report')}


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
    expected_names={label+' shop',label+' unassigned'} if label.endswith('-report') else {label+' shop'}
    require({b.get('name') for b in shops}==expected_names and len(shops)==len(expected_names)
            and all(b.get('user_id')==owner for b in shops),'operations-added-business-scope')
    business=next(b['id'] for b in shops if b['name']==label+' shop')
    expense_businesses={b['id'] for b in shops}
    members={m['id'] for m in additions.get('staff_membership',[])}
    products={p['id'] for p in additions.get('product',[])}
    audits={a['id'] for a in additions.get('audit_log',[])}
    sales={s['id'] for s in additions.get('sale',[])}
    checks={
        'user':lambda row:row.get('id') in actor_ids,
        'business':lambda row:row.get('id') in expense_businesses,
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
        'expense':lambda row:row.get('business_id') in expense_businesses,
        'restock':lambda row:row.get('business_id')==business and row.get('product_id') in products,
        'transaction_submission':lambda row:row.get('business_id')==business and row.get('user_id') in actor_ids,
        'notification':lambda row:row.get('business_id')==business,
        'notification_preference':lambda row:row.get('business_id')==business,
        'email_outbox':lambda row:row.get('business_id')==business,
    }
    for table,rows in additions.items():
        require(not rows or table in checks and all(checks[table](row) for row in rows),'sales-added-row-outside-scope')


def namespace(auth):
    value=auth.get('continuation_namespace')
    require(type(value) is str and re.fullmatch(r'ops-cont-[a-f0-9]{8}',value),'operations-namespace-rejected')
    return value


def require_unused(rows,label):
    require(not any(type(row.get('email')) is str and row['email'].startswith(label+'-')
                    for row in rows.get('user',[])),'operations-namespace-used')


def inspect(rt,report=None):
    require(rt.auth.get('mode')=='operations-state' and rt.auth.get('cases')==[],'operations-mode-rejected')
    label=namespace(rt.auth);rt.guards.check_clean();rt.check_source();rt.preflight(V18)
    rows=checked_snapshot(rt);require_unused(rows,label);rt.guards.check_clean();rt.check_source()
    return dict(schema=V18,starting_state_sha256=digest(rows),continuation_namespace=label,
                namespace_unused=True,rows=sum(map(len,rows.values())),cases=list(OPERATIONS_CASES))


def seed(rt,label):
    require(rt.auth.get('mode')=='operations','operations-write-mode-required')
    from tests.support.postgres_staff_fixtures import T,PASSWORD
    from app import db
    from app.models import (User,Business,AccountBilling,RecurringSubscription,Product,
                            StaffMembership,StaffMembershipBusiness)
    from app.staff import service,roles
    app=rt.app();app.config['STAFF_OPERATIONS_ENABLED']='true'
    class Clock(datetime):
        @classmethod
        def utcnow(cls):return T
    if not getattr(rt,'operations_clocks_installed',False):
        for module in ('app.staff.operations_access','app.staff.operations_routes','app.expenses.ledger','app.subscriptions.entitlements'):
            rt.stack.enter_context(patch(module+'.datetime',Clock))
        rt.operations_clocks_installed=True
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
        member=StaffMembership(owner_id=owner.id,user_id=staff.id,role='manager',state='active',duty_label='',
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
    response=client.get(path);require(response.status_code==200,'operations-form-rejected')
    output={}
    for name in ('csrf_token','submission_token'):
        match=re.search(('name="'+name+'"[^>]*value="([^"]+)"').encode(),response.data)
        require(match is not None,'operations-form-reference-missing');output[name]=match[1].decode()
    return output


def validate_result(data,variant,statuses,ledger):
    require(variant in ('create','edit','stale','expense','revoke-product','revoke-expense'),'operations-variant-rejected')
    expected=[302,403] if variant.startswith('revoke-') else [303,409] if variant=='stale' else [303,200]
    require(statuses==expected,'operations-worker-status-rejected')
    products=ledger['products'];receipts=ledger['receipts'];audits=ledger['audits']
    expenses=ledger['expenses'];moves=ledger['moves']
    original=next((row for row in products if row[0]==data['product']),None)
    require(original is not None,'operations-original-product-missing')
    require(original[1:4]==(1,1,Decimal('8')),'operations-protected-stock-cost-rejected')
    require(ledger['sales']==[],'operations-unexpected-sale')
    if variant.startswith('revoke-'):
        require(ledger['membership']=='revoked' and len(products)==1 and not receipts and not expenses
                and not audits and not moves and original[4]==Decimal('20'),'operations-revocation-rejected')
        return
    require(ledger['membership']=='active','operations-membership-rejected')
    require(len(receipts)==1 and receipts[0][0]==data['staff'] and receipts[0][3] is not None,'operations-receipt-rejected')
    action='EXPENSE_CREATED' if variant=='expense' else 'PRODUCT_CREATED' if variant=='create' else 'PRODUCT_UPDATED'
    require(audits==[(data['staff'],action)],'operations-actor-rejected')
    operation='expense' if variant=='expense' else 'product_create' if variant=='create' else 'product_edit'
    require(receipts[0][1]==operation,'operations-receipt-operation-rejected')
    if variant in ('expense','create'):require(original[4]==Decimal('20'),'operations-unrelated-price-rejected')
    if variant=='expense':
        require(len(products)==1 and not moves and len(expenses)==1 and expenses[0][1]==Decimal('3.00'), 'operations-expense-rejected')
        reference='expense:'+str(expenses[0][0])
    elif variant=='create':
        added=[row for row in products if row[0]!=data['product']]
        require(len(added)==1 and len(products)==2 and added[0][1:5]==(2,2,Decimal('12.50'),Decimal('30'))
                and moves==[('opening',2)] and not expenses,'operations-opening-rejected')
        reference='product:'+str(added[0][0])
    else:
        require(len(products)==1 and original[4]==Decimal('30') and not expenses and not moves,'operations-edit-rejected')
        reference='product:'+str(data['product'])
    require(receipts[0][2]==reference,'operations-result-reference-rejected')


def assert_result(rt,data,variant,statuses):
    with rt.connection() as conn:
        ledger=dict(
            products=conn.execute('SELECT id,stock_quantity,opening_quantity,buying_price,selling_price FROM public.product WHERE business_id=%s ORDER BY id',(data['shop'],)).fetchall(),
            receipts=conn.execute('SELECT user_id,operation,result_reference,completed_at FROM public.transaction_submission WHERE business_id=%s ORDER BY id',(data['shop'],)).fetchall(),
            audits=conn.execute("SELECT actor_id,action FROM public.audit_log WHERE business_id=%s AND action IN ('PRODUCT_CREATED','PRODUCT_UPDATED','EXPENSE_CREATED') ORDER BY id",(data['shop'],)).fetchall(),
            expenses=conn.execute('SELECT id,amount FROM public.expense WHERE business_id=%s ORDER BY id',(data['shop'],)).fetchall(),
            moves=conn.execute('SELECT kind,quantity_change FROM public.stock_movement WHERE business_id=%s ORDER BY id',(data['shop'],)).fetchall(),
            sales=conn.execute('SELECT id FROM public.sale WHERE business_id=%s',(data['shop'],)).fetchall(),
            membership=conn.execute('SELECT state FROM public.staff_membership WHERE id=%s',(data['member'],)).fetchone()[0])
    validate_result(data,variant,statuses,ledger)


def race(rt,label,variant):
    from tests.support.postgres_staff_fixtures import login,csrf
    from tests.support.postgres_staff_operations_races import operations_paths,concurrent_operations_posts
    from app import db
    data=seed(rt,label);app=rt.app();paths=operations_paths(variant,data['member'],data['shop'],data['product'])
    first=login(app,data['owner_email'] if variant.startswith('revoke-') else data['staff_email'])
    second=login(app,data['staff_email'])
    staff_path=paths[1]
    payload=(dict(description='Synthetic expense',category='Miscellaneous',amount='3.00',note='') if variant in ('expense','revoke-expense')
             else dict(name=label+' new product',sku='',category='',unit='unit',supplier_name='',description='',
                       selling_price='30.00',minimum_stock_level='0',supplier_lead_time='2',safety_stock='0'))
    if variant=='create':payload.update(buying_price='12.50',stock_quantity='2')
    if variant.startswith('revoke-'):
        first_payload=dict(action='revoke',kind='membership',id=str(data['member']),csrf_token=csrf(first,'/staff/manage'))
        second_payload=dict(payload,**form(second,staff_path))
    else:
        first_payload=dict(payload,**form(first,staff_path))
        second_form=form(second,staff_path)
        second_payload=dict(first_payload,csrf_token=second_form['csrf_token'])
        if variant=='stale':second_payload.update(second_form,name=label+' competing edit')
    evidence={}
    with app.app_context(),rt.connection() as observer:
        statuses=concurrent_operations_posts(db.engine,[first,second],paths,[first_payload,second_payload],observer,
            owner_id=data['owner'],membership_id=data['member'],business_id=data['shop'],product_id=data['product'],
            variant=variant,evidence=evidence)
    assert_result(rt,data,variant,statuses)
    return dict(status='PASS',variant=variant,independent_backends=True,owner_lock_contention_observed=True)


def report_check(rt,label):
    from tests.support.postgres_staff_fixtures import login,T
    from app import db
    from app.models import Product,Expense,Sale,SaleItem,Business
    data=seed(rt,label);app=rt.app()
    with app.app_context():
        # Synthetic accounting rows only; route permission and rendered values checked below.
        sale=Sale(business_id=data['shop'],sold_at=T,payment_method='Cash')
        db.session.add(sale);db.session.flush()
        db.session.add(SaleItem(sale_id=sale.id,product_id=data['product'],quantity=1,unit_price=20,unit_cost=8))
        db.session.add(Expense(business_id=data['shop'],description='Synthetic expense',amount=3,category='Miscellaneous',spent_at=T))
        foreign=Business(user_id=data['owner'],name=label+' unassigned');db.session.add(foreign);db.session.flush()
        db.session.add(Expense(business_id=foreign.id,description='Unassigned amount',amount=999,category='Miscellaneous',spent_at=T))
        foreign_id=foreign.id;db.session.commit()
    client=login(app,data['staff_email']);base=f"/staff/memberships/{data['member']}/businesses/"
    query='?start='+T.date().isoformat()+'&end='+T.date().isoformat()
    result=client.get(base+str(data['shop'])+'/reports'+query)
    require(result.status_code==200,'operations-report-rejected')
    html=result.get_data(as_text=True)
    for title,value in (('Revenue','20.00'),('Cost of goods sold','8.00'),('Gross profit','12.00'),('Operating expenses','3.00'),('Net profit','9.00')):
        require('<dt>'+title+'</dt><dd>₦'+value+'</dd>' in html,'operations-report-accounting-rejected')
    require(client.get(base+str(foreign_id)+'/reports'+query).status_code==403,'operations-report-scope-rejected')
    require(client.get(base+str(data['shop'])+'/reports?start=2000-01-01&end=2100-01-01').status_code==400,'operations-report-bound-rejected')
    return dict(variant='report',status='PASS',assigned_business_only=True)


def run(rt,report):
    require(rt.auth.get('mode')=='operations' and rt.auth.get('cases')==list(OPERATIONS_CASES),'operations-selection-rejected')
    rt.guards.check_clean();rt.check_source();rt.preflight(V18)
    current=checked_snapshot(rt);base=namespace(rt.auth)
    require(digest(current)==rt.auth.get('starting_state_sha256'),'operations-starting-state-differs');require_unused(current,base)
    for case in OPERATIONS_CASES:
        report('RUNNING',dict(case=case))
        for variant in VARIANTS[case]:
            label=base+'-'+case.lower()+'-'+variant;require_unused(current,label)
            try:result=report_check(rt,label) if variant=='report' else race(rt,label,variant)
            except BaseException:
                rt.guards.check_clean();rt.check_source();require_preserved(current,checked_snapshot(rt),label,allow_empty=True);raise
            rt.guards.check_clean();rt.check_source();after=checked_snapshot(rt)
            require_preserved(current,after,label);rt.preflight(V18);current=after
            report('VARIANT',dict(case=case,existing_records_preserved=True,**result))
    return dict(status='PASS',cases=list(OPERATIONS_CASES),variants=7,existing_records_preserved=True,
                continuation_namespace=base,end_state_sha256=digest(current))
