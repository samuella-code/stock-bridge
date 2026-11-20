"""Business-wide summary presentation; disposable SQLite fixtures only."""
from decimal import Decimal
import re

import pytest
from flask import template_rendered
from tests.presentation_helpers import text,naira

from app import db
from app.models import Product
from tests.test_multi_business import app, seed, login

HEADING = 'Business-wide summary — all active products'
EXPLANATION = ('These totals cover all active products in this business. Search, filters and pagination '
               'affect only the product list below. Archived products are excluded.')


def catalogue(app):
    user, shops = seed('plus', 2)
    _, foreign = seed('basic', email='foreign-summary@example.invalid')
    products = [
        Product(business_id=shops[0].id, name='Alpha Drink', category='Drinks', stock_quantity=10,
                buying_price=100, selling_price=200, minimum_stock_level=2),
        Product(business_id=shops[0].id, name='Beta Food', category='Food', stock_quantity=2,
                buying_price=250, selling_price=400, minimum_stock_level=3),
        Product(business_id=shops[0].id, name='Gamma Food', category='Food', stock_quantity=0,
                buying_price=400, selling_price=600, minimum_stock_level=1),
        Product(business_id=shops[0].id, name='Archived Item', stock_quantity=100,
                buying_price=999, active=False),
        Product(business_id=shops[1].id, name='Second Business Item', stock_quantity=4,
                buying_price=900, minimum_stock_level=5),
        Product(business_id=foreign[0].id, name='PRIVATE_NOT_FOR_THIS_BUSINESS',
                stock_quantity=100, buying_price=10000),
    ]
    db.session.add_all(products); db.session.commit()
    client = app.test_client(); login(client, user)
    return client, shops


def get_catalogue(app, client, query):
    contexts = []
    def capture(sender, template, context, **extra):
        if template.name == 'products/index.html': contexts.append(context)
    with template_rendered.connected_to(capture, app):
        response = client.get('/products/', query_string=query)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'aria-label="Stock summary"' in html or 'aria-label="Product summary"' in html
    assert html.index('sb-catalogue-summary')<html.index('sb-catalogue-filter')
    assert 'PRIVATE_NOT_FOR_THIS_BUSINESS' not in html
    return html, contexts[0]


def assert_summary(html, context, count=3, low=2, value=Decimal('1500')):
    assert (context['total_products'], context['low_stock_count'], context['inventory_value']) == (count, low, value)
    summary=html.split('sb-catalogue-summary',1)[1].split('</section>',1)[0]
    if 'sb-inventory-summary' in summary:
        assert 'Needs restocking' in summary and f'<dd>{low}</dd>' in summary
        assert naira(value) in summary
        assert 'across all active products' in summary and 'Active products on this page' in summary
    else:
        assert 'Active products' in summary and f'<dd>{count}</dd>' in summary
        assert 'Average margin' in summary and 'on this page' in summary



@pytest.mark.parametrize('view', ['', 'inventory'], ids=['products','inventory'])
@pytest.mark.parametrize('query,names', [
    ({}, ['Alpha Drink','Beta Food','Gamma Food']),
    ({'q':'Alpha'}, ['Alpha Drink']),
    ({'category':'Food'}, ['Beta Food','Gamma Food']),
    ({'stock':'low'}, ['Beta Food']),
    ({'stock':'out'}, ['Gamma Food']),
    ({'stock':'in'}, ['Alpha Drink']),
    ({'stock':'archived'}, ['Archived Item']),
    ({'q':'no-such-product'}, []),
    ({'q':'Food','category':'Food','stock':'low'}, ['Beta Food']),
])
def test_summary_scope_is_explicit_and_unchanged_by_filters(app, view, query, names):
    client, _ = catalogue(app)
    html, context = get_catalogue(app, client, {'view':view, **query})
    assert_summary(html, context)
    assert sorted(product.name for product in context['products']) == sorted(names)
    assert context['query'] == query.get('q','')
    assert context['category'] == query.get('category','')
    assert context['stock_filter'] == query.get('stock','all')
    if not names:
        assert 'No products match these filters.' in html
    for name in names:
        assert name in text(html)


@pytest.mark.parametrize('view', ['', 'inventory'], ids=['products','inventory'])
def test_summary_tracks_selected_business_and_restores_original(app, view):
    client, shops = catalogue(app)
    html, context = get_catalogue(app, client, {'view':view})
    assert_summary(html, context)
    assert client.post(f'/businesses/{shops[1].id}/switch').status_code == 302
    html, context = get_catalogue(app, client, {'view':view})
    assert_summary(html, context, count=1, low=1, value=Decimal('3600'))
    assert [p.name for p in context['products']] == ['Second Business Item']
    assert 'Alpha Drink' not in html
    assert client.post(f'/businesses/{shops[0].id}/switch').status_code == 302
    html, context = get_catalogue(app, client, {'view':view})
    assert_summary(html, context)
    assert 'Second Business Item' not in html


@pytest.mark.parametrize('view', ['', 'inventory'], ids=['products','inventory'])
def test_summary_is_unchanged_across_pagination(app, view):
    client, shops = catalogue(app)
    db.session.add_all([Product(business_id=shops[0].id, name=f'Page Item {i:02}',
        stock_quantity=1, buying_price=10) for i in range(21)])
    db.session.commit()
    seen = set()
    for page, expected_rows in [(1,20),(2,4)]:
        html, context = get_catalogue(app, client, {'view':view,'page':page,'sort':'name'})
        assert_summary(html, context, count=24, low=2, value=Decimal('1710'))
        assert context['pagination'].page == page and context['pagination'].pages == 2
        assert len(context['products']) == expected_rows
        ids = {p.id for p in context['products']}
        assert not seen & ids
        seen.update(ids)
    assert len(seen) == 24
