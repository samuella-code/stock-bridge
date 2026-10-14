"""Account-scoped, append-only guided-tour lifecycle; no business mutations."""
from sqlalchemy import exists, update
from app import db
from app.models import AuditLog, User, Business, Product, Sale, Restock, Expense

ACTIONS = ('CUSTOMER_TOUR_PROGRESS', 'CUSTOMER_TOUR_SKIPPED', 'CUSTOMER_TOUR_FINISHED')


def tour_state(user):
    if user.role != 'user' or user.admin_enabled or not user.email_verified_at or user.suspended_at:
        return {'eligible':False}
    terminal = AuditLog.query.filter(AuditLog.actor_id == user.id, AuditLog.target_type == 'user',
        AuditLog.target_id == user.id, AuditLog.action.in_(ACTIONS[1:])).order_by(AuditLog.id.desc()).first()
    if terminal:
        return {'eligible':False, 'status':terminal.action, 'version':terminal.id}
    latest = AuditLog.query.filter_by(actor_id=user.id, target_type='user', target_id=user.id,
        action=ACTIONS[0]).order_by(AuditLog.id.desc()).first()
    if latest:
        try: step = int(latest.description)
        except ValueError: return {'eligible':False}  # Do not guess corrupt progress.
        if not 0 <= step < 7: return {'eligible':False}
        return {'eligible':True, 'started':True, 'step':step, 'version':latest.id}
    owned = db.session.query(Business.id).filter(Business.user_id == user.id)
    used = db.session.query(*[exists().where(model.business_id.in_(owned))
        for model in (Product, Sale, Restock, Expense)]).one()
    return {'eligible':not any(used), 'started':False, 'step':0, 'version':0}


def record_tour(user, action, step, version):
    # Serializes concurrent state transitions on PostgreSQL and SQLite.
    locked = db.session.execute(update(User).where(User.id == user.id, User.role == 'user',
        User.admin_enabled.is_(False), User.suspended_at.is_(None)).values(full_name=User.full_name)
        .returning(User.id), execution_options={'synchronize_session':False}).scalar_one_or_none()
    if locked is None: raise PermissionError
    state = tour_state(user)
    terminal = {'skip':ACTIONS[1], 'finish':ACTIONS[2]}
    if not state['eligible']:
        if state.get('status') == terminal.get(action): return state
        raise ValueError('Tour is unavailable. Refresh the dashboard.')
    if state['version'] != version:
        raise ValueError('Tour progress changed. Refresh the dashboard.')
    if action == 'start' and not state['started'] and step == 0:
        event = ACTIONS[0]
    elif action == 'progress' and state['started'] and abs(step-state['step']) == 1:
        event = ACTIONS[0]
    elif action == 'skip': event = ACTIONS[1]
    elif action == 'finish' and state['started'] and state['step'] == 6 and step == 6:
        event = ACTIONS[2]
    else: raise ValueError('Invalid tour transition.')
    db.session.add(AuditLog(actor_id=user.id, target_type='user', target_id=user.id,
                           action=event, description=str(step)))
    db.session.flush()
    return tour_state(user)
