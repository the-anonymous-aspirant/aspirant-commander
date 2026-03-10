import uuid


class TestListTasks:
    def test_list_tasks_empty(self, client):
        response = client.get("/tasks")
        assert response.status_code == 200

        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["page_size"] == 20

    def test_list_tasks_with_filters(self, client, db_session):
        from app.models import CommanderTask

        task = CommanderTask(
            voice_message_id=uuid.uuid4(),
            title="Test task",
            priority="high",
            status="open",
            label="work",
        )
        db_session.add(task)
        db_session.commit()

        # Filter by status
        response = client.get("/tasks?status=open")
        assert response.status_code == 200
        assert response.json()["total"] == 1

        # Filter by non-matching status
        response = client.get("/tasks?status=closed")
        assert response.status_code == 200
        assert response.json()["total"] == 0

        # Filter by priority
        response = client.get("/tasks?priority=high")
        assert response.status_code == 200
        assert response.json()["total"] == 1

        # Filter by label
        response = client.get("/tasks?label=work")
        assert response.status_code == 200
        assert response.json()["total"] == 1


class TestGetTask:
    def test_get_task_not_found(self, client):
        fake_id = uuid.uuid4()
        response = client.get(f"/tasks/{fake_id}")
        assert response.status_code == 404

        data = response.json()
        assert data["error"]["code"] == "not_found"


class TestUpdateTask:
    def test_update_task_not_found(self, client):
        fake_id = uuid.uuid4()
        response = client.patch(f"/tasks/{fake_id}", json={"status": "closed"})
        assert response.status_code == 404

        data = response.json()
        assert data["error"]["code"] == "not_found"

    def test_update_task_status_to_closed(self, client, db_session):
        from app.models import CommanderTask

        task = CommanderTask(
            voice_message_id=uuid.uuid4(),
            title="Task to close",
            status="open",
        )
        db_session.add(task)
        db_session.commit()
        task_id = task.id

        response = client.patch(f"/tasks/{task_id}", json={"status": "closed"})
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "closed"
        assert data["closed_at"] is not None

    def test_update_task_reopen_clears_closed_at(self, client, db_session):
        from datetime import datetime, timezone
        from app.models import CommanderTask

        task = CommanderTask(
            voice_message_id=uuid.uuid4(),
            title="Closed task",
            status="closed",
            closed_at=datetime.now(timezone.utc),
        )
        db_session.add(task)
        db_session.commit()
        task_id = task.id

        response = client.patch(f"/tasks/{task_id}", json={"status": "open"})
        assert response.status_code == 200

        data = response.json()
        assert data["status"] == "open"
        assert data["closed_at"] is None


class TestDeleteTask:
    def test_delete_task_not_found(self, client):
        fake_id = uuid.uuid4()
        response = client.delete(f"/tasks/{fake_id}")
        assert response.status_code == 404

        data = response.json()
        assert data["error"]["code"] == "not_found"

    def test_delete_task_success(self, client, db_session):
        from app.models import CommanderTask

        task = CommanderTask(
            voice_message_id=uuid.uuid4(),
            title="Task to delete",
        )
        db_session.add(task)
        db_session.commit()
        task_id = task.id

        response = client.delete(f"/tasks/{task_id}")
        assert response.status_code == 204

        # Verify it is gone
        response = client.get(f"/tasks/{task_id}")
        assert response.status_code == 404


class TestProcess:
    def test_process_returns_expected_shape(self, client):
        response = client.post("/process")
        assert response.status_code == 200

        data = response.json()
        assert "message" in data
        assert "processed_count" in data
        assert isinstance(data["processed_count"], int)


class TestVocabulary:
    def test_vocabulary_returns_reference(self, client):
        response = client.get("/vocabulary")
        assert response.status_code == 200

        data = response.json()
        assert "grammar" in data
        assert "operations" in data
        assert isinstance(data["operations"], list)
        assert "tables" in data
        assert "task" in data["tables"]
        assert "note" in data["tables"]
        assert "priorities" in data
        assert isinstance(data["priorities"], list)
        assert "examples" in data
        assert isinstance(data["examples"], list)
        assert len(data["examples"]) > 0


class TestListNotes:
    def test_list_notes_empty(self, client):
        response = client.get("/notes")
        assert response.status_code == 200

        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1
        assert data["page_size"] == 20

    def test_list_notes_with_filters(self, client, db_session):
        from app.models import CommanderNote

        note = CommanderNote(
            voice_message_id=uuid.uuid4(),
            content="Test diary entry",
            mood="positive",
            tag="work",
        )
        db_session.add(note)
        db_session.commit()

        # Filter by mood
        response = client.get("/notes?mood=positive")
        assert response.status_code == 200
        assert response.json()["total"] == 1

        # Filter by non-matching mood
        response = client.get("/notes?mood=negative")
        assert response.status_code == 200
        assert response.json()["total"] == 0

        # Filter by tag
        response = client.get("/notes?tag=work")
        assert response.status_code == 200
        assert response.json()["total"] == 1


class TestGetNote:
    def test_get_note_not_found(self, client):
        fake_id = uuid.uuid4()
        response = client.get(f"/notes/{fake_id}")
        assert response.status_code == 404

        data = response.json()
        assert data["error"]["code"] == "not_found"


class TestUpdateNote:
    def test_update_note_not_found(self, client):
        fake_id = uuid.uuid4()
        response = client.patch(f"/notes/{fake_id}", json={"content": "updated"})
        assert response.status_code == 404

    def test_update_note_content(self, client, db_session):
        from app.models import CommanderNote

        note = CommanderNote(
            voice_message_id=uuid.uuid4(),
            content="Original content",
        )
        db_session.add(note)
        db_session.commit()
        note_id = note.id

        response = client.patch(f"/notes/{note_id}", json={"content": "Updated content", "tag": "updated"})
        assert response.status_code == 200

        data = response.json()
        assert data["content"] == "Updated content"
        assert data["tag"] == "updated"


class TestDeleteNote:
    def test_delete_note_not_found(self, client):
        fake_id = uuid.uuid4()
        response = client.delete(f"/notes/{fake_id}")
        assert response.status_code == 404

    def test_delete_note_success(self, client, db_session):
        from app.models import CommanderNote

        note = CommanderNote(
            voice_message_id=uuid.uuid4(),
            content="Note to delete",
        )
        db_session.add(note)
        db_session.commit()
        note_id = note.id

        response = client.delete(f"/notes/{note_id}")
        assert response.status_code == 204

        # Verify it is gone
        response = client.get(f"/notes/{note_id}")
        assert response.status_code == 404
