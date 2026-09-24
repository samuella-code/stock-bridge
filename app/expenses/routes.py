from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from app import db
from app.models import Expense
from app.admin.routes import log

expenses_bp = Blueprint("expenses", __name__, url_prefix="/expenses")
CATEGORIES = ("Transportation", "Electricity", "Rent", "Packaging", "Wages",
              "Repairs", "Marketing", "Internet/Data", "Equipment", "Miscellaneous")

@expenses_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    b = current_user.businesses[0]
    if request.method == "POST":
        try:
            amount = Decimal(request.form["amount"])
            spent = datetime.strptime(request.form["spent_at"], "%Y-%m-%d") if request.form.get("spent_at") else datetime.utcnow()
            if not amount.is_finite() or amount <= 0: raise ValueError
        except (KeyError, InvalidOperation, ValueError):
            flash("Enter a valid amount above zero and a valid date.", "error")
            return redirect(url_for("expenses.index"))
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "Miscellaneous")
        if not description or len(description) > 180 or category not in CATEGORIES:
            flash("Enter a description and choose an expense category.", "error")
        else:
            row=Expense(business_id=b.id, description=description, amount=amount,
                category=category, note=request.form.get("note", "").strip()[:500], spent_at=spent)
            db.session.add(row)
            db.session.flush()
            log("EXPENSE_CREATED", f"Expense {row.id} created.", actor=current_user, business_id=b.id)
            db.session.commit()
            flash("Expense recorded.", "success")
            return redirect(url_for("expenses.index"))
    rows = Expense.query.filter_by(business_id=b.id).order_by(Expense.spent_at.desc(), Expense.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False)
    total = db.session.query(func.coalesce(func.sum(Expense.amount),0)).filter(Expense.business_id == b.id, Expense.voided_at.is_(None)).scalar()
    return render_template("expenses/index.html", business=b, expenses=rows, total=total, categories=CATEGORIES)

@expenses_bp.post("/<int:expense_id>/delete")
@login_required
def delete(expense_id):
    b = current_user.businesses[0]
    row = db.session.get(Expense, expense_id)
    if not row or row.business_id != b.id: abort(404)
    reason = request.form.get("reason", "").strip()
    if row.voided_at:
        flash("This expense is already voided.", "warning")
        return redirect(url_for("expenses.index"))
    if not reason:
        flash("Give a reason before voiding an expense.", "error")
        return redirect(url_for("expenses.index"))
    row.voided_at = datetime.utcnow()
    row.void_reason = reason[:300]
    db.session.commit()
    flash("Expense voided; its history is preserved.", "success")
    return redirect(url_for("expenses.index"))
