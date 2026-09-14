from datetime import datetime,time
from flask import Blueprint,redirect,render_template,url_for
from flask_login import current_user,login_required
from app.models import Expense,Product,Sale
main_bp=Blueprint("main",__name__)
@main_bp.route("/")
def index(): return redirect(url_for("main.dashboard")) if current_user.is_authenticated else redirect(url_for("auth.login"))
@main_bp.route("/dashboard")
@login_required
def dashboard():
 b=current_user.businesses[0]; start=datetime.combine(datetime.utcnow().date(),time.min)
 products=Product.query.filter_by(business_id=b.id).all(); sales=Sale.query.filter(Sale.business_id==b.id,Sale.sold_at>=start).all(); expenses=Expense.query.filter(Expense.business_id==b.id,Expense.spent_at>=start).all()
 low=sorted((p for p in products if p.is_low_stock),key=lambda p:p.stock_quantity-p.minimum_stock_level)[:5]
 metrics={"sales_today":sum(s.total for s in sales),"expenses_today":sum(e.amount for e in expenses),"gross_profit":sum(s.profit for s in sales),"product_count":len(products),"low_stock_count":sum(p.is_low_stock for p in products)}
 recent=[{"title":s.product.name+" sale","date":s.sold_at.strftime("%d %b, %H:%M"),"amount":"+₦{:,.2f}".format(s.total)} for s in sales]+[{"title":e.description,"date":e.spent_at.strftime("%d %b, %H:%M"),"amount":"-₦{:,.2f}".format(e.amount)} for e in expenses]
 return render_template("dashboard.html",business=b,metrics=metrics,recent_transactions=recent[-6:][::-1],low_stock_products=low)
