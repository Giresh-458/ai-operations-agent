from sqlalchemy.orm import Session
from app.models.database_models import Customer

def get_customer_by_id(db: Session, customer_id: int):
    return db.query(Customer).filter(Customer.id == customer_id).first()

def update_customer_email(db: Session, customer_id: int, new_email: str) -> dict:
    # Basic validation is expected to be done prior or here
    if "@" not in new_email:
        raise ValueError("Invalid email format")
        
    customer = get_customer_by_id(db, customer_id)
    if not customer:
        raise ValueError(f"Customer {customer_id} was not found.")
    
    customer.email = new_email
    db.commit()
    db.refresh(customer)
    
    return {
        "customer_id": customer.id,
        "new_email": customer.email,
        "status": "UPDATED"
    }
