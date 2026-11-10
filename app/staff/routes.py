from datetime import datetime
from flask import Blueprint, abort, current_app, render_template, request, redirect, url_for, session
from flask_login import current_user, login_required, login_user
from sqlalchemy.exc import IntegrityError
from app import db
from app.models import User, StaffMembership, StaffInvitation, StaffMembershipBusiness
from app.staff import service, roles as role_service, delivery

staff_bp = Blueprint('staff', __name__, url_prefix='/staff')


@staff_bp.app_context_processor
def staff_workflow_flags():
    from app.staff.sales_access import enabled
    from app.staff.stock_access import enabled as stock_enabled
    from app.staff.operations_access import enabled as operations_enabled
    return dict(staff_sales_enabled=enabled(), staff_stock_enabled=stock_enabled(), staff_operations_enabled=operations_enabled())



def actor():
    return current_user._get_current_object()


def owner_context(now):
    try: access, choices = service.owner_access(actor(), now=now)
    except ValueError: abort(403)
    members = StaffMembership.query.filter_by(owner_id=current_user.id).order_by(StaffMembership.id).all()
    assignments = {m.id:set() for m in members}
    for assignment in StaffMembershipBusiness.query.join(StaffMembership,
            StaffMembership.id == StaffMembershipBusiness.membership_id).filter(StaffMembership.owner_id == current_user.id):
        assignments[assignment.membership_id].add(assignment.business_id)
    roles = role_service.options(current_user.id)
    return dict(choices=choices, seats=service.seat_count(current_user.id, now=now), roles=roles, role_count=len(roles), catalogue=role_service.CATALOGUE,
        memberships=members,
        invitations=StaffInvitation.query.filter_by(owner_id=current_user.id).order_by(StaffInvitation.id.desc()).all(),
        names={u.id:u.full_name for u in User.query.join(StaffMembership, StaffMembership.user_id==User.id).filter(StaffMembership.owner_id==current_user.id)},
        assignments=assignments)



@staff_bp.route('/manage', methods=['GET','POST'])
@login_required
def manage():
    now = datetime.utcnow(); context = owner_context(now)
    if request.method == 'GET': return render_template('staff/manage.html', **context)
    raw = None; invitation_id = None
    try:
        action = request.form.get('action')
        if action == 'role_save':
            role_service.save(actor(), request.form.get('role_key') or None, request.form.get('role_name',''),
                request.form.getlist('permissions'), now=now)
        elif action == 'role_remove':
            role_service.remove(actor(), request.form.get('role_key'), request.form.get('replacement') or None, now=now)
        elif action == 'resend':
            invitation, raw = service.resend(actor(), int(request.form['id']), now=now)
            invitation_id = invitation.id
        elif action == 'invite':
            invitation, raw = service.invite(actor(), request.form.get('email',''), request.form.get('role',''),
                request.form.get('duty_label',''), False, request.form.getlist('business_ids'), now=now)
            invitation_id = invitation.id
        elif action == 'change':
            service.change(actor(), int(request.form['id']), request.form.get('role',''), request.form.get('duty_label',''),
                False, request.form.getlist('business_ids'), now=now)
        elif action == 'revoke': service.revoke(actor(), request.form.get('kind'), int(request.form['id']), now=now)
        elif action == 'reconfirm':
            if request.form.get('confirm') != 'on': raise ValueError('Confirm access restoration explicitly.')
            service.reconfirm(actor(), int(request.form['id']), now=now)
        else: raise ValueError('Choose a staff management action.')
        db.session.commit()
    except (ValueError, KeyError):
        db.session.rollback()
        return render_template('staff/manage.html', error='Staff change rejected. Check the role, assignments, identity and available seats.', **owner_context(now)), 400
    except IntegrityError:
        db.session.rollback()
        return render_template('staff/manage.html', error='Staff change conflicted. No change was saved; review the current list.', **owner_context(now)), 409
    if raw:
        try:
            status = delivery.deliver(invitation_id, raw)
        except Exception:
            # Digest is already committed; a failure must not duplicate a seat or
            # trigger an uncertain SMTP retry. Never log the token/exception.
            db.session.rollback(); status = 'unknown'
        return render_template('staff/manage.html', delivery_result=status, **owner_context(datetime.utcnow()))
    return redirect(url_for('staff.manage'))


@staff_bp.route('/invite', methods=['GET','POST'])
def invite_page():
    if request.method == 'GET': return render_template('staff/invite.html')
    raw = request.form.get('token',''); now=datetime.utcnow()
    try:
        row = service.invitation_for(raw, now=now)
        existing = User.query.filter(db.func.lower(User.email)==row.email).first()
        if request.form.get('action') == 'review':
            return render_template('staff/invite.html', invitation=row, token=raw, existing=bool(existing))
        if request.form.get('action') != 'accept': raise ValueError('Review your invitation first.')
        user, _ = service.accept(raw, actor() if current_user.is_authenticated else None,
            request.form.get('full_name',''), request.form.get('password',''), request.form.get('confirm',''), now=now)
        db.session.commit()
        session.clear(); login_user(user)
        return redirect(url_for('staff.home' if user.email_verified_at else 'auth.verification_pending'))
    except (ValueError, IntegrityError):
        db.session.rollback()
        return render_template('staff/invite.html', error='Invitation could not be accepted. It may be expired, revoked or require the verified invited account. Reopen the private link after signing in.'), 400


@staff_bp.get('/')
@login_required
def home():
    if not current_user.email_verified_at:
        return redirect(url_for('auth.verification_pending'))
    now=datetime.utcnow(); rows=[]
    for membership in StaffMembership.query.filter_by(user_id=current_user.id):
        choices = service.member_businesses(membership, actor(), now=now)
        if choices: rows.append((membership, choices))
    from app.staff.sales_access import enabled
    return render_template('staff/home.html', sales_enabled=enabled(), rows=rows, role_names={(m.owner_id,m.role):role_service.resolve(m.owner_id,m.role).name for m,_ in rows}, role_permissions={(m.owner_id,m.role):role_service.resolve(m.owner_id,m.role).permissions for m,_ in rows})


@staff_bp.after_request
def protect(response):
    response.headers['Cache-Control']='private, no-store'
    # HTTPS CSRF checks require same-origin Referer; external origins receive none.
    response.headers['Referrer-Policy']='same-origin'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'none'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
    return response


def delegated_context(owner, user, now):
    from app.staff import delegation_access as delegated, delegation as policy
    auth, choices = delegated.mutation(owner,user,now=now)
    all_roles = role_service.options(owner.id)
    roles = {}
    for key, value in all_roles.items():
        try:
            if not auth.owner:
                auth.assignment(value['permissions'],auth.businesses)
                delegated.shared(auth,key,value['permissions'],value['permissions'])
            roles[key]=value
        except ValueError: continue
    members=[]; invitations=[]; assigned={}; names={}
    for row in StaffMembership.query.filter_by(owner_id=owner.id).order_by(StaffMembership.id).all():
        try: delegated.target(auth,row)
        except ValueError: continue
        members.append(row);assigned[row.id]=delegated.assignments(row)
        person=db.session.get(User,row.user_id);names[row.user_id]=person.full_name
    for row in StaffInvitation.query.filter_by(owner_id=owner.id).order_by(StaffInvitation.id.desc()).all():
        try: delegated.target(auth,row)
        except ValueError: continue
        invitations.append(row)
    return dict(authority=auth,team_owner=owner,choices=choices,roles=roles,
        role_count=len(all_roles),seats=service.seat_count(owner.id,now=now),
        catalogue=role_service.CATALOGUE,authority_catalogue=policy.CATALOGUE,
        memberships=members,invitations=invitations,assignments=assigned,names=names)


@staff_bp.get('/teams')
@login_required
def teams():
    from app.staff import delegation_access as delegated
    candidates={actor().id}
    candidates.update(m.owner_id for m in StaffMembership.query.filter_by(user_id=actor().id,state='active'))
    available=[];now=datetime.utcnow()
    for owner_id in sorted(candidates):
        owner=db.session.get(User,owner_id)
        if not owner:continue
        try: delegated.mutation(owner,actor(),now=now)
        except ValueError:continue
        available.append(owner)
    return render_template('staff/teams.html',teams=available)


@staff_bp.route('/team/<int:owner_id>',methods=['GET','POST'])
@login_required
def team_manage(owner_id):
    owner=db.session.get(User,owner_id);user=actor();now=datetime.utcnow()
    if not owner:abort(403)
    try:context=delegated_context(owner,user,now)
    except ValueError:abort(403)
    if request.method=='GET':return render_template('staff/team_manage.html',**context)
    raw=None;invitation_id=None
    try:
        if user.id!=owner.id and ('authority_permissions' in request.form):
            raise ValueError('Only the owner may grant administration or export authority.')
        action=request.form.get('action')
        if action=='role_save':
            role_service.save(owner,request.form.get('role_key') or None,request.form.get('role_name',''),
                request.form.getlist('permissions'),now=now,actor=user,
                authority_permissions=request.form.getlist('authority_permissions') if user.id==owner.id else None)
            # A Manager posting authority fields must fail, not have them ignored.
        elif action=='role_remove':role_service.remove(owner,request.form.get('role_key'),request.form.get('replacement') or None,now=now,actor=user)
        elif action=='invite':
            row,raw=service.invite(owner,request.form.get('email',''),request.form.get('role',''),request.form.get('duty_label',''),False,request.form.getlist('business_ids'),now=now,actor=user);invitation_id=row.id
        elif action=='resend':
            row,raw=service.resend(owner,int(request.form['id']),now=now,actor=user);invitation_id=row.id
        elif action=='change':service.change(owner,int(request.form['id']),request.form.get('role',''),request.form.get('duty_label',''),False,request.form.getlist('business_ids'),now=now,actor=user)
        elif action=='revoke':service.revoke(owner,request.form.get('kind'),int(request.form['id']),now=now,actor=user)
        elif action=='reconfirm':
            if request.form.get('confirm')!='on':raise ValueError('Confirmation required.')
            service.reconfirm(owner,int(request.form['id']),now=now,actor=user)
        else:raise ValueError('Action unavailable.')
        db.session.commit()
    except (ValueError,KeyError,IntegrityError):
        db.session.rollback()
        try:context=delegated_context(owner,user,datetime.utcnow())
        except ValueError:abort(403)
        return render_template('staff/team_manage.html',error='Change not saved. Check your permissions, business scope and available seats or roles.',**context),400
    if raw:
        try:status=delivery.deliver(invitation_id,raw,actor_id=user.id)
        except Exception:db.session.rollback();status='unknown'
        try:context=delegated_context(owner,user,datetime.utcnow())
        except ValueError:abort(403)
        return render_template('staff/team_manage.html',delivery_result=status,**context)
    return redirect(url_for('staff.team_manage',owner_id=owner.id))
