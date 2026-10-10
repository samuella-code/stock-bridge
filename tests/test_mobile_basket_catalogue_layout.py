"""Database-free presentation regressions; native layout requires browser QA."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]


def test_basket_legend_remains_semantic_but_moves_into_card_layout():
    css = (ROOT / 'app/static/css/customer.css').read_text()
    assert '.customer-app .basket-line legend{float:left;grid-column:1/-1;width:100%;padding:0;min-width:0;box-sizing:border-box}' in css
    script = (ROOT / 'app/static/js/basket.js').read_text()
    assert "document.createElement('fieldset')" in script
    assert "document.createElement('legend')" in script
    assert 'title.textContent=product.name' in script
    assert 'grid-template-columns:repeat(2,minmax(0,1fr))' in css


@pytest.mark.parametrize('view', ['products', 'inventory'])
def test_catalogue_sort_has_full_width_mobile_hook_and_original_options(view):
    env = Environment(loader=ChoiceLoader([
        DictLoader({'base.html': '{% block content %}{% endblock %}'}),
        FileSystemLoader(ROOT / 'app/templates')]), autoescape=True)
    html = env.get_template('products/index.html').render(
        request=SimpleNamespace(args={'view': view}), total_products=0,
        low_stock_count=0, inventory_value=0, query='', categories=[], category='',
        stock_filter='all', sort='priority', products=[], catalogue_navigation={},
        url_for=lambda endpoint, **kw: '/' + endpoint)
    assert '<label class="catalogue-sort">Sort<select name="sort">' in html
    for value, label in [('recent', 'Recently added'), ('name', 'Name'),
                         ('stock', 'Quantity'), ('price', 'Selling price'),
                         ('cost', 'Cost price'), ('value', 'Stock value'),
                         ('oldest', 'Oldest first'), ('priority', 'Low-stock priority')]:
        assert f'value="{value}"' in html and label in html
    assert '<option value="priority" selected>Low-stock priority</option>' in html


def test_sort_width_rule_is_scoped_to_mobile_without_changing_grid():
    css = (ROOT / 'app/static/css/customer.css').read_text()
    start = css.index('@media(max-width:600px){.customer-app .dashboard-wrap')
    mobile = css[start:css.index('\n}', start)]
    assert '.customer-app .inventory-toolbar .catalogue-sort{grid-column:1/-1}' in mobile
    assert '.customer-app .inventory-toolbar .catalogue-sort select{width:100%;max-width:100%;min-width:0;box-sizing:border-box}' in mobile
    assert 'grid-template-columns:repeat(2,minmax(0,1fr))' in mobile
    assert 'catalogue-sort' not in css[:start]
