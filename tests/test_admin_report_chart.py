"""Render the real admin page under its real CSP, using disposable data only."""
from html.parser import HTMLParser
import pytest
from app.admin import report_data
from tests.test_admin_portal import client, seed, admin_login

class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__(); self.items=[]; self.feed(html)
    def handle_starttag(self, tag, attrs):
        self.items.append((tag,dict(attrs)))

@pytest.mark.parametrize('count', [2,5,14,53])
def test_chart_uses_csp_safe_geometry(client, monkeypatch, tmp_path, count):
    seed(client); admin_login(client)
    weeks=[dict(percent=100 if i==0 else 0, amount=1300000 if i==0 else 0,
        monday='19/10/2026',start='19/10/2026',end='25/10/2026') for i in range(count)]
    monkeypatch.setattr(report_data, 'report', lambda *args,**kwargs: dict(
        new_customers=1,verified=1,collected=1300000,payment_count=2,active=7,
        suspended=0,paying=3,by_plan=dict(basic=1,plus=1,pro=1),
        generated='19/11/2026 · 19:34 WAT',weeks=weeks))
    response=client.get('/admin/reports?period=30d')
    assert response.status_code==200
    html=response.get_data(as_text=True); elements=Elements(html).items
    assert "style-src 'self'" in response.headers['Content-Security-Policy']
    assert not any('style' in attrs for tag,attrs in elements)
    rects=[attrs for tag,attrs in elements if tag=='rect']
    assert len(rects)==count
    assert rects[0]['height']=='100' and rects[0]['y']=='0'
    assert all(r['height']=='0' and r['y']=='100' for r in rects[1:])
    assert f'report-columns-{min(count,7)}' in html
    assert 'aria-live="polite"></p>' in html
    # Browser regression test consumes this exact rendered response and header.
    (tmp_path/'page.html').write_text(html)
    (tmp_path/'csp.txt').write_text(response.headers['Content-Security-Policy'])
