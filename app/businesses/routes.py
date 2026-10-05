from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from app import db
from app.models import Business
from app.subscriptions.entitlements import effective_access, selected_business
from app.businesses.service import (owned_businesses, accessible_business, selection_required,
    creation_token, create_business, choose_primary)

businesses_bp = Blueprint('businesses', __name__, url_prefix='/businesses')


@businesses_bp.get('/')
@login_required
def index():
    access = effective_access(current_user) if current_app.config.get('SUBSCRIPTIONS_ENABLED') else None
    businesses = owned_businesses(current_user)
    choosing = selection_required(current_user, access)
    return render_template('businesses/index.html', businesses=businesses,
        active=selected_business(current_user), choosing=choosing,
        allowed={b.id for b in businesses if accessible_business(current_user, b, access)},
        can_add=bool(access and access.can_write and len(businesses) < access.business_limit),
        can_choose=bool(access and access.business_limit == 1 and len(businesses) > 1))


@businesses_bp.route('/new', methods=['GET', 'POST'])
@login_required
def create():
    if not current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        abort(404)
    if request.method == 'POST':
        try:
            business = create_business(current_user, request.form.get('name', ''), request.form.get('creation_token', ''))
            db.session.commit()
            session['active_business_id'] = business.id
            flash('Business added. Its inventory and reports start empty.', 'success')
            return redirect(url_for('main.dashboard'))
        except ValueError as error:
            db.session.rollback()
            flash(str(error), 'warning')
            return redirect(url_for('businesses.index'))
        except Exception:
            db.session.rollback()
            raise
    access = effective_access(current_user)
    if not access.can_write or Business.query.filter_by(user_id=current_user.id).count() >= access.business_limit:
        flash('Your plan’s business limit has been reached. Manage your plan to add another business.', 'warning')
        return redirect(url_for('businesses.index'))
    return render_template('businesses/form.html', editing=None, creation_token=creation_token(current_user))


@businesses_bp.post('/<int:business_id>/switch')
@login_required
def switch(business_id):
    business = Business.query.filter_by(id=business_id, user_id=current_user.id).first_or_404()
    if not accessible_business(current_user, business):
        flash('This business is locked. Choose your active business or upgrade to Plus.', 'warning')
        return redirect(url_for('businesses.index'))
    session['active_business_id'] = business.id
    return redirect(url_for('main.dashboard'))


@businesses_bp.post('/<int:business_id>/choose')
@login_required
def choose(business_id):
    if not current_app.config.get('SUBSCRIPTIONS_ENABLED'):
        abort(404)
    try:
        business = choose_primary(current_user, business_id)
        if not business:
            db.session.rollback()
            abort(404)
        db.session.commit()
        session['active_business_id'] = business.id
    except ValueError as error:
        db.session.rollback()
        flash(str(error), 'warning')
        return redirect(url_for('businesses.index'))
    except Exception:
        db.session.rollback()
        raise
    return redirect(url_for('main.dashboard'))


@businesses_bp.route('/<int:business_id>/edit', methods=['GET', 'POST'])
@login_required
def edit(business_id):
    business = Business.query.filter_by(id=business_id, user_id=current_user.id).first_or_404()
    if not accessible_business(current_user, business) or not business.has_write_access:
        flash('This business is currently restricted. Your records are preserved.', 'warning')
        return redirect(url_for('businesses.index'))
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name or len(name) > 140:
            flash('Enter a business name of 1–140 characters.', 'error')
        else:
            business.name = name
            db.session.commit()
            flash('Business updated.', 'success')
            return redirect(url_for('businesses.index'))
    return render_template('businesses/form.html', editing=business)
