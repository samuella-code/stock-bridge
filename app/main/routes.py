from datetime import datetime,time,timedelta
from flask import Blueprint,redirect,render_template,url_for
from flask_login import current_user,login_required
from app.models import Expense,Product,Sale
main_bp=Blueprint("main",__name__)
@main_bp.route("/")
def index(): return redirect(url_for("main.dashboard")) if current_user.is_authenticated else redirect(url_for("subscriptions.index"))
@main_bp.route("/dashboard")
@login_required
def dashboard():
 b=current_user.businesses[0]; today=datetime.utcnow().date(); start=datetime.combine(today,time.min); products=Product.query.filter_by(business_id=b.id).all(); sales_today=Sale.query.filter(Sale.business_id==b.id,Sale.sold_at>=start).all(); expenses=Expense.query.filter(Expense.business_id==b.id,Expense.spent_at>=start).all()
 low=sorted((p for p in products if p.is_low_stock),key=lambda p:p.stock_quantity-p.minimum_stock_level)[:5]
 metrics={"sales_today":sum(s.total for s in sales_today),"expenses_today":sum(e.amount for e in expenses),"gross_profit":sum(s.profit for s in sales_today),"product_count":len(products),"low_stock_count":sum(p.is_low_stock for p in products)}
 all_recent=Sale.query.filter_by(business_id=b.id).order_by(Sale.sold_at.desc()).limit(6).all(); exp_recent=Expense.query.filter_by(business_id=b.id).order_by(Expense.spent_at.desc()).limit(6).all(); recent=[{"title":s.product.name+" sale","date":s.sold_at,"amount":"+₦{:,.2f}".format(s.total)} for s in all_recent]+[{"title":e.description,"date":e.spent_at,"amount":"-₦{:,.2f}".format(e.amount)} for e in exp_recent]; recent=sorted(recent,key=lambda x:x["date"],reverse=True)[:6]
 chart=[]; week_start=start-timedelta(days=6); week_sales=Sale.query.filter(Sale.business_id==b.id,Sale.sold_at>=week_start).all()
 for offset in range(7):
  day=today-timedelta(days=6-offset); total=sum(float(s.total) for s in week_sales if s.sold_at.date()==day); chart.append({"label":day.strftime("%a"),"total":total})
 maximum=max([x["total"] for x in chart]+[1])
 for x in chart: x["height"]=max(3,round(x["total"]/maximum*100))
 return render_template("dashboard.html",business=b,metrics=metrics,recent_transactions=recent,low_stock_products=low,chart=chart)
