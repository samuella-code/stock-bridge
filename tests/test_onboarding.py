"""Guidance acceptance with disposable data, no provider or email requests."""
from datetime import datetime, timedelta
import re
import pytest
from test_multi_business import app, seed, login
from app import db
from app.main.onboarding import setup_progress
from app.models import AccountBilling, Product, Sale, SaleItem, Expense, Restock


def records(business, *, sale=False, expense=False, voided=False):
    product = Product(business_id=business.id, name='Coke', stock_quantity=90,
        opening_quantity=100, buying_price=300, selling_price=500)
    db.session.add(product); db.session.flush()
    if sale:
        row = Sale(business_id=business.id, sold_at=datetime.utcnow()-timedelta(days=60),
            voided_at=datetime.utcnow() if voided else None)
        db.session.add(row); db.session.flush()
        db.session.add(SaleItem(sale_id=row.id, product_id=product.id,
            quantity=10, unit_price=500, unit_cost=300))
    if expense:
        db.session.add(Expense(business_id=business.id, description='Rent', amount=2000,
            spent_at=datetime.utcnow()-timedelta(days=60),
            voided_at=datetime.utcnow() if voided else None))
    db.session.commit()
    return product


@pytest.mark.parametrize('kind', ['basic', 'plus', 'trial', 'lifetime', 'lifetime_plus'])
def test_empty_business_welcome_and_real_setup(app, kind):
    user, shops = seed(kind); client = app.test_client(); login(client, user)
    html = client.get('/dashboard').get_data(as_text=True)
    assert 'Here is how '+shops[0].name+' is doing today.' in html
    assert setup_progress(shops[0])['completed']==0
    assert 'business-setup' not in html and 'Finish setting up' not in html
    for action in ('Record sale','Restock','Add expense'):assert action in html



@pytest.mark.parametrize('sale,expense,count', [(False,False,1),(True,False,2),(False,True,2),(True,True,3)])
def test_setup_completes_from_all_dates_and_hides_when_finished(app, sale, expense, count):
    user, shops = seed(); records(shops[0], sale=sale, expense=expense)
    progress = setup_progress(shops[0]); assert progress['completed'] == count
    client = app.test_client(); login(client, user)
    html = client.get('/dashboard?period=today').get_data(as_text=True)
    assert 'Welcome to StockBridge' not in html
    assert 'id="business-setup"' not in html
    assert setup_progress(shops[0])['completed']==count
    if count == 2:
        assert 'id="business-setup" open' not in html  # Existing transaction history stays compact.
    assert Product.query.one().stock_quantity == 90


def test_voided_activity_does_not_complete_setup(app):
    _, shops = seed(); records(shops[0], sale=True, expense=True, voided=True)
    progress = setup_progress(shops[0])
    assert progress['completed'] == 1
    assert progress['steps'][1]['complete'] is False and progress['steps'][2]['complete'] is False


def test_sale_header_without_items_is_not_valid_completion(app):
    _, shops = seed(); db.session.add(Sale(business_id=shops[0].id)); db.session.commit()
    assert not setup_progress(shops[0])['steps'][1]['complete']


def test_archived_products_do_not_make_established_business_new(app):
    _, shops = seed(); product=records(shops[0]); product.active=False; db.session.commit()
    assert setup_progress(shops[0])['completed'] == 1
    assert not setup_progress(shops[0])['show_welcome']


def test_switching_uses_each_business_setup_and_empty_states(app):
    user, shops=seed('plus',2); records(shops[0], sale=True, expense=True)
    client=app.test_client(); login(client,user)
    assert 'id="business-setup"' not in client.get('/dashboard').get_data(as_text=True)
    assert client.post(f'/businesses/{shops[1].id}/switch').status_code == 302
    html=client.get('/dashboard').get_data(as_text=True)
    assert f'Here is how {shops[1].name} is doing today.' in html
    assert setup_progress(shops[1])['completed']==0
    assert 'Your products and prices will appear here once you add a product.' in client.get('/products/').get_data(as_text=True)
    client.post(f'/businesses/{shops[0].id}/switch')
    assert 'id="business-setup"' not in client.get('/dashboard').get_data(as_text=True)


def test_other_account_records_cannot_complete_setup(app):
    _, shops=seed(); records(shops[0],sale=True,expense=True)
    user, other=seed('basic', email='other@example.invalid')
    assert setup_progress(other[0])['completed']==0
    client=app.test_client();login(client,user)
    assert f'Here is how {other[0].name} is doing today.' in client.get('/dashboard').get_data(as_text=True)


@pytest.mark.parametrize('path,title,action', [('/products/','Your products and prices will appear here','Add product'),
    ('/products/?view=inventory','Your stock levels will appear here','Add product'),
    ('/sales/','Your sales will appear here once recorded.','Add product'),
    ('/restocking/','Your recent restocks will appear here.','Add a product'),
    ('/expenses/','Your expenses will appear here once recorded.','Add expense')])
def test_useful_no_data_states(app,path,title,action):
    user,_=seed();client=app.test_client();login(client,user)
    html=client.get(path).get_data(as_text=True)
    assert title in html and action in html
    assert '<summary>Learn about' not in html and 'sb-empty' in html


@pytest.mark.parametrize('path', ['/products/?q=missing','/products/?view=inventory&stock=low', '/restocking/?q=missing'])
def test_filtered_empty_state_never_claims_first_product(app,path):
    user,shops=seed();records(shops[0]);client=app.test_client();login(client,user)
    html=client.get(path).get_data(as_text=True)
    assert 'Clear filters' in html
    assert 'Add your first product' not in html


def test_form_helper_text_keeps_field_names_and_stock_edit_guard(app):
    user,shops=seed();product=records(shops[0]);client=app.test_client();login(client,user)
    html=client.get('/products/new').get_data(as_text=True)
    for name in ('stock_quantity','buying_price','selling_price','minimum_stock_level'):
        assert f'name="{name}"' in html and f'aria-describedby="help-{name}"' in html
        assert f'id="help-{name}"' in html
    assert 'name="stock_quantity"' not in client.get(f'/products/{product.id}/edit').get_data(as_text=True)


def test_trial_banner_uses_real_account_date_not_legacy_business_date(app):
    user,shops=seed('trial');account=db.session.get(AccountBilling,user.id)
    account.trial_ends_at=datetime.utcnow()+timedelta(days=2,hours=3)
    shops[0].trial_ends_at=datetime(2040,1,1);db.session.commit()
    before=(account.trial_started_at,account.trial_ends_at)
    client=app.test_client();login(client,user);html=client.get('/dashboard').get_data(as_text=True)
    assert account.trial_ends_at.strftime('%d/%m/%Y') in html and 'data-sb-wat="date"' in html
    assert 'Free Trial · 3 days remaining' in html and 'No card required.' in html
    assert '2040' not in html
    assert (account.trial_started_at,account.trial_ends_at)==before


@pytest.mark.parametrize('kind',['basic','plus','lifetime','lifetime_plus'])
def test_paid_and_lifetime_accounts_never_get_trial_pressure(app,kind):
    user,_=seed(kind);account=db.session.get(AccountBilling,user.id)
    account.trial_started_at=datetime.utcnow();account.trial_ends_at=datetime.utcnow()+timedelta(days=7);db.session.commit()
    client=app.test_client();login(client,user);html=client.get('/dashboard').get_data(as_text=True)
    assert 'trial-status' not in html and 'After your seven-day trial' not in html
    if kind.startswith('lifetime'):assert 'Existing account access' in html


def test_expired_guidance_preserves_write_guard(app):
    user,_=seed('trial');account=db.session.get(AccountBilling,user.id)
    account.trial_ends_at=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
    client=app.test_client();login(client,user);html=client.get('/dashboard').get_data(as_text=True)
    assert 'trial-status' not in html and 'data-welcome-key' not in html
    assert 'Trial expired' in html and 'Your records are safe. Choose a plan to add or change records.' in html
    assert client.post('/products/new',data={'name':'Forbidden'}).status_code==302
    assert Product.query.count()==0


def test_reports_explains_preserved_financial_formulas(app):
    user,shops=seed();records(shops[0],sale=True,expense=True)
    client=app.test_client();login(client,user);html=client.get('/reports?period=month').get_data(as_text=True)
    assert '<summary>More figures</summary>' in html
    assert 'What you sold minus what the goods cost you' in html
    assert 'Estimated net profit' in html and 'Voided sales and expenses are excluded from totals.' in html
