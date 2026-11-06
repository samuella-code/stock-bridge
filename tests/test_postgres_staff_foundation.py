"""18 live scenarios. Default skips before fixture/application import/connection."""
import os
import pytest
from tests.support.postgres_staff_guard import CASES, authorized_case


@pytest.mark.parametrize('case', CASES)
def test_staff_postgresql(case, request):
    runtime=authorized_case(case, request.config.getoption('maxfail'), os.environ)
    from tests.support.postgres_staff_fixtures import execute_case
    execute_case(runtime, case)
