from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for, send_file
from flask_login import current_user, login_required
from app import db
from app.models import Business
from app.subscriptions.entitlements import effective_access, selected_business
from app.businesses.service import (owned_businesses, accessible_business, selection_required,
    creation_token, create_business, choose_primary)

businesses_bp = Blueprint('businesses', __name__, url_prefix='/businesses')


@businesses_bp.errorhandler(413)
def oversized_upload(error):
    if request.endpoint == 'businesses.logo_upload':
        flash('Choose an image no larger than 5 MB (5 MiB).', 'error')
        return redirect(url_for('businesses.profile', business_id=request.view_args['business_id']))
    return error


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


def profile_business(business_id, writing=False):
    business = Business.query.filter_by(id=business_id, user_id=current_user.id).first_or_404()
    if not current_user.email_verified_at:
        abort(403)
    if not accessible_business(current_user, business):
        abort(403)
    if writing and not business.has_write_access:
        abort(403)
    return business


@businesses_bp.get('/<int:business_id>/profile')
@login_required
def profile(business_id):
    business = profile_business(business_id)
    from app.businesses.images import storage_configured
    return render_template('businesses/profile.html', business=business,
        storage_available=storage_configured())


@businesses_bp.post('/<int:business_id>/logo')
@login_required
def logo_upload(business_id):
    business = profile_business(business_id, writing=True)
    from app.businesses.images import image_storage, validate_image, new_key, cleanup, verify_upload
    upload = request.files.get('logo')
    if not upload:
        flash('Choose a JPEG, PNG or WebP image.', 'error')
        return redirect(url_for('businesses.profile', business_id=business.id))
    try:
        data = validate_image(upload)
    except ValueError as error:
        flash(str(error), 'error')
        return redirect(url_for('businesses.profile', business_id=business.id))
    key, old_key, storage = new_key(business.id), None, None
    try:
        storage = image_storage()
        verify_upload(storage, key, data, business.id)
        # Serialize replacements so each one retires the key it actually replaced.
        business = Business.query.filter_by(id=business.id, user_id=current_user.id).with_for_update().populate_existing().one()
        if not accessible_business(current_user, business) or not business.has_write_access:
            abort(403)
        old_key = business.logo_key
        business.logo_key = key
        db.session.commit()
    except Exception:
        db.session.rollback()
        if storage:
            cleanup(storage, key, business_id)
        flash('The image could not be saved. Your previous image is unchanged. Please try again later.', 'error')
        return redirect(url_for('businesses.profile', business_id=business_id))
    if not cleanup(storage, old_key, business_id):
        flash('Your new image is saved. Previous-image cleanup is pending; support can safely retry it.', 'warning')
    flash('Business image updated.', 'success')
    return redirect(url_for('businesses.profile', business_id=business.id))


@businesses_bp.post('/<int:business_id>/logo/remove')
@login_required
def logo_remove(business_id):
    business = profile_business(business_id, writing=True)
    business = Business.query.filter_by(id=business.id, user_id=current_user.id).with_for_update().populate_existing().one()
    old_key = business.logo_key
    business.logo_key = None
    db.session.commit()
    if old_key:
        from app.businesses.images import image_storage, cleanup
        try:
            cleanup(image_storage(), old_key, business_id)
        except Exception:
            current_app.logger.warning('Business image cleanup deferred; review storage lifecycle.')
    flash('Business image removed.', 'success')
    return redirect(url_for('businesses.profile', business_id=business.id))


@businesses_bp.get('/<int:business_id>/logo')
@login_required
def logo(business_id):
    business = profile_business(business_id)
    if not business.logo_key or not business.logo_key.startswith(f'businesses/{business.id}/'):
        abort(404)
    from app.businesses.images import image_storage
    import io
    try:
        data = image_storage().get(business.logo_key)
    except Exception:
        abort(404)
    response = send_file(io.BytesIO(data), mimetype='image/webp', max_age=0)
    response.headers['Cache-Control'] = 'no-store, private'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response
