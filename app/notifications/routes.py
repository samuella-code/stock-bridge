from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import current_user, login_required
from app import db
from app.models import Notification, NotificationPreference
from app.subscriptions.entitlements import selected_business
from app.notifications.service import destination

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
    row.low_stock_email = request.form.get('low_stock_email') == 'on'
    row.out_of_stock_email = request.form.get('out_of_stock_email') == 'on'
    db.session.commit()
    flash('Preferences saved. Email alerts are not enabled yet; in-app notifications remain available.', 'success')
    return redirect(url_for('notifications.index'))
