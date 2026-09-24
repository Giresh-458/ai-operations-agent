from sqlalchemy.orm import Session
from app.models.database_models import SupportTicket, Customer

def create_support_ticket(db: Session, customer_id: int, issue: str, priority: str) -> dict:
    customer = db.query(Customer).filter(Customer.id == customer_id).first()
    if not customer:
        raise ValueError(f"Customer {customer_id} was not found.")
        
    ticket = SupportTicket(
        customer_id=customer_id,
        issue=issue,
        priority=priority.upper(),
        status="OPEN"
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    
    return {
        "ticket_id": ticket.id,
        "customer_id": ticket.customer_id,
        "priority": ticket.priority,
        "status": ticket.status
    }
