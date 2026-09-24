from sqlalchemy.orm import Session
from app.models.database_models import Order

def check_order_status(db: Session, order_id: int) -> dict:
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise ValueError(f"Order {order_id} was not found.")
    
    return {
        "order_id": order.id,
        "status": order.status,
        "customer_id": order.customer_id
    }
