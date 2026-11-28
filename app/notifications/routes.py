from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, abort, session
from flask_login import current_user, login_required
from app import db
from app.models import Business, Product, Notification, NotificationPreference
from app.subscriptions.entitlements import selected_business
from app.businesses.service import accessible_business
from app.notifications.service import destination
from app.notifications.email_outbox import PREFERENCE_FIELDS, dispatch_one

notifications_bp = Blueprint('notifications', __name__, url_prefix='/notifications')


@notifications_bp.after_request
def private_notifications(response):
    response.headers['Cache-Control'] = 'no-store, private'
    return response


def scoped():
    business = selected_business(current_user)
    return Notification.query.filter_by(user_id=current_user.id, business_id=business.id)


@notifications_bp.get('/')
@login_required
def index():
    business = selected_business(current_user)
    page = scoped().order_by(Notification.created_at.desc(), Notification.id.desc()).paginate(
        page=request.args.get('page', 1, type=int), per_page=25, error_out=False)
    return render_template('notifications/index.html', business=business, notifications=page,
        notification_destination=destination,
        preferences=db.session.get(NotificationPreference, business.id))


@notifications_bp.post('/<int:notification_id>/read')
@login_required
def read(notification_id):
    row = scoped().filter_by(id=notification_id).first_or_404()
    if row.read_at is None:
        row.read_at = datetime.utcnow()
        db.session.commit()
    return redirect(url_for('notifications.index'))


@notifications_bp.post('/read-all')
@login_required
def read_all():
    scoped().filter(Notification.read_at.is_(None)).update({Notification.read_at: datetime.utcnow()}, synchronize_session=False)
    db.session.commit()
    return redirect(url_for('notifications.index'))


@notifications_bp.post('/preferences')
@login_required
def preferences():
    business = selected_business(current_user)
    row = db.session.get(NotificationPreference, business.id)
    if row is None:
        row = NotificationPreference(business_id=business.id)
        db.session.add(row)
    if any(request.form.get(field) not in (None, 'on') for field in PREFERENCE_FIELDS):
        db.session.rollback()
        abort(400)
    for field in PREFERENCE_FIELDS:
        setattr(row, field, request.form.get(field) == 'on')
    db.session.commit()
    flash('Notification preferences saved. Low-stock and out-of-stock in-app alerts remain enabled.', 'success')
    return redirect(url_for('notifications.index'))


@notifications_bp.post('/<int:notification_id>/open')
@login_required
def open_notification(notification_id):
    row = scoped().filter_by(id=notification_id).first_or_404()
    if row.read_at is None:
        row.read_at = datetime.utcnow()
        db.session.commit()
    target = destination(row)
    if request.accept_mimetypes.best == 'application/json':
        return jsonify(destination=target, unread_count=scoped().filter(Notification.read_at.is_(None)).count())
    return redirect(target)


@notifications_bp.get('/email/summary/<int:business_id>/<summary_type>')
@login_required
def email_summary_link(business_id, summary_type):
    """Restore the business that owns an emailed summary before opening its page."""
    if summary_type not in {'daily', 'weekly'}:
        abort(404)
    business = Business.query.filter_by(id=business_id, user_id=current_user.id).first()
    if business is None:
        abort(404)
    if not accessible_business(current_user, business):
        # Suspended or no-longer-entitled businesses must not become active
        # merely because an old email still exists.
        return redirect(url_for('businesses.index'))
    session['active_business_id'] = business.id
    target = 'restocking.index' if summary_type == 'daily' else 'main.dashboard'
    return redirect(url_for(target))


@notifications_bp.get('/email/product/<int:business_id>/<int:product_id>')
@login_required
def email_product_link(business_id, product_id):
    """Restore the email's business context before opening its product action."""
    business = Business.query.filter_by(id=business_id, user_id=current_user.id).first()
    product = Product.query.filter_by(id=product_id, business_id=business_id, active=True).first()
    if not business or not product or not accessible_business(current_user, business):
        abort(404)
    session['active_business_id'] = business.id
    if request.args.get('action') == 'restock':
        return redirect(url_for('restocking.index', product_id=product.id))
    return redirect(url_for('products.detail', product_id=product.id))


@notifications_bp.post('/email-dispatch')
@login_required
def email_dispatch():
    # Browser trigger cannot dispatch another customer's or another business's jobs.
    business = selected_business(current_user)
    return jsonify(result=dispatch_one(current_user.id, business.id))
