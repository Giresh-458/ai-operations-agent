import os
import sys

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.database import SessionLocal, engine, Base
from app.models.database_models import Customer, Order, SupportTicket, Notification, KnowledgeArticle
import json


def sync_postgres_sequences():
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        for table_name in ("customers", "orders", "support_tickets", "notifications", "tasks", "audit_logs", "knowledge_articles"):
            conn.execute(text(
                f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), "
                f"EXISTS (SELECT 1 FROM {table_name}))"
            ))


def seed_data():
    if engine.dialect.name == "postgresql":
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()

    Base.metadata.create_all(bind=engine)
    # Repair databases seeded by earlier versions before the early return.
    sync_postgres_sequences()
    db = SessionLocal()
    
    if db.query(Customer).count() > 0:
        print("Database already seeded.")
        db.close()
        return

    # Seed Customers
    customers = [
        Customer(id=101, name="Rahul", email="rahul@example.com"),
        Customer(id=102, name="Priya", email="priya@example.com"),
        Customer(id=103, name="Anil", email="anil@example.com"),
        Customer(id=104, name="Neha", email="neha@example.com"),
        Customer(id=105, name="Amit", email="amit@example.com"),
    ]
    db.add_all(customers)
    db.commit()

    # Seed Orders
    orders = [
        Order(id=5001, customer_id=101, status="SHIPPED"),
        Order(id=5002, customer_id=102, status="PROCESSING"),
        Order(id=5003, customer_id=103, status="DELIVERED"),
        Order(id=5004, customer_id=104, status="CANCELLED"),
        Order(id=5005, customer_id=105, status="PENDING"),
    ]
    db.add_all(orders)
    db.commit()
    
    # Seed Tickets
    tickets = [
        SupportTicket(id=1, customer_id=101, issue="Login issue", priority="LOW"),
        SupportTicket(id=2, customer_id=102, issue="Payment failed", priority="HIGH"),
    ]
    db.add_all(tickets)
    db.commit()

    notifications = [
        Notification(id=1, customer_id=101, message="Your order has shipped.", status="SENT"),
        Notification(id=2, customer_id=102, message="Your order is being processed.", status="SENT"),
    ]
    db.add_all(notifications)
    db.commit()
    
    # Seed Knowledge Base
    if db.query(KnowledgeArticle).count() == 0:
        articles = [
            {"title": "Return Policy", "content": "Customers can return items within 30 days of receipt. Items must be in original condition."},
            {"title": "Support SLA", "content": "Critical issues are responded to within 1 hour. High priority within 4 hours. Normal priority within 24 hours."},
            {"title": "Lost Package", "content": "If a package is lost, wait 5 days after the expected delivery date before creating a replacement ticket."}
        ]
        
        from app.config import settings
        from langchain_openai import OpenAIEmbeddings

        if settings.OPENAI_API_KEY and settings.OPENAI_API_KEY != "mock_key":
            try:
                embeddings = OpenAIEmbeddings(api_key=settings.OPENAI_API_KEY)
                embedded_articles = [
                    KnowledgeArticle(
                        title=article["title"],
                        content=article["content"],
                        embedding=embeddings.embed_query(article["content"]),
                    )
                    for article in articles
                ]
                db.add_all(embedded_articles)
                db.commit()
            except Exception as exc:
                db.rollback()
                print(f"Skipping vector embedding generation ({type(exc).__name__}); using lexical retrieval.")
                db.add_all([
                    KnowledgeArticle(title=article["title"], content=article["content"], embedding=None)
                    for article in articles
                ])
                db.commit()
        else:
            db.add_all([
                KnowledgeArticle(title=article["title"], content=article["content"], embedding=None)
                for article in articles
            ])
            db.commit()

    sync_postgres_sequences()


    print("Database seeded successfully.")
    db.close()

if __name__ == "__main__":
    seed_data()
