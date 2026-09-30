"""Render real business templates against disposable data for JS DOM checks."""
import io
from pathlib import Path
import sys
from datetime import datetime
from app import create_app, db
from app.models import User, Business

def main():
    destination=Path(sys.argv[1]);destination.mkdir(parents=True,exist_ok=True)
    app=create_app({'TESTING':True,'SECRET_KEY':'dom-test','SQLALCHEMY_DATABASE_URI':'sqlite:///:memory:'})
    with app.app_context():
        db.create_all()
        user=User(full_name='Demo',email='demo@example.invalid',email_verified_at=datetime.utcnow());user.set_password('test-password')
        db.session.add(user);db.session.flush();db.session.add(Business(user_id=user.id,name='Demo',subscription_plan='lifetime',subscription_status='active'));db.session.commit()
        client=app.test_client();client.post('/auth/login',data={'email':user.email,'password':'test-password'})
        client.post('/products/new',data={'name':'Coca-Cola','barcode':'001234','stock_quantity':20,'buying_price':250,'selling_price':350})
        for name,url in [('quick','/products/quick-add'),('sale','/sales/'),('restock','/restocking/'),('scan','/products/scan'),('products','/products/'),('product-form','/products/new')]:
            response=client.get(url);assert response.status_code==200
            (destination/f'{name}.html').write_bytes(response.data)
        csv='Product Name\n'+'\n'.join(f'P{i}' for i in range(51))
        response=client.post('/products/import',data={'spreadsheet':(io.BytesIO(csv.encode()),'p.csv')});assert response.status_code==200
        (destination/'preview.html').write_bytes(response.data)
if __name__=='__main__':main()
