"""Presentation/navigation checks without an application or database connection."""
import ast
import importlib.util
from datetime import datetime
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('catalogue_navigation', ROOT / 'app/products/navigation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
context = module.catalogue_context


def render(name, args=None, **values):
    env = Environment(loader=ChoiceLoader([
        DictLoader({'base.html': '{% block content %}{% endblock %}',
                    'components/ui.html': '{% macro help_panel(title) %}{{caller()}}{% endmacro %}{% macro empty_state(a,b,c,d) %}{{a}}{% endmacro %}',
                    'products/basket.html': ''}),
        FileSystemLoader(ROOT / 'app/templates')]), autoescape=select_autoescape())
    defaults = dict(request=SimpleNamespace(args=args or {}, form={}), csrf_token=lambda: 'test',
        catalogue_navigation=context(args or {}),
        url_for=lambda endpoint, **kw: '/' + endpoint + ('?' + urlencode(kw) if kw else ''),
        total=1234, profit=900, categories=[], payment_methods=[], submission_token='test',
        has_products=False, sales=SimpleNamespace(items=[]), expenses=SimpleNamespace(items=[]))
    defaults.update(values)
    return unescape(env.get_template(name).render(**defaults))


@pytest.mark.parametrize('template,label,note', [
    ('sales/index.html', 'All-time recorded sales', 'Voided sales excluded from both totals.'),
    ('expenses/index.html', 'All-time recorded expenses', 'Voided expenses excluded.')])
def test_totals_have_accurate_labels(template, label, note):
    html = render(template)
    assert label in html and note in html and '₦1,234.00' in html
    if template.startswith('sales'):
        assert 'All-time estimated gross profit ₦900.00' in html


@pytest.mark.parametrize('path,model', [('app/sales/routes.py', 'Sale'), ('app/expenses/routes.py', 'Expense')])
def test_existing_totals_exclude_voided_and_have_no_period_filter(path, model):
    tree = ast.parse((ROOT / path).read_text())
    assignment = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == ('totals' if model == 'Sale' else 'total') for t in n.targets))
    query = ast.unparse(assignment.value)
    assert f'{model}.voided_at.is_(None)' in query
    assert f'{model}.business_id == b.id' in query
    assert 'sold_at' not in query and 'spent_at' not in query


@pytest.mark.parametrize('view,label', [('inventory', 'inventory'), ('products', 'products'), ('//evil.example', 'products'), ('', 'products')])
def test_form_return_and_cancel_match_view(view, label):
    html = render('products/form.html', {'view': view}, product=None)
    assert f'Back to {label}' in html
    target = '/products.index?view=inventory' if label == 'inventory' else '/products.index'
    assert f'href="{target}"' in html
    assert 'evil.example' not in html


def test_filters_are_preserved_without_destination_or_history_page_override():
    args = dict(view='inventory', q='Coke', category='Drinks', stock='low', sort='name',
                page='8', next='https://evil.example', product_id='99')
    expected = dict(view='inventory', q='Coke', category='Drinks', stock='low', sort='name')
    assert context(args) == expected
    html = render('products/form.html', args, product=None)
    assert '/products.index?' + urlencode(expected) in html
    assert 'evil.example' not in html


def test_invalid_context_has_safe_products_fallback():
    assert context({'view': 'https://evil.example', 'stock': 'unexpected', 'sort': 'invalid', 'page': '-1'}) == {}
    assert len(context({'q': 'x' * 200})['q']) == 100


def test_detail_edit_and_history_links_keep_context():
    product = SimpleNamespace(id=1, name='Coke', category='Drinks', unit='units', active=True,
        stock_quantity=10, stock_status='In stock', buying_price=2, selling_price=3,
        minimum_stock_level=5, sku=None, description=None, created_at=datetime(2026, 10, 1))
    html = render('products/detail.html', {'view': 'inventory', 'q': 'Coke'}, product=product,
                  sold=(0, 0), last_restock=None, reasons=[],
                  movements=SimpleNamespace(items=[], has_prev=True, prev_num=1, has_next=True, next_num=3, page=2, pages=3))
    assert 'Back to inventory' in html
    assert '/products.edit?product_id=1&view=inventory&q=Coke' in html
    assert '/products.detail?product_id=1&page=3&view=inventory&q=Coke' in html


def test_catalogue_details_and_add_links_carry_context():
    html = render('products/index.html', {'view': 'inventory', 'q': 'Coke'}, products=[],
        pagination=SimpleNamespace(has_prev=False, has_next=False, page=1, pages=1), query='Coke',
        stock_filter='all', category='', sort='recent', total_products=0, low_stock_count=0, inventory_value=0)
    assert '/products.create?view=inventory&q=Coke' in html
    source = (ROOT / 'app/templates/products/index.html').read_text()
    assert source.count("url_for('products.detail',product_id=p.id,**catalogue_navigation)") == 2
