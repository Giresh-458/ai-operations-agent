from sqlalchemy.orm import Session
from app.models.database_models import Notification, Customer

def send_notification(db: Session, customer_id: int, message: str) -> dict:
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise ValueError(f"Customer {customer_id} was not found.")
        
    notification = Notification(
        customer_id=customer_id,
        message=message,
        status="SENT"
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    
    return {
        "notification_id": notification.id,
        "status": notification.status
    }
