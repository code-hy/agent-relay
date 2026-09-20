"""Q2 acceptance scenario 1 as an API integration test against the real API + DB.

Register two agents, sender sends a task, recipient claims and completes it,
sender reads the result. Asserts sender sees status == completed.
"""
import os
os.environ.setdefault("RELAY_DATABASE_URL", "sqlite:////tmp/agent-relay-test.db")

from fastapi.testclient import TestClient
import main
from database import Base, engine


def _register(client: TestClient, name: str):
    r = client.post("/api/v1/agents", json={"name": name})
    assert r.status_code == 201, r.text
    data = r.json()
    return data, {"Authorization": f"Bearer {data['token']}"}


def test_q2_two_agents_exchange_task_and_result():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        with TestClient(main.app) as client:
            sender, sender_headers = _register(client, "alice-sender")
            recipient, recipient_headers = _register(client, "bob-worker")

            sent = client.post(
                "/api/v1/tasks",
                headers=sender_headers,
                json={"to": recipient["agent_id"], "input": "Review this Python function: def add(a,b): return a+b"},
            )
            assert sent.status_code == 201, sent.text
            task_id = sent.json()["task_id"]
            assert sent.json()["status"] == "queued"

            claim = client.post(
                "/api/v1/tasks/claim",
                headers=recipient_headers,
                json={"worker_id": "bob-laptop-1", "wait_seconds": 0},
            )
            assert claim.status_code == 200, claim.text
            claim_data = claim.json()
            assert claim_data["task_id"] == task_id

            done = client.post(
                f"/api/v1/tasks/{task_id}/complete",
                headers=recipient_headers,
                json={"claim_token": claim_data["claim_token"], "output": "Looks good"},
            )
            assert done.status_code == 200, done.text
            assert done.json()["status"] == "completed"

            # Sender polls/reads result
            view = client.get(f"/api/v1/tasks/{task_id}", headers=sender_headers)
            assert view.status_code == 200, view.text
            body = view.json()
            assert body["status"] == "completed"
            assert body["output"] == "Looks good"

            # Dashboard serves
            dash = client.get("/")
            assert dash.status_code == 200
            assert "Agent Relay" in dash.text
    finally:
        Base.metadata.drop_all(engine)
