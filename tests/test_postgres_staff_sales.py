"""No application import or connection unless the exact launcher authorizes it."""
import os
import pytest
from tests.support.postgres_staff_guard import SALES_CASES,authorized_case


def test_staff_sales_live_default_gate(request):
    runtime=authorized_case(SALES_CASES[0],request.config.getoption('maxfail'),os.environ)
    # The launcher dispatches the state-bound batch directly, never this test.
    raise AssertionError('Use the separately approved sales launcher stage')
