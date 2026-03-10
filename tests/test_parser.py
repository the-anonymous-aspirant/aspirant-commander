from datetime import date

from app.parser import (
    parse_transcription,
    preprocess_lightweight,
    strip_filler_words,
    _normalize_priority,
    _split_commands,
    _extract_dimensions,
)


class TestPreprocessLightweight:
    def test_lowercases_text(self):
        assert preprocess_lightweight("HELLO WORLD") == "hello world"

    def test_strips_punctuation(self):
        text = "Hello, world! This is a test."
        result = preprocess_lightweight(text)
        assert "," not in result
        assert "!" not in result
        assert "." not in result
        assert result == "hello world this is a test"

    def test_collapses_whitespace(self):
        assert preprocess_lightweight("  hello   world  ") == "hello world"

    def test_preserves_filler_words(self):
        text = "uh um basically create a task"
        result = preprocess_lightweight(text)
        assert "uh" in result
        assert "um" in result
        assert "basically" in result


class TestStripFillerWords:
    def test_strips_single_word_fillers(self):
        text = "uh um basically create a task"
        result = strip_filler_words(text)
        assert "uh" not in result
        assert "um" not in result
        assert "basically" not in result
        assert "create a task" in result

    def test_strips_multiword_fillers(self):
        text = "i mean you know this is the task"
        result = strip_filler_words(text)
        assert "i mean" not in result
        assert "you know" not in result

    def test_collapses_whitespace_after_removal(self):
        result = strip_filler_words("uh hello um world")
        assert result == "hello world"


class TestSplitCommands:
    def test_single_command(self):
        text = "command create task fix the bug"
        segments = _split_commands(text)
        assert len(segments) == 1
        assert segments[0].startswith("create task")

    def test_multiple_commands(self):
        text = "command create task first command delete task 1"
        segments = _split_commands(text)
        assert len(segments) == 2

    def test_text_before_first_command_ignored(self):
        text = "hello there command create task fix bug"
        segments = _split_commands(text)
        assert len(segments) == 1
        assert "hello" not in segments[0]

    def test_no_commands(self):
        text = "this is just plain speech"
        segments = _split_commands(text)
        assert len(segments) == 0

    def test_command_word_not_followed_by_crud_not_split(self):
        text = "command create task use the command line tool"
        segments = _split_commands(text)
        assert len(segments) == 1
        assert "command line tool" in segments[0]


class TestExtractDimensions:
    def test_single_dimension(self):
        text = "dimension priority value high"
        preamble, dims = _extract_dimensions(text, "task")
        assert dims["priority"] == "high"
        assert preamble == ""

    def test_multiple_dimensions(self):
        text = "dimension title value buy groceries dimension priority value high"
        preamble, dims = _extract_dimensions(text, "task")
        assert dims["title"] == "buy groceries"
        assert dims["priority"] == "high"

    def test_preamble_before_first_dimension(self):
        text = "fix the bug dimension priority value high"
        preamble, dims = _extract_dimensions(text, "task")
        assert preamble == "fix the bug"
        assert dims["priority"] == "high"

    def test_no_dimensions_returns_text_as_preamble(self):
        text = "some text without dimensions"
        preamble, dims = _extract_dimensions(text, "task")
        assert preamble == "some text without dimensions"
        assert dims == {}

    def test_invalid_dimension_key_ignored(self):
        text = "dimension mood value happy dimension priority value high"
        preamble, dims = _extract_dimensions(text, "task")
        assert "mood" not in dims
        assert dims["priority"] == "high"

    def test_filler_words_preserved_in_values(self):
        text = "dimension content value i just basically want to fix the bug"
        preamble, dims = _extract_dimensions(text, "task")
        assert "just" in dims["content"]
        assert "basically" in dims["content"]

    def test_filler_words_stripped_from_preamble(self):
        text = "uh fix the um bug dimension priority value high"
        preamble, dims = _extract_dimensions(text, "task")
        assert "uh" not in preamble
        assert "um" not in preamble
        assert preamble == "fix the bug"


class TestCreateTask:
    def test_task_with_all_dimensions(self):
        text = (
            "command create task "
            "dimension title value buy groceries "
            "dimension content value get milk eggs and bread "
            "dimension date value march 15 "
            "dimension priority value high "
            "dimension label value shopping"
        )
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert len(result.errors) == 0

        task = result.tasks[0]
        assert task["title"] == "buy groceries"
        assert task["description"] == "get milk eggs and bread"
        assert task["priority"] == "high"
        assert task["label"] == "shopping"
        assert task["due_date"] is not None
        assert isinstance(task["due_date"], date)

    def test_task_with_preamble_as_title(self):
        text = "command create task fix the login bug dimension priority value high"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        task = result.tasks[0]
        assert task["title"] == "fix the login bug"
        assert task["priority"] == "high"

    def test_task_explicit_title_overrides_preamble(self):
        text = "command create task some preamble dimension title value real title"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert result.tasks[0]["title"] == "real title"

    def test_task_no_title_uses_content(self):
        text = (
            "command create task "
            "dimension content value this is a fairly long description for a task that has no explicit title"
        )
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        task = result.tasks[0]
        assert len(task["title"]) <= 60
        assert task["title"].startswith("this is a fairly long description")

    def test_task_no_title_no_content_errors(self):
        text = "command create task dimension priority value high"
        result = parse_transcription(text)

        assert len(result.tasks) == 0
        assert len(result.errors) == 1

    def test_task_default_priority_medium(self):
        text = "command create task fix the bug"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert result.tasks[0]["priority"] == "medium"

    def test_task_with_only_title_dimension(self):
        text = "command create task dimension title value fix the bug"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        task = result.tasks[0]
        assert task["title"] == "fix the bug"
        assert task["description"] is None
        assert task["due_date"] is None
        assert task["priority"] == "medium"
        assert task["label"] is None

    def test_task_date_normalization_tomorrow(self):
        text = "command create task dimension title value test dimension date value tomorrow"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert result.tasks[0]["due_date"] is not None
        assert isinstance(result.tasks[0]["due_date"], date)

    def test_task_date_normalization_next_friday(self):
        text = "command create task dimension title value test dimension date value next friday"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert result.tasks[0]["due_date"] is not None

    def test_task_date_normalization_in_two_weeks(self):
        text = "command create task dimension title value test dimension date value in two weeks"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert result.tasks[0]["due_date"] is not None


class TestCreateNote:
    def test_note_with_all_dimensions(self):
        text = (
            "command create note "
            "dimension title value morning thoughts "
            "dimension content value feeling good about the project progress "
            "dimension tag value work "
            "dimension date value tomorrow"
        )
        result = parse_transcription(text)

        assert len(result.notes) == 1
        assert len(result.errors) == 0

        note = result.notes[0]
        assert note["title"] == "morning thoughts"
        assert note["content"] == "feeling good about the project progress"
        assert note["tag"] == "work"
        assert note["noted_at"] is not None

    def test_note_with_only_content(self):
        text = "command create note dimension content value had a quick thought"
        result = parse_transcription(text)

        assert len(result.notes) == 1
        note = result.notes[0]
        assert note["content"] == "had a quick thought"
        assert note["title"] is None
        assert note["tag"] is None
        assert note["noted_at"] is None

    def test_note_preamble_as_title(self):
        text = "command create note morning thoughts dimension content value feeling good about progress"
        result = parse_transcription(text)

        assert len(result.notes) == 1
        note = result.notes[0]
        assert note["title"] == "morning thoughts"
        assert note["content"] == "feeling good about progress"

    def test_note_no_content_errors(self):
        text = "command create note dimension tag value work"
        result = parse_transcription(text)

        assert len(result.notes) == 0
        assert len(result.errors) == 1

    def test_note_has_no_mood(self):
        # mood is not a valid dimension for notes in the new grammar
        text = "command create note dimension content value test entry"
        result = parse_transcription(text)

        assert len(result.notes) == 1
        assert "mood" not in result.notes[0]


class TestReadCommand:
    def test_read_task(self):
        text = "command read task 5"
        result = parse_transcription(text)

        assert len(result.commands) == 1
        cmd = result.commands[0]
        assert cmd["operation"] == "read"
        assert cmd["table"] == "task"
        assert cmd["target_id"] == 5

    def test_read_note(self):
        text = "command read note 3"
        result = parse_transcription(text)

        assert len(result.commands) == 1
        cmd = result.commands[0]
        assert cmd["operation"] == "read"
        assert cmd["table"] == "note"
        assert cmd["target_id"] == 3


class TestUpdateCommand:
    def test_update_task_priority(self):
        text = "command update task 2 dimension priority value critical"
        result = parse_transcription(text)

        assert len(result.commands) == 1
        cmd = result.commands[0]
        assert cmd["operation"] == "update"
        assert cmd["table"] == "task"
        assert cmd["target_id"] == 2
        assert cmd["dimensions"]["priority"] == "critical"

    def test_update_task_status_closed(self):
        text = "command update task 5 dimension status value closed"
        result = parse_transcription(text)

        assert len(result.commands) == 1
        cmd = result.commands[0]
        assert cmd["operation"] == "update"
        assert cmd["target_id"] == 5
        assert cmd["dimensions"]["status"] == "closed"

    def test_update_note(self):
        text = "command update note 1 dimension content value updated content"
        result = parse_transcription(text)

        assert len(result.commands) == 1
        cmd = result.commands[0]
        assert cmd["operation"] == "update"
        assert cmd["table"] == "note"
        assert cmd["target_id"] == 1
        assert cmd["dimensions"]["content"] == "updated content"

    def test_update_multiple_dimensions(self):
        text = "command update task 3 dimension priority value high dimension label value urgent"
        result = parse_transcription(text)

        assert len(result.commands) == 1
        cmd = result.commands[0]
        assert cmd["dimensions"]["priority"] == "high"
        assert cmd["dimensions"]["label"] == "urgent"


class TestDeleteCommand:
    def test_delete_task(self):
        text = "command delete task 5"
        result = parse_transcription(text)

        assert len(result.commands) == 1
        cmd = result.commands[0]
        assert cmd["operation"] == "delete"
        assert cmd["table"] == "task"
        assert cmd["target_id"] == 5

    def test_delete_note(self):
        text = "command delete note 3"
        result = parse_transcription(text)

        assert len(result.commands) == 1
        cmd = result.commands[0]
        assert cmd["operation"] == "delete"
        assert cmd["table"] == "note"
        assert cmd["target_id"] == 3


class TestInvalidInputs:
    def test_empty_text(self):
        result = parse_transcription("")
        assert len(result.tasks) == 0
        assert len(result.commands) == 0
        assert len(result.errors) == 0

    def test_no_command_keyword(self):
        result = parse_transcription("Hello this is a regular conversation")
        assert len(result.tasks) == 0
        assert len(result.commands) == 0
        assert len(result.errors) == 0

    def test_unknown_operation(self):
        text = "command merge task 5"
        result = parse_transcription(text)
        # "merge" is not a CRUD operation, so the split regex won't match
        assert len(result.tasks) == 0
        assert len(result.commands) == 0

    def test_unknown_table(self):
        text = "command create project fix bug"
        result = parse_transcription(text)
        assert len(result.tasks) == 0
        assert len(result.errors) >= 1


class TestMultipleCommands:
    def test_two_tasks(self):
        text = (
            "command create task dimension title value first task "
            "command create task dimension title value second task dimension priority value low"
        )
        result = parse_transcription(text)

        assert len(result.tasks) == 2
        assert result.tasks[0]["title"] == "first task"
        assert result.tasks[1]["title"] == "second task"
        assert result.tasks[1]["priority"] == "low"

    def test_task_and_command(self):
        text = (
            "command create task fix the login bug dimension priority value high "
            "command delete task 1"
        )
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert len(result.commands) == 1
        assert result.tasks[0]["title"] == "fix the login bug"
        assert result.commands[0]["operation"] == "delete"

    def test_mixed_create_update_delete(self):
        text = (
            "command create task dimension title value new task "
            "command create note dimension content value diary entry "
            "command update task 1 dimension priority value critical "
            "command delete task 2"
        )
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert len(result.notes) == 1
        assert len(result.commands) == 2


class TestCaseInsensitive:
    def test_uppercase_commands(self):
        text = "COMMAND CREATE TASK DIMENSION TITLE VALUE Buy groceries"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert result.tasks[0]["title"] == "buy groceries"

    def test_mixed_case(self):
        text = "Command Create Task Dimension Title Value clean the house"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert result.tasks[0]["title"] == "clean the house"


class TestWhisperPunctuation:
    def test_punctuation_stripped(self):
        text = "Command create task, dimension title value, buy groceries."
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert result.tasks[0]["title"] == "buy groceries"

    def test_task_and_command_in_same_transcription(self):
        text = (
            "Command create task dimension title value fix the login bug "
            "dimension priority value high. "
            "Command delete task 1."
        )
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert len(result.commands) == 1
        assert result.tasks[0]["title"] == "fix the login bug"
        assert result.commands[0]["operation"] == "delete"


class TestFillerWordsInValues:
    def test_filler_words_preserved_in_dimension_values(self):
        text = (
            "command create task dimension title value fix bug "
            "dimension content value i just basically want to fix the login issue"
        )
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert "just" in result.tasks[0]["description"]
        assert "basically" in result.tasks[0]["description"]

    def test_filler_words_stripped_from_preamble(self):
        text = "command create task uh um fix the basically bug dimension priority value high"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert result.tasks[0]["title"] == "fix the bug"

    def test_filler_words_between_operation_and_table(self):
        text = "command create uh task dimension title value test"
        result = parse_transcription(text)

        assert len(result.tasks) == 1
        assert result.tasks[0]["title"] == "test"


class TestPriorityValidation:
    def test_valid_priorities(self):
        for p in ("low", "medium", "high", "critical"):
            assert _normalize_priority(p) == p

    def test_case_insensitive_priority(self):
        assert _normalize_priority("HIGH") == "high"
        assert _normalize_priority("Critical") == "critical"

    def test_invalid_priority_defaults_to_medium(self):
        assert _normalize_priority("urgent") == "medium"
        assert _normalize_priority("asdf") == "medium"
        assert _normalize_priority("") == "medium"

    def test_none_priority_defaults_to_medium(self):
        assert _normalize_priority(None) == "medium"
