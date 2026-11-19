"""Payment presentation and projections against disposable SQLite only."""
import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from app import db
from app.admin import payment_data
from app.models import Business, Payment, RecurringSubscription, User
from tests.test_admin_portal import client, admin_login
from tests.test_admin_subscription_payments import records
from tests.test_admin_subscription_reporting import customer, payment


def test_summary_verified_collections_and_currency(client, records):
    for status,amount,currency in [('initialized',120000,'NGN'),('pending',230050,'NGN'),('failed',450000,'NGN'),('failed',1000,'USD')]:
        db.session.add(Payment(user_id=records['customer'],business_id=records['business'],product='subscription',
            customer_email='owner@example.com',reference=status+currency,amount_kobo=amount,currency=currency,status=status))
    # A success marker without a verified linked ledger entry is not collected money.
    db.session.add(Payment(user_id=records['customer'],product='subscription',customer_email='owner@example.com',
        reference='unverified',amount_kobo=999900,status='success'))
    db.session.commit()
    values=payment_data.summary(datetime.utcnow())
    assert values['collected']==dict(count=1,revenue_kobo=500000)
    assert values['pending']==dict(count=2,revenue_kobo=350050)
    assert values['failed']==dict(count=1,revenue_kobo=450000)
    assert payment_data.money(120000)=='₦1,200' and payment_data.money(120050)=='₦1,200.50'
    assert payment_data.money(1250,'USD')=='USD 12.50'
    admin_login(client)
    page=client.get('/admin/payments').get_data(as_text=True)
    assert '<h1>Payments</h1>' in page and '₦3,500.50' in page
    assert 'Read-only history. Access is managed by the payment verification process.' in page
    assert 'not money collected' in page


def test_renewals_use_winning_period_and_exclude_stopped_expired_staff_and_foreign_links(client, records):
    now=datetime.utcnow()
    client.application.config['SUBSCRIPTIONS_ENABLED']=True
    valid,shops,sub=customer('plus',now=now)
    sub.next_renewal_at=now+timedelta(days=10)
    for kind in ('flag','cancelled','expired','staff','far','foreign','suspended'):
        owner,owned,other=customer('pro',now=now)
        other.next_renewal_at=now+timedelta(days=4)
        if kind=='flag': other.cancel_at_period_end=True
        elif kind=='cancelled': other.status='cancelled'
        elif kind=='expired': other.current_period_end=now-timedelta(seconds=1)
        elif kind=='staff': owner.staff_only=True
        elif kind=='far': other.next_renewal_at=now+timedelta(days=30)
        elif kind=='foreign': other.business_id=shops[0].id
        elif kind=='suspended': owner.suspended_at=now
    db.session.commit()
    assert payment_data.summary(now)['renewals']==dict(count=1,revenue_kobo=500000)
    # A later winning cancellation must not leave the earlier active row counted.
    newer=RecurringSubscription(user_id=valid.id,business_id=shops[0].id,plan_code='plus',billing_interval='monthly',
        amount_kobo=500000,provider_plan_code='synthetic',status='cancel_at_period_end',
        current_period_start=now,current_period_end=now+timedelta(days=9))
    db.session.add(newer);db.session.commit()
    assert payment_data.summary(now)['renewals']==dict(count=0,revenue_kobo=0)


@pytest.mark.parametrize('term',['SUB-current','owner@example.com','Owner','Shop'])
def test_search_covers_reference_customer_business(client, records,term):
    assert [p.id for p in payment_data.filtered(term)]==[records['payment']]


def test_literal_search_and_masked_paginated_export(client, records):
    stamp=datetime.utcnow()-timedelta(days=1)
    for i in range(105):
        db.session.add(Payment(user_id=records['customer'],business_id=records['business'],product='subscription',
            customer_email='owner@example.com',reference=f'=100%_REF-{i}',amount_kobo=120000,status='pending',created_at=stamp))
    db.session.commit(); admin_login(client)
    response=client.get('/admin/payments?format=csv&status=pending&q=%25_')
    assert response.status_code==200 and response.mimetype=='text/csv'
    text=response.get_data(as_text=True)
    rows=list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
    assert len(rows)==106 and len({r[-1] for r in rows[1:]})==105
    assert all(r[-1].startswith("'=100%_") for r in rows[1:])
    assert 'owner@example.com' not in text and 'o•••@e•••.com' in text and 'WAT' in text
    assert 'SUB-current' not in text
    empty=client.get('/admin/payments?format=csv&q=no-such-record').get_data(as_text=True)
    assert len(list(csv.reader(io.StringIO(empty.lstrip('\ufeff')))))==1


def test_copy_full_reference_and_provider_button_cannot_mutate(client, records):
    reference='SUB-long-reference-1234567890abcdef'
    row=db.session.get(Payment,records['payment']);row.reference=reference;row.status='pending';db.session.commit()
    admin_login(client)
    page=client.get('/admin/payments').get_data(as_text=True)
    assert 'SUB-long-r…abcdef' in page and f'data-copy-reference="{reference}"' in page
    assert f'data-check-payment="/admin/payments/{row.id}/check"' in page
    assert '/verify' not in page
    assert 'Checking with Paystack is read-only and recorded in Activity.' in page
    script=Path('app/static/js/admin-payments.js').read_text()
    assert 'navigator.clipboard.writeText(button.dataset.copyReference)' in script
    assert "method: 'POST'" in script and 'X-CSRFToken' in script and '.innerHTML' not in script
    assert db.session.get(Payment,records['payment']).status=='pending'


def test_export_requires_admin_and_excludes_protected_or_unowned_records(client, records):
    path='/admin/payments?format=csv'
    assert client.get(path).status_code==302
    admin_login(client)
    db.session.get(Payment,records['payment']).user_id=records['admin'];db.session.commit()
    assert 'SUB-current' not in client.get(path).get_data(as_text=True)


def test_read_views_and_export_only_select(client, records):
    from sqlalchemy import event
    admin_login(client)
    statements=[]
    def capture(conn,cursor,statement,*args): statements.append(statement.lstrip().split()[0].upper())
    event.listen(db.engine,'before_cursor_execute',capture)
    try:
        assert client.get('/admin/payments').status_code==200
        assert client.get('/admin/payments?format=csv').status_code==200
        assert client.get(f"/admin/payments/{records['payment']}").status_code==200
    finally: event.remove(db.engine,'before_cursor_execute',capture)
    assert set(statements)=={'SELECT'}
