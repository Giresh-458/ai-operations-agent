import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["OPENAI_API_KEY"] = "mock_key"

import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import Base, engine
from scripts.seed_database import seed_data

# We will use the SQLite in-memory for testing, but let's just stick to the configured test db or SQLite.
# Actually, since sqlalchemy is setup, we can just use the provided DB in settings but it's risky to clear it.
# For simplicity, we just use the real db assuming it's a test environment, or use the seeded data.
# The user said "Mock OpenAI API calls where appropriate. Tests should not require a real API call."
# Our config.py sets OPENAI_API_KEY="mock_key" by default and nodes.py handles it!

client = TestClient(app)

@pytest.fixture(scope="session", autouse=True)
def setup_db():
    # Setup test database
    Base.metadata.create_all(bind=engine)
    seed_data()
    yield

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_check_order():
    response = client.post("/tasks", json={"request": "Check the status of order 5001"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert data["intent"] == "CHECK_ORDER"
    assert data["result"]["message"] == "Order 5001 is SHIPPED."

def test_create_ticket_human_approval():
    # "critical" triggers human approval mock logic
    response = client.post("/tasks", json={"request": "Create a critical support ticket for customer 101"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "WAITING_FOR_APPROVAL"
    assert data["intent"] == "CREATE_TICKET"
    
    task_id = data["task_id"]
    
    # Approve it
    approve_res = client.post(f"/tasks/{task_id}/approve")
    assert approve_res.status_code == 200
    approve_data = approve_res.json()
    assert approve_data["status"] == "COMPLETED"
    assert "ticket_id" in approve_data["result"]

def test_update_email():
    response = client.post("/tasks", json={"request": "Update email for customer 101 to example@gmail.com"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert data["intent"] == "UPDATE_EMAIL"
    
def test_send_notification():
    response = client.post("/tasks", json={"request": "Send notification to customer 101 say Hello"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert data["intent"] == "SEND_NOTIFICATION"
