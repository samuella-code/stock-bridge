from datetime import datetime
from decimal import Decimal,InvalidOperation
from flask import Blueprint,flash,redirect,render_template,request,url_for
from flask_login import current_user,login_required
from app import db
from app.models import Expense
expenses_bp=Blueprint("expenses",__name__,url_prefix="/expenses")
@expenses_bp.route("/",methods=["GET","POST"])
@login_required
def index():
 b=current_user.businesses[0]
 if request.method=="POST":
  try: amount=Decimal(request.form["amount"]); spent=datetime.strptime(request.form.get("spent_at",""),"%Y-%m-%d") if request.form.get("spent_at") else datetime.utcnow()
  except (InvalidOperation,ValueError): flash("Enter a valid amount and date.","error"); return redirect(url_for("expenses.index"))
  description=request.form.get("description","").strip()
  if not description or amount<=0: flash("Description and an amount above zero are required.","error")
  else: db.session.add(Expense(business_id=b.id,description=description,amount=amount,spent_at=spent)); db.session.commit(); flash("Expense recorded.","success"); return redirect(url_for("expenses.index"))
 rows=Expense.query.filter_by(business_id=b.id).order_by(Expense.spent_at.desc()).all()
 return render_template("expenses/index.html",business=b,expenses=rows,total=sum(x.amount for x in rows))
