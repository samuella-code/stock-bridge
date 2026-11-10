"""Explicit, fingerprint-bound backfill; never runs during startup or migration."""
import hashlib,json
import click
from app import db
from app.models import StaffRole
from app.staff import roles,service


def snapshot():
    rows=StaffRole.query.filter(StaffRole.key.in_(tuple(roles.TEMPLATES))).order_by(StaffRole.owner_id,StaffRole.key).all()
    if len(rows)>10000:raise ValueError('Default-role update exceeds reviewed bound.')
    return [dict(owner_id=r.owner_id,key=r.key,name=r.name,archived=r.archived,permissions=r.permissions) for r in rows]


def digest(rows):return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def permissions_for(row):
    # Preserve separately owner-granted team administration/export permissions.
    return roles.validate_permissions(sorted(set(roles.TEMPLATES[row['key']][1]) | (set(row['permissions'])-set(roles.CATALOGUE))))


def plan():
    rows=snapshot()
    changed=[r for r in rows if set(r['permissions'])!=set(permissions_for(r))]
    return dict(fingerprint=digest(rows),roles=len(rows),changed=len(changed),owners=len({r['owner_id'] for r in changed}))


def apply(expected,actor):
    if actor is None or actor.role!='admin' or actor.suspended_at is not None:raise ValueError('Active administrator required.')
    rows=snapshot()
    if digest(rows)!=expected:raise ValueError('Role state changed; review a fresh plan.')
    for owner_id in sorted({r['owner_id'] for r in rows}):service.lock_owner(owner_id)
    rows=snapshot()
    if digest(rows)!=expected:raise ValueError('Role state changed; review a fresh plan.')
    changed=0
    for row in rows:
        desired=permissions_for(row)
        if set(row['permissions'])==set(desired):continue
        role=db.session.get(StaffRole,(row['owner_id'],row['key']))
        before=roles.facts(role);role.permissions=desired
        roles.record(role.owner_id,'STAFF_DEFAULTS_UPDATED',before,roles.facts(role),actor_id=actor.id)
        changed+=1
    return changed  # Caller owns the one atomic commit/rollback.


def register_commands(app):
    @app.cli.command('staff-defaults-plan')
    def preview():
        try:click.echo(json.dumps(plan(),sort_keys=True))
        finally:db.session.rollback()
    @app.cli.command('staff-defaults-apply')
    @click.option('--expected-fingerprint',required=True)
    @click.option('--admin-user-id',required=True,type=int)
    @click.option('--ack',required=True,type=click.Choice(['approved-existing-team-defaults']))
    def commit(expected_fingerprint,admin_user_id,ack):
        from app.models import User
        try:
            changed=apply(expected_fingerprint,db.session.get(User,admin_user_id))
            db.session.commit();click.echo(json.dumps(dict(changed=changed,status='applied')))
        except Exception:
            db.session.rollback();raise click.ClickException('Update rejected; no partial changes committed.') from None
