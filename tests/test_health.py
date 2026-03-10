def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "commander"
    assert data["version"] == "1.0.0"
    assert "database" in data
    assert "polling" in data
    assert isinstance(data["database"], bool)
    assert isinstance(data["polling"], bool)
