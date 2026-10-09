"""Disclosure icons use CSS geometry; native details semantics remain intact."""
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]


def test_faq_and_sales_help_use_css_triangles_not_font_glyphs():
    css = (ROOT / 'app/static/css/style.css').read_text()
    for selector in ('.faq-section details > summary', '.context-help > summary'):
        assert selector + '::-webkit-details-marker' in css
        assert selector + '::marker' in css
        assert selector + '::before' in css
    assert 'border-left: 6px solid #111827;' in css
    assert 'border-top: 4px solid transparent;' in css
    assert 'border-bottom: 4px solid transparent;' in css
    assert '.customer-app details.context-help > summary { padding-left: 30px; }' in css
    assert '.faq-section details[open] > summary::before' in css
    assert '.context-help[open] > summary::before { transform: rotate(90deg); }' in css
    assert '▶' not in css


def test_sales_help_preserves_native_disclosure_and_accessible_text():
    env = Environment(loader=FileSystemLoader(ROOT / 'app/templates'), autoescape=True)
    html = env.from_string("{% from 'components/ui.html' import help_panel %}{% call help_panel('Sales') %}<p>Help</p>{% endcall %}").render()
    assert '<details class="context-help"><summary>Learn about Sales</summary>' in html
    assert '<p>Help</p>' in html
    assert '</details>' in html
    assert 'aria-hidden' not in html
    assert '▶' not in html
    sales = (ROOT / 'app/templates/sales/index.html').read_text()
    assert "help_panel('Sales')" in sales
