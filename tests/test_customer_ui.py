"""Customer presentation regressions on disposable data; no provider calls."""
import re
import pytest
from test_multi_business import app, seed, login
from app import db
from app.models import RecurringSubscription
from app.subscriptions.entitlements import PLANS

@pytest.mark.parametrize('path', ['/dashboard','/products/','/products/?view=inventory','/sales/','/restocking/','/expenses/','/reports','/businesses/','/plans/','/profile/'])
def test_customer_pages_keep_navigation_and_csrf(app,path):
    user,shops=seed('plus',2);client=app.test_client();login(client,user)
    response=client.get(path)
    assert response.status_code==200
    html=response.get_data(as_text=True)
    assert 'customer.css' in html and 'customer.js' in html
    assert 'Working in' in html and shops[0].name in html
    assert 'My Businesses' in html and 'aria-current="page"' in html
    for form in re.findall(r'<form\b[^>]*method="(?:POST|post)"[^>]*>(.*?)</form>',html,re.S):
        assert 'name="csrf_token"' in form

@pytest.mark.parametrize('kind',['basic','plus','trial','lifetime'])
def test_billing_prices_and_entitlement_presentation(app,kind):
    user,_=seed(kind);client=app.test_client();login(client,user)
    html=client.get('/plans/').get_data(as_text=True)
    for plan in ('basic','plus'):
        for interval in ('monthly','yearly'):
            assert f'{PLANS[(plan,interval)]["amount"]/100:,.0f}' in html
    assert 'Save ₦6,000/year' in html and 'Save ₦10,000/year' in html
    assert 'type="button" data-price-interval="yearly"' in html
    assert 'switcher is coming' not in html
    if kind in ('basic','plus'):
        assert 'action="/plans/checkout"' not in html
        assert '/change' in html
    if kind=='trial': assert 'No automatic charge follows the trial' in html
    if kind=='lifetime': assert 'You do not need Basic' in html

def test_cancelled_subscription_does_not_claim_renewal(app):
    user,_=seed('plus');sub=RecurringSubscription.query.one();sub.cancel_at_period_end=True;db.session.commit()
    client=app.test_client();login(client,user);html=client.get('/plans/').get_data(as_text=True)
    assert 'Renewal stopped.' in html and 'Next renewal:' not in html

def test_reports_current_navigation_and_inventory_view(app):
    user,_=seed();client=app.test_client();login(client,user)
    html=client.get('/reports').get_data(as_text=True)
    active=re.findall(r'<a class="nav-link active"[^>]*href="([^"]+)"',html)
    assert active==['/reports']
    html=client.get('/products/?view=inventory').get_data(as_text=True)
    assert '<h2>Inventory</h2>' in html and 'name="view" value="inventory"' in html

def test_downgrade_prompt_has_no_false_active_business(app):
    user,shops=seed('plus',2);sub=RecurringSubscription.query.one();sub.plan_code='basic';db.session.commit()
    client=app.test_client();login(client,user);html=client.get('/businesses/').get_data(as_text=True)
    assert 'Choose your active business' in html
    assert 'Working in' not in html
    assert 'Both businesses and all their records are safely stored' in html

def test_public_home_has_no_customer_redesign_assets(app):
    html=app.test_client().get('/').get_data(as_text=True)
    assert 'customer.css' not in html and 'customer.js' not in html
