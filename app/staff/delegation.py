"""Pure, fail-closed delegation policy. No ORM, HTTP or application imports.

Role names never authorize. Snapshots are rebuilt under the common owner lock
for each mutation; no snapshot is persisted or reused across requests.
"""
from dataclasses import dataclass

CATALOGUE = {
    'staff_admin': 'Manage employees, roles and business assignments within your scope',
    'records_export': 'Export records you can view (exports are not enabled yet)',
}
OWNER_GRANTED = frozenset(CATALOGUE)
VIEW_PERMISSIONS = frozenset(('sales_view','inventory_view','restocking_view','expenses_view','financial_view'))


class Denied(ValueError):
    pass


def require(condition):
    if not condition: raise Denied('Delegated action is not permitted.')


def ids(values):
    require(isinstance(values,(set,frozenset,list,tuple)))
    require(all(type(v) is int and v>0 for v in values))
    return frozenset(values)


@dataclass(frozen=True)
class Authority:
    owner_id: int
    actor_id: int
    businesses: frozenset
    permissions: frozenset
    role: str = ''

    @property
    def owner(self): return self.actor_id==self.owner_id

    def check(self, permission):
        require(type(self.owner_id) is int and self.owner_id>0 and type(self.actor_id) is int and self.actor_id>0)
        require(self.owner or bool(ids(self.businesses)))
        require(self.owner or permission in self.permissions)

    def assignment(self, permissions, businesses, *, target_user=None, target_email=None, actor_email=None):
        self.check('staff_admin')
        scope=ids(businesses);require(bool(scope) and scope<=self.businesses)
        if not self.owner:
            require(target_user not in (self.actor_id,self.owner_id))
            require(not (target_email and actor_email and target_email.lower()==actor_email.lower()))
            require(not (set(permissions)&OWNER_GRANTED) and set(permissions)<=self.permissions)

    def shared_role(self, key, old, new, references):
        self.check('staff_admin')
        if self.owner:return
        require(key!=self.role)
        require(not ((set(old)|set(new))&OWNER_GRANTED))
        require((set(old)|set(new))<=self.permissions)
        # Include historical/expired references conservatively: never broaden scope
        # by filtering out inconvenient assignments or the acting user.
        for user_id, businesses in references:
            self.assignment(old,businesses,target_user=user_id)


# Explicit field allowlists. A view permission alone never implies cost/financial
# visibility. This is a future export plan only; no serializer/route is enabled.
EXPORTS = {
    'sales': ('sales_view', ('sale_id','sold_at','product_name','quantity'), ('unit_price','revenue','unit_cost','cogs','gross_profit')),
    'stock': ('inventory_view', ('product_id','product_name','stock_quantity'), ('unit_cost','unit_price')),
    'restocks': ('restocking_view', ('receipt_id','restocked_at','product_name','quantity'), ('unit_cost','total_cost')),
    'expenses': ('expenses_view', ('expense_id','spent_at','description'), ('amount',)),
    'reports': ('financial_view', (), ('revenue','cogs','gross_profit','expenses','net_profit')),
}


def export_scope(authority, dataset, businesses, fields):
    authority.check('records_export');require(dataset in EXPORTS)
    view,plain,financial=EXPORTS[dataset]
    require(authority.owner or view in authority.permissions)
    require(isinstance(fields,(list,tuple,set,frozenset)) and all(isinstance(f,str) for f in fields))
    require(set(fields)<=set(plain+financial))
    selected=ids(businesses)&authority.businesses;require(bool(selected))
    allowed=plain+financial if authority.owner or 'financial_view' in authority.permissions else plain
    columns=tuple(f for f in plain+financial if f in fields and f in allowed)
    require(bool(columns))
    return selected,columns


def can_export(*args, **kwargs):
    return False  # No export endpoint, query, file generation or operational bypass.
