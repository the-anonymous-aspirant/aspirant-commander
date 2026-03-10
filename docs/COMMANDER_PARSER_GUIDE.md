# Commander Parser Guide

How voice transcriptions become structured tasks. This document explains every step the parser takes, which keywords it detects, and what happens at each stage.

---

## Pipeline Overview

```
 Raw transcription from Whisper
              |
              v
 +----------------------------+
 |   1. PREPROCESS             |
 |   lowercase, strip          |
 |   punctuation, strip        |
 |   filler words              |
 +-------------+--------------+
               |
               v
 +----------------------------+
 |   2. DETECT BLOCKS          |
 |   scan for command         |
 |   activate / deactivate     |
 +-------------+--------------+
               |
               v
 +----------------------------+
 |   3. CLASSIFY BLOCK         |
 |   CONTENT                   |
 +------+-------------+-------+
        |             |
        v             v
 +------------+ +------------+
 | create     | | Command    |
 | task       | | close,     |
 |            | | reopen,    |
 |            | | update,    |
 |            | | list tasks |
 +------+-----+ +------+----+
        |              |
        v              v
 +----------------------------+
 |   4. EXTRACT METADATA       |
 |   metadata title ...        |
 |   metadata priority ...     |
 |   metadata date ...         |
 +-------------+--------------+
               |
               v
 +----------------------------+
 |   5. VALIDATE &             |
 |   NORMALIZE                 |
 |   dates -> ISO, priority    |
 |   -> known values           |
 +-------------+--------------+
               |
               v
          ParseResult
    +---------+---------+
    | tasks[] | cmds[]  | errors[]
    +---------+---------+
```

---

## Step 1: Preprocess

Before any keyword detection, the raw Whisper transcription is cleaned. Whisper may produce punctuation and capitalization that must be normalized before delimiter matching can work reliably.

### Operations (in order)

1. **Lowercase** the entire transcription.
2. **Strip punctuation** -- commas, periods, question marks, exclamation marks, colons, semicolons, and other non-alphanumeric characters are removed. This is necessary because Whisper may insert punctuation that would break keyword matching (e.g., "command activate," would not match "command activate").
3. **Strip filler words** -- common speech disfluencies are removed by word boundary.

```
"Uh, Command Activate, create task metadata title Buy Groceries
metadata priority high. Command Deactivate."
                                    |
                                    v
                          lowercase everything
                                    |
                                    v
"uh, command activate, create task metadata title buy groceries
metadata priority high. command deactivate."
                                    |
                                    v
                         strip punctuation
                                    |
                                    v
"uh command activate create task metadata title buy groceries
metadata priority high command deactivate"
                                    |
                                    v
                          strip filler words
                                    |
                                    v
"command activate create task metadata title buy groceries
metadata priority high command deactivate"
```

### Filler Words Removed

| Single word | Multi-word |
|-------------|------------|
| uh          | you know   |
| um          | i mean     |
| so          |            |
| like        |            |
| well        |            |
| right       |            |
| just        |            |
| basically   |            |
| actually    |            |

Multi-word fillers are stripped first (longest match) to avoid partial removal. All matching is by word boundaries, so "dislike" stays intact.

---

## Step 2: Detect Blocks

The parser scans the preprocessed text for `command activate` ... `command deactivate` delimiter pairs.

```
+-------------------------------------------------------------+
|                    Preprocessed text                          |
|                                                              |
|  ...text... +----------------------+ ...text...              |
|             | command activate    |                         |
|             |  ...                 |                         |
|             | command deactivate  |                         |
|             +----------+-----------+                         |
|                        |                                     |
|                  COMMAND BLOCK                                |
+------------------------+-------------------------------------+
```

| Delimiter pair | What it wraps |
|---------------|---------------|
| `command activate` ... `command deactivate` | One or more commands (task creation, close, reopen, update, list) |

- Multiple blocks can appear in a single transcription.
- Text outside any block is ignored.
- Blocks cannot nest (no `command activate` inside another `command activate`).

---

## Step 3: Classify Block Content

Inside each `command activate` ... `command deactivate` block, the parser determines what type of content is present. A single block may contain one or more commands.

```
command activate
  |
  +-- contains "create task"? -----------------> TASK CREATION
  +-- contains "close task {N}"? ---------------> CLOSE COMMAND
  +-- contains "reopen task {N}"? -------------> REOPEN COMMAND
  +-- contains "update task {N}"? -------------> UPDATE COMMAND
  +-- contains "list tasks filter by {S}"? ----> FILTERED LIST
  +-- contains "list tasks"? ------------------> LIST ALL
  +-- none matched? ---------------------------> ERROR (logged)
  |
command deactivate
```

### Command Reference

```
+------------------------------------------------------------------+
| CREATE TASK                                                       |
| -----------                                                       |
| Syntax:  command activate create task                            |
|            metadata title {text}                                  |
|            metadata priority {level}                              |
|            metadata date {text}                                   |
|            metadata label {text}                                  |
|            metadata content {text}                            |
|          command deactivate                                      |
| Effect:  Creates a new task with the specified fields             |
| Example: "command activate create task metadata title buy        |
|           groceries metadata priority high metadata date          |
|           tomorrow command deactivate"                           |
|                                                                   |
+-------------------------------------------------------------------+
| CLOSE                                                             |
| -----                                                             |
| Syntax:  command activate close task {number}                    |
|          command deactivate                                      |
| Effect:  Sets task status -> "closed", records closed_at          |
| Example: "command activate close task 3 command deactivate"    |
|                                                                   |
+-------------------------------------------------------------------+
| REOPEN                                                            |
| ------                                                            |
| Syntax:  command activate reopen task {number}                   |
|          command deactivate                                      |
| Effect:  Sets task status -> "open", clears closed_at             |
| Example: "command activate reopen task 3 command deactivate"   |
|                                                                   |
+-------------------------------------------------------------------+
| UPDATE                                                            |
| ------                                                            |
| Syntax:  command activate update task {number}                   |
|            metadata {field} {value}                               |
|          command deactivate                                      |
| Effect:  Updates specified fields on existing task                |
| Fields:  title, content, date, priority, label, status            |
| Example: "command activate update task 2 metadata priority       |
|           critical command deactivate"                           |
|                                                                   |
+-------------------------------------------------------------------+
| LIST (filtered)                                                   |
| ---------------                                                   |
| Syntax:  command activate list tasks filter by {status}          |
|          command deactivate                                      |
| Effect:  Logged only (results served via GET /tasks API)          |
| Example: "command activate list tasks filter by open             |
|           command deactivate"                                    |
|                                                                   |
+-------------------------------------------------------------------+
| LIST (all)                                                        |
| ----------                                                        |
| Syntax:  command activate list tasks command deactivate         |
| Effect:  Logged only (results served via GET /tasks API)          |
| Example: "command activate list tasks command deactivate"       |
+-------------------------------------------------------------------+
```

### How Target IDs Work

When you say "close task 3", the parser extracts the integer `3`. The poller then resolves this to a task by **creation order**: task 1 is the oldest, task 2 is the second oldest, etc. This is intentional -- UUIDs are not speakable, but "close task three" is natural.

```
commander_tasks (ordered by created_at ASC):
+----+-------------------+----------+
| #  | title             | UUID     |
+----+-------------------+----------+
| 1  | Buy groceries     | a1b2...  |  <- "task 1"
| 2  | Fix login bug     | c3d4...  |  <- "task 2"
| 3  | Write report      | e5f6...  |  <- "task 3"  <- "close task 3" targets this
+----+-------------------+----------+
```

---

## Step 4: Extract Metadata Fields

For both task creation and update commands, the parser extracts fields using `metadata {field_name}` markers.

### Metadata Field Pattern

```
metadata {field_name}  <-- marker identifying the field
  {value}              <-- everything after the field name until the next marker
```

The value of a metadata field extends from the field name to whichever of these comes first:
- The next `metadata` keyword
- The next `create task` keyword (for multiple tasks in one block)
- The `command deactivate` delimiter

### Recognized Fields

```
+--------------+----------+--------------------------------------+
| Field        | Required | Default / Fallback                   |
+--------------+----------+--------------------------------------+
| title        | No*      | Preamble text, or first 60 chars of  |
|              |          | content                              |
| content      | No       | NULL (stored as "description" in DB) |
| date         | No       | NULL (no due date)                   |
| priority     | No       | "medium"                             |
| label        | No       | NULL                                 |
+--------------+----------+--------------------------------------+

* If neither title, preamble, nor content is present, the task
  block is rejected and an error is logged.
```

### Field Order

Fields can appear in **any order**. These are equivalent:

```
command activate create task metadata title fix bug metadata priority high command deactivate
command activate create task metadata priority high metadata title fix bug command deactivate
```

### Worked Example: Task Extraction

```
Input (after preprocessing):
"command activate create task metadata title buy groceries metadata date
next friday metadata priority high metadata label shopping metadata
content get milk eggs and bread command deactivate"


Detected block content (between command activate / deactivate):
+-------------------------------------------------------------+
| create task                                                  |
|   metadata title -------- "buy groceries"                    |
|   metadata date ---------- "next friday"                     |
|   metadata priority ------ "high"                            |
|   metadata label --------- "shopping"                        |
|   metadata content --- "get milk eggs and bread"         |
+-------------------------------------------------------------+


Extracted task:
{
  "title":       "buy groceries",
  "description": "get milk eggs and bread",
  "due_date":    2026-03-14,          <- "next friday" normalized
  "priority":    "high",              <- validated against known values
  "label":       "shopping"
}
```

---

## Step 5: Validate & Normalize

### Date Normalization

Raw date text from `metadata date {value}` is passed through `dateparser`, which handles natural language:

```
+-----------------------+------------------+
| Spoken                | Resolved to      |
+-----------------------+------------------+
| "tomorrow"            | next calendar day|
| "next friday"         | upcoming Friday  |
| "march 15"            | 2026-03-15       |
| "in two weeks"        | +14 days         |
| "january 1 2027"      | 2027-01-01       |
| "gibberish"           | NULL (no error)  |
+-----------------------+------------------+
```

Invalid date text does not cause an error -- the date is simply set to NULL.

### Priority Normalization

```
+------------------+------------------+
| Spoken           | Stored as        |
+------------------+------------------+
| "high"           | "high"           |
| "HIGH"           | "high"           |
| "critical"       | "critical"       |
| "urgent"         | "medium" (default|
| (not provided)   | "medium" (default|
+------------------+------------------+

Valid values: low, medium, high, critical
Anything else -> defaults to "medium"
```

---

## Full Worked Example

### Input (raw Whisper output)

```
"Uh, Command Activate, create task metadata title Fix the login bug
metadata priority high metadata content Users are getting 500
errors on the login page. Command Deactivate. And um, Command
Activate close task 1 Command Deactivate."
```

### Step 1: Preprocess

```
-> lowercase:
"uh, command activate, create task metadata title fix the login bug
metadata priority high metadata content users are getting 500
errors on the login page. command deactivate. and um, command
activate close task 1 command deactivate."

-> strip punctuation:
"uh command activate create task metadata title fix the login bug
metadata priority high metadata content users are getting 500
errors on the login page command deactivate and um command
activate close task 1 command deactivate"

-> strip fillers (uh, um):
"command activate create task metadata title fix the login bug
metadata priority high metadata content users are getting 500
errors on the login page command deactivate and command activate
close task 1 command deactivate"
```

### Step 2: Detect Blocks

```
Block 1: "create task metadata title fix the login bug metadata
          priority high metadata content users are getting 500
          errors on the login page"

Block 2: "close task 1"
```

### Step 3: Classify

```
Block 1 -> contains "create task"   -> TASK CREATION
Block 2 -> contains "close task 1"  -> CLOSE COMMAND
```

### Step 4: Extract Metadata

```
Task block -> extract metadata fields:
  metadata title:       "fix the login bug"
  metadata priority:    "high"
  metadata content: "users are getting 500 errors on the login page"
  date:                 (not provided) -> NULL
  label:                (not provided) -> NULL

Command block -> pattern match:
  "close task 1" -> CLOSE command, target_id=1
```

### Step 5: Final Result

```python
ParseResult(
    tasks=[{
        "title": "fix the login bug",
        "description": "users are getting 500 errors on the login page",
        "due_date": None,
        "priority": "high",
        "label": None,
    }],
    commands=[{
        "type": "close",
        "target_id": 1,
        "params": {},
    }],
    errors=[],
)
```

---

## Multiple Tasks in One Block

A single `command activate` ... `command deactivate` block can contain multiple `create task` sequences:

```
"command activate create task metadata title buy groceries metadata
priority high create task metadata title call dentist metadata date
tomorrow command deactivate"

Result:
  Task 1: { title: "buy groceries", priority: "high" }
  Task 2: { title: "call dentist", due_date: <tomorrow> }
```

Each `create task` keyword starts a new task. Metadata fields after a `create task` belong to that task until the next `create task` or `command deactivate`.

---

## No Commands Found

If the transcription contains no `command activate` delimiter, the parser returns an empty result:

```
Input:  "hey just wanted to say the deployment went well today"

Result: ParseResult(tasks=[], commands=[], errors=[])

Stored in commander_processed as:
  parse_status = "no_commands"
  commands_found = 0
```

No action is taken. The message is simply marked as processed so it will not be picked up again.

---

## All Keywords (Quick Reference)

```
BLOCK DELIMITERS          METADATA MARKERS            COMMAND KEYWORDS
-------------------       --------------------        -------------------
command activate         metadata title              create task
command deactivate       metadata content            close task {N}
                          metadata date               reopen task {N}
                          metadata priority           update task {N} ...
                          metadata label              list tasks
                                                      list tasks filter by {S}
```
