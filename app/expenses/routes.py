from app.subscriptions.entitlements import selected_business
from datetime import datetime
from decimal import Decimal, InvalidOperation
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from app import db
from app.models import Expense
from app.admin.routes import log
from app.transaction_submissions import issue_token, claim, complete, SubmissionError, SubmissionConflict, SubmissionIncomplete

expenses_bp = Blueprint("expenses", __name__, url_prefix="/expenses")
CATEGORIES = ("Transportation", "Electricity", "Rent", "Packaging", "Wages",
              "Repairs", "Marketing", "Internet/Data", "Equipment", "Miscellaneous")

@expenses_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    b = selected_business(current_user)
    token = request.form.get("submission_token", "") if request.method == "POST" else issue_token(current_user.id, b.id, "expense")
    if request.method == "POST":
        try:
            amount = Decimal(request.form["amount"])
            spent = datetime.strptime(request.form["spent_at"], "%Y-%m-%d") if request.form.get("spent_at") else None
            if not amount.is_finite() or amount <= 0: raise ValueError
        except (KeyError, InvalidOperation, ValueError):
            flash("Enter a valid amount above zero and a valid date.", "error")
            return _expense_form(b, token)
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "Miscellaneous")
        if not description or len(description) > 180 or category not in CATEGORIES:
            flash("Enter a description and choose an expense category.", "error")
            return _expense_form(b, token)
        note = request.form.get("note", "").strip()[:500]
        normalized_amount = format(amount, "f")
        if "." in normalized_amount:
            normalized_amount = normalized_amount.rstrip("0").rstrip(".")
        payload = dict(description=description, category=category, amount=normalized_amount,
                       spent_at=spent.strftime("%Y-%m-%d") if spent else None, note=note)
        try:
            receipt, replay = claim(token, current_user.id, b.id, "expense", payload)
            if replay:
                row = Expense.query.filter_by(id=int(receipt.result_reference.split(":")[1]), business_id=b.id).first()
                if row is None:
                    raise SubmissionConflict("Original expense is unavailable.")
                db.session.rollback()
                flash("This expense was already recorded. No expense was added again.", "success")
                return redirect(url_for("expenses.index"))
            row = Expense(business_id=b.id, description=description, amount=amount,
                category=category, note=note, spent_at=spent or datetime.utcnow())
            db.session.add(row)
            db.session.flush()
            log("EXPENSE_CREATED", f"Expense {row.id} created.", actor=current_user, business_id=b.id)
            complete(receipt, f"expense:{row.id}")
            db.session.commit()
        except SubmissionIncomplete:
            db.session.rollback()
            flash("This submission has no confirmed completed result. Check expense history before submitting again.", "error")
            return _expense_form(b, token, 409)
        except SubmissionConflict:
            db.session.rollback()
            flash("This submission was already used with different contents, or its original expense is unavailable. Check expense history.", "error")
            return _expense_form(b, token, 409)
        except SubmissionError as error:
            db.session.rollback()
            flash("Submission could not be accepted. Check expense history before refreshing the form or submitting again.", "error")
            return _expense_form(b, token, 409 if isinstance(error, SubmissionConflict) else 400)
        except Exception:
            db.session.rollback()
            raise
        flash("Expense recorded.", "success")
        return redirect(url_for("expenses.index"))
    return _expense_form(b, token)

def _expense_form(b, token, status=200):
    rows = Expense.query.filter_by(business_id=b.id).order_by(Expense.spent_at.desc(), Expense.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False)
    total = db.session.query(func.coalesce(func.sum(Expense.amount),0)).filter(Expense.business_id == b.id, Expense.voided_at.is_(None)).scalar()
    return render_template("expenses/index.html", business=b, expenses=rows, total=total, categories=CATEGORIES, submission_token=token), status

@expenses_bp.post("/<int:expense_id>/delete")
@login_required
def delete(expense_id):
    b = selected_business(current_user)
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
