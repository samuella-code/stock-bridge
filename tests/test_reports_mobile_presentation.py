"""Database-free Reports presentation tests; no application imports or factory."""
import ast
from datetime import datetime, time, timedelta
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from jinja2 import DictLoader, Environment, select_autoescape

ROOT = Path(__file__).resolve().parents[1]


class ReportsMobilePresentation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse((ROOT / 'app/main/routes.py').read_text())
        functions = [node for node in source.body if isinstance(node, ast.FunctionDef)
                     and node.name in ('period_dates', 'report_date_errors')]
        assert len(functions) == 2
        cls.functions = dict(datetime=datetime, time=time, timedelta=timedelta, re=re)
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<date-helpers-only>', 'exec'), cls.functions)
        cls.env = Environment(loader=DictLoader({
            'reports.html': (ROOT / 'app/templates/reports.html').read_text(),
            'components/assistant.html': (ROOT / 'app/templates/components/assistant.html').read_text(),
            'base.html': '{% block content %}{% endblock %}{% block scripts %}{% endblock %}',
            'components/ui.html': '{% macro help_panel(title) %}{{ caller() }}{% endmacro %}',
            'components/icons.html': '{% macro icon(name) %}<svg></svg>{% endmacro %}',
        }), autoescape=select_autoescape(['html']))
        cls.env.globals['url_for'] = lambda endpoint, **kw: '/synthetic/' + kw.get('filename', endpoint)

    def render_report(self, values):
        # Match the unchanged Reports route's explicit-date precedence without app execution.
        args = dict(values)
        if any(args.get(field, '') for field in ('from', 'to')):
            args['period'] = 'custom'
        errors = self.functions['report_date_errors'](args)
        context = dict(request=SimpleNamespace(args=values), business=SimpleNamespace(name='Synthetic'),
                       report_errors=errors, period='custom' if errors else args.get('period', 'today'))
        if not errors:
            period, start, end = self.functions['period_dates'](args)
            context.update(period=period, start=start, end=end-timedelta(days=1), metrics={
                k:0 for k in ('revenue','cogs','gross_profit','expenses','net_profit','inventory_purchased',
                             'inventory_value','number_of_sales','units_sold','low_stock_count','out_stock_count')},
                by_units=[], by_revenue=[], slow=[])
        return self.env.get_template('reports.html').render(**context), errors

    def test_valid_ranges_and_presets(self):
        for preset in ('today','week','month','custom'):
            with self.subTest(preset=preset):
                html, errors = self.render_report(dict(period=preset, **{'from':'2026-10-01','to':'2026-10-02'}))
                self.assertFalse(errors)
                self.assertIn('data-report-results', html)
                self.assertIn('data-report-period', html)
                self.assertIn('01 Oct 2026 to 02 Oct 2026', html)

    def test_reversed_and_incomplete_ranges_preserve_accessible_errors(self):
        for values in ({'from':'2026-10-22','to':'2026-10-20'},
                       {'from':'2026-10-01','to':''}, {'from':'','to':'2026-10-02'}):
            with self.subTest(values=values):
                html, errors = self.render_report(dict(period='month', **values))
                self.assertTrue(errors)
                self.assertNotIn('data-report-results', html)
                self.assertIn('role="alert"', html)
                for field in errors:
                    self.assertIn(f'aria-describedby="report-{field}-error"', html)
                    self.assertIn(f'id="report-{field}-error"', html)
                for field,value in values.items():
                    self.assertIn(f'name="{field}" value="{value}"', html)

    def test_correction_restores_results(self):
        _, errors = self.render_report({'period':'month','from':'2026-10-22','to':'2026-10-20'})
        self.assertTrue(errors)
        html, errors = self.render_report({'period':'month','from':'2026-10-20','to':'2026-10-22'})
        self.assertFalse(errors)
        self.assertIn('data-report-results', html)
        self.assertNotIn('aria-invalid="true"', html)

    def test_assistant_marker_only_on_reports(self):
        for endpoint in ('main.reports','main.dashboard','products.index'):
            with self.subTest(endpoint=endpoint):
                html = self.env.get_template('components/assistant.html').render(
                    request=SimpleNamespace(endpoint=endpoint), config=SimpleNamespace(),
                    current_user=SimpleNamespace(is_authenticated=True))
                self.assertEqual('data-assistant-page="reports"' in html, endpoint=='main.reports')


if __name__ == '__main__':
    unittest.main()
