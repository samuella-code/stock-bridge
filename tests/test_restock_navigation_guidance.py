"""Database-free UX rendering checks."""
from types import SimpleNamespace
from tests.test_catalogue_navigation_ux import render


def restock(args=None):
    return render('restocking/index.html', args, restocks=SimpleNamespace(items=[SimpleNamespace(
        received_at=__import__('datetime').datetime(2026, 10, 1), product=SimpleNamespace(name='Coke'),
        quantity=2, unit_cost=3, supplier=None)], has_prev=True, has_next=True, prev_num=2, next_num=4, page=3, pages=5),
        product_page=SimpleNamespace(has_prev=True, has_next=True, prev_num=6, next_num=8, page=7, pages=9),
        query='Coke', recommendations=[], selected=None)


def test_history_preserves_insights_context():
    html = restock()
    assert '/restocking.index?page=2&insight_page=7&q=Coke' in html
    assert '/restocking.index?page=4&insight_page=7&q=Coke' in html


def test_insights_preserves_history_and_search_resets_only_insights():
    html = restock()
    assert '/restocking.index?insight_page=8&q=Coke&page=3' in html
    assert '<input type="hidden" name="page" value="3">' in html
    assert '/restocking.index?page=3">Clear filters' in html


def test_unknown_parameters_not_forwarded():
    html = restock({'next': 'https://evil.example', 'unexpected': 'secret'})
    assert 'evil.example' not in html and 'unexpected=' not in html


def test_empty_notification_history_accurate():
    # Preferences are rendered separately and do not affect the history empty state.
    from tests.test_catalogue_navigation_ux import ROOT
    from jinja2 import Environment, DictLoader
    source = (ROOT / 'app/templates/notifications/index.html').read_text()
    env = Environment(loader=DictLoader({'base.html': '{% block content %}{% endblock %}',
        'notifications/preferences.html': '', 'page.html': source}))
    for total, expected in [(0, 'No notifications yet'), (10, 'No notifications on this page')]:
        html = env.get_template('page.html').render(business=SimpleNamespace(name='Store'),
            notifications=SimpleNamespace(items=[], total=total, pages=1), csrf_token=lambda: '', url_for=lambda *a, **k: '/')
        assert expected in html and "You're all caught up" not in html


def test_basket_guidance_and_tokens_in_both_forms():
    from tests.test_catalogue_navigation_ux import ROOT
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'))
    for mode in ['sale', 'restock']:
        html = env.get_template('products/basket.html').render(mode=mode, url_for=lambda *a: '/lookup')
        assert '<noscript>' in html and 'JavaScript is required to add products and submit this form.' in html
    for path in ['sales/index.html', 'restocking/index.html']:
        source = (ROOT / 'app/templates' / path).read_text()
        assert 'name="submission_token"' in source and 'name="csrf_token"' in source
        assert "'disabled' if not has_products" in source
