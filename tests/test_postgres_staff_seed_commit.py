"""Fixture transaction-boundary regressions with synthetic sessions only."""
import ast
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType,SimpleNamespace
import sys
import pytest
from tests.support import postgres_staff_fixtures as fixtures


@pytest.fixture
def setup(monkeypatch):
    class Session:
        def __init__(self):self.pending=[];self.committed=[];self.serial=0
        def add(self,row):
            self.serial+=1;row.id=self.serial;self.pending.append(row)
        def flush(self):pass
        def commit(self):self.committed.extend(self.pending);self.pending=[]
    session=Session()
    class Record:
        def __init__(self,**values):self.__dict__.update(values)
        def set_password(self,value):pass
    models=ModuleType('app.models')
    names=('User','Business','AccountBilling','RecurringSubscription','StaffMembership','StaffMembershipBusiness')
    for name in names:setattr(models,name,type(name,(Record,),{}))
    app=ModuleType('app');app.db=SimpleNamespace(session=session)
    staff=ModuleType('app.staff')
    def invite(*args,**kwargs):
        row=Record();session.add(row);return row,'synthetic-unused-token'
    staff.service=SimpleNamespace(lock_owner=lambda *a:None,owner_access=lambda *a,**k:None,invite=invite)
    staff.roles=SimpleNamespace(_initialize_locked=lambda *a:None,save=lambda *a,**k:None)
    for name,value in [('app',app),('app.models',models),('app.staff',staff)]:monkeypatch.setitem(sys.modules,name,value)
    @contextmanager
    def context():
        try:yield
        finally:session.pending=[]  # Uncommitted setup rows do not survive session removal.
    runtime=SimpleNamespace(app=lambda:SimpleNamespace(app_context=context))
    return runtime,session,models


@pytest.mark.parametrize('case,count',[('I01',4),('F01',4),('I02',4),('A01',3),('A02',3)])
def test_starting_seats_and_assignments_are_committed_before_context_exit(setup,case,count):
    runtime,session,models=setup
    result=fixtures.seed_case(runtime,case)
    assert result['occupied']==count
    assert len([r for r in session.committed if type(r) is models.StaffMembership])==count
    assert len([r for r in session.committed if type(r) is models.StaffMembershipBusiness])==count
    assert not session.pending


@pytest.mark.parametrize('case',['I01','F01'])
def test_original_missing_commit_discards_starting_seats(setup,case):
    runtime,session,models=setup
    source=Path(fixtures.__file__).read_text()
    tree=ast.parse(source)
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='seed_case')
    # Recreate the exact original function by removing only the new commit boundary.
    class RemoveCommit(ast.NodeTransformer):
        def visit_If(self,node):
            if isinstance(node.test,ast.Name) and node.test.id=='occupied':return ast.Pass()
            return self.generic_visit(node)
    function=RemoveCommit().visit(function)
    module=ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))
    namespace=dict(vars(fixtures));exec(compile(module,'<original-seed-fixture>','exec'),namespace)
    result=namespace['seed_case'](runtime,case)
    assert result['occupied']==4
    assert not any(type(r) in (models.StaffMembership,models.StaffMembershipBusiness) for r in session.committed)
