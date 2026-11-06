"""16 Manager integration cases; no fixture/app import before authorization."""
import os
import pytest
from tests.support.postgres_staff_guard import MANAGER_CASES,authorized_case


@pytest.mark.parametrize('case',MANAGER_CASES)
def test_manager_postgresql(case,request):
    runtime=authorized_case(case,request.config.getoption('maxfail'),os.environ)
    from tests.support.postgres_staff_manager import execute_case
    execute_case(runtime,case)
