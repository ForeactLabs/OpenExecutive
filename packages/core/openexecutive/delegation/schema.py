"""Tables for Act as me, in the episodic DB (per company: they swap with a
client slot — see ``clients.slots._wipe_per_client_tables``).

Kept free of imports so ``memory.episodic.initialize_db`` can create them
without an import cycle.
"""
from __future__ import annotations

import json
import sqlite3

SETTINGS_TABLE = "delegation_settings"
VOICE_TABLE = "delegation_voice"
VOICE_HISTORY_TABLE = "delegation_voice_history"
DRAFTS_TABLE = "delegation_drafts"
INBOX_WATCH_TABLE = "delegation_inbox_watch"
INBOX_MESSAGES_TABLE = "delegation_inbox_messages"
TEAM_TABLE = "delegation_team"
HANDLE_IT_TABLE = "delegation_handle_it"
HANDLED_TABLE = "delegation_handled"
REMINDERS_TABLE = "delegation_reminders"
MAIL_READ_TABLE = "delegation_mail_read"
TRAINING_TABLE = "delegation_training"
DISCLOSURE_TABLE = "delegation_disclosure"
CALENDAR_TABLE = "delegation_calendar"
SAVED_FILES_TABLE = "delegation_saved_files"
SAVED_FOLDER_TABLE = "delegation_saved_folder"
UPGRADES_TABLE = "delegation_upgrades"
CARD_FILES_TABLE = "delegation_card_files"
# The person's own texts, through Google Messages in their browser
# (``delegation.texts``).
TEXT_SETTINGS_TABLE = "delegation_text_settings"
TEXT_CONVERSATIONS_TABLE = "delegation_text_conversations"
TEXTS_TABLE = "delegation_texts"
TEXT_SENDS_TABLE = "delegation_text_sends"
# What came in for a person, kept a short while as their private context
# (``delegation.noticed``), and where the inbox read for it got to
# (``delegation.mail_context``).
NOTICED_TABLE = "delegation_noticed"
MAIL_CONTEXT_TABLE = "delegation_mail_context"

TABLES: tuple[str, ...] = (
    SETTINGS_TABLE,
    VOICE_TABLE,
    VOICE_HISTORY_TABLE,
    DRAFTS_TABLE,
    INBOX_WATCH_TABLE,
    INBOX_MESSAGES_TABLE,
    TEAM_TABLE,
    HANDLE_IT_TABLE,
    HANDLED_TABLE,
    REMINDERS_TABLE,
    MAIL_READ_TABLE,
    TRAINING_TABLE,
    DISCLOSURE_TABLE,
    CALENDAR_TABLE,
    SAVED_FILES_TABLE,
    SAVED_FOLDER_TABLE,
    UPGRADES_TABLE,
    CARD_FILES_TABLE,
    TEXT_SETTINGS_TABLE,
    TEXT_CONVERSATIONS_TABLE,
    TEXTS_TABLE,
    TEXT_SENDS_TABLE,
    NOTICED_TABLE,
    MAIL_CONTEXT_TABLE,
)

_DDL: tuple[str, ...] = (
    # One row per thing that came in for a person (an email, a phone
    # alert), theirs alone, deleted after ``noticed.KEEP``. ``item_key`` is
    # the source's own id; ``match_key`` the same item seen another way.
    f"CREATE TABLE IF NOT EXISTS {NOTICED_TABLE} ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  person_id INTEGER NOT NULL,"
    "  source TEXT NOT NULL,"
    "  item_key TEXT NOT NULL,"
    "  match_key TEXT NOT NULL DEFAULT '',"
    "  sender_name TEXT NOT NULL DEFAULT '',"
    "  sender TEXT NOT NULL DEFAULT '',"
    "  subject TEXT NOT NULL DEFAULT '',"
    "  gist TEXT NOT NULL DEFAULT '',"
    "  tie TEXT NOT NULL DEFAULT '',"
    "  thread_id TEXT NOT NULL DEFAULT '',"
    "  received_at TEXT NOT NULL,"
    "  noted_at TEXT NOT NULL,"
    "  acted_at TEXT,"
    # Times the source changed it in place (``noticed.note(refresh=True)``).
    "  updates INTEGER NOT NULL DEFAULT 0,"
    # Something should be done because of it (``noticed.waiting_to_act``).
    "  wants_act INTEGER NOT NULL DEFAULT 0,"
    "  UNIQUE(person_id, item_key)"
    ")",
    f"CREATE INDEX IF NOT EXISTS idx_{NOTICED_TABLE}_person_received "
    f"ON {NOTICED_TABLE}(person_id, received_at)",
    f"CREATE TABLE IF NOT EXISTS {MAIL_CONTEXT_TABLE} ("
    "  person_id INTEGER PRIMARY KEY,"
    "  read_through TEXT,"
    "  last_poll_at TEXT,"
    "  backoff_until TEXT,"
    "  failures INTEGER NOT NULL DEFAULT 0"
    ")",
    # Texts: the person's switch, and what their browser last said about
    # Google Messages. One row per person; absent means off.
    f"CREATE TABLE IF NOT EXISTS {TEXT_SETTINGS_TABLE} ("
    "  person_id INTEGER PRIMARY KEY,"
    "  enabled INTEGER NOT NULL DEFAULT 0,"
    "  paired INTEGER NOT NULL DEFAULT 0,"
    "  seen_at TEXT,"
    "  updated_at TEXT"
    ")",
    # One conversation in their Google Messages (a person or a group). ``id``
    # is ours and random; ``key`` is Google Messages' own.
    f"CREATE TABLE IF NOT EXISTS {TEXT_CONVERSATIONS_TABLE} ("
    "  id TEXT PRIMARY KEY,"
    "  person_id INTEGER NOT NULL,"
    "  key TEXT NOT NULL,"
    "  title TEXT NOT NULL DEFAULT '',"
    "  number TEXT NOT NULL DEFAULT '',"
    "  is_group INTEGER NOT NULL DEFAULT 0,"
    "  updated_at TEXT NOT NULL,"
    # The Executive's own line on what it last did about this conversation
    # (``texts_watch``), shown on the Texts page, and when.
    "  note TEXT NOT NULL DEFAULT '',"
    "  note_at TEXT,"
    "  UNIQUE (person_id, key)"
    ")",
    f"CREATE TABLE IF NOT EXISTS {TEXTS_TABLE} ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  person_id INTEGER NOT NULL,"
    "  conversation_id TEXT NOT NULL,"
    "  ref TEXT NOT NULL,"
    "  sender TEXT NOT NULL DEFAULT '',"
    "  text TEXT NOT NULL DEFAULT '',"
    "  outgoing INTEGER NOT NULL DEFAULT 0,"
    "  at TEXT NOT NULL,"
    "  handled INTEGER NOT NULL DEFAULT 0,"
    "  UNIQUE (person_id, ref)"
    ")",
    f"CREATE INDEX IF NOT EXISTS idx_{TEXTS_TABLE}_conv ON {TEXTS_TABLE} (conversation_id, at)",
    # What the Executive asked their browser to send, until it says how it
    # went: in a conversation, or to a number (a new conversation).
    f"CREATE TABLE IF NOT EXISTS {TEXT_SENDS_TABLE} ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  person_id INTEGER NOT NULL,"
    "  conversation_id TEXT,"
    "  to_number TEXT NOT NULL DEFAULT '',"
    "  text TEXT NOT NULL,"
    "  status TEXT NOT NULL DEFAULT 'pending',"
    "  error TEXT NOT NULL DEFAULT '',"
    "  created_at TEXT NOT NULL,"
    # When the browser took it, then when it said how it went.
    "  done_at TEXT,"
    # The chat that was told "on its way": how it went is said there.
    "  tell_session TEXT,"
    # Its words came back once in the list (``texts._echo_of``): the same
    # words again are someone's reply.
    "  echoed INTEGER NOT NULL DEFAULT 0,"
    # The Take the lead log row that says it is on its way, told how it
    # went (``take_the_lead.device_settled``).
    "  log_id INTEGER,"
    # The chat whose person asked for it (an errand), however fast it went;
    # NULL for a reply a texts turn or an approved card sent. Its first answer
    # is kept as a heard note (``memory.history_heard``).
    "  asked_in TEXT"
    ")",
    # The owner's "Let team members use Act as me": one row, absent means off.
    f"CREATE TABLE IF NOT EXISTS {TEAM_TABLE} ("
    "  id INTEGER PRIMARY KEY CHECK (id = 1),"
    "  enabled INTEGER NOT NULL DEFAULT 0,"
    "  updated_at TEXT,"
    "  updated_by TEXT"
    ")",
    # One row per person who has ever set it. Absent means off.
    f"CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} ("
    "  person_id INTEGER PRIMARY KEY,"
    "  enabled INTEGER NOT NULL DEFAULT 0,"
    "  updated_at TEXT,"
    "  updated_by TEXT"
    ")",
    # "How I write": one profile per person (JSON), lockable.
    f"CREATE TABLE IF NOT EXISTS {VOICE_TABLE} ("
    "  person_id INTEGER PRIMARY KEY,"
    "  profile TEXT NOT NULL DEFAULT '{}',"
    "  locked INTEGER NOT NULL DEFAULT 0,"
    "  learned_at TEXT,"
    "  sample_count INTEGER NOT NULL DEFAULT 0,"
    "  updated_at TEXT,"
    "  updated_by TEXT"
    ")",
    # Every change to a profile, for review.
    f"CREATE TABLE IF NOT EXISTS {VOICE_HISTORY_TABLE} ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  person_id INTEGER NOT NULL,"
    "  created_at TEXT NOT NULL,"
    "  profile TEXT NOT NULL,"
    "  locked INTEGER NOT NULL DEFAULT 0,"
    "  updated_by TEXT NOT NULL"
    ")",
    # Every draft saved in someone's Gmail (delegation.drafts): ids and
    # times only, never an address, a subject or text. gone_at: a chat draft
    # found missing from their Drafts (sent or deleted there). session_id:
    # the conversation a chat draft was written in (``delegation.redrafts``).
    # replaced_by: the row of the newer chat draft that retired this one.
    # from_executive: a chat draft switched on its card to go from the
    # Executive (``delegation.chat_draft_card.switch``), 1, else 0.
    f"CREATE TABLE IF NOT EXISTS {DRAFTS_TABLE} ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  person_id INTEGER NOT NULL,"
    "  source TEXT NOT NULL,"
    "  thread_id TEXT,"
    "  draft_id TEXT,"
    "  message_id TEXT,"
    "  sent_message_id TEXT,"
    "  created_at TEXT NOT NULL,"
    "  gone_at TEXT,"
    "  session_id TEXT,"
    "  replaced_by INTEGER,"
    "  from_executive INTEGER NOT NULL DEFAULT 0"
    ")",
    f"CREATE INDEX IF NOT EXISTS idx_{DRAFTS_TABLE}_person_created "
    f"ON {DRAFTS_TABLE}(person_id, created_at)",
    # The inbox watcher's switch and health, one row per person (absent: off).
    # watch_since resets on every off → on, so it never drafts for old mail.
    # mailbox_drafts: also save each reply in their mailbox's Drafts (off: the
    # reply lives on its card alone until they send it).
    f"CREATE TABLE IF NOT EXISTS {INBOX_WATCH_TABLE} ("
    "  person_id INTEGER PRIMARY KEY,"
    "  enabled INTEGER NOT NULL DEFAULT 0,"
    "  watch_since TEXT,"
    "  last_poll_at TEXT,"
    "  status TEXT NOT NULL DEFAULT 'off',"
    "  backoff_until TEXT,"
    "  failures INTEGER NOT NULL DEFAULT 0,"
    "  mailbox_drafts INTEGER NOT NULL DEFAULT 0,"
    "  updated_at TEXT,"
    "  updated_by TEXT"
    ")",
    # What the watcher did with each inbound message: its durable cursor.
    # Codes and ids only: sender_key is a hash of the address, never the
    # address, and no subject or text is kept here.
    f"CREATE TABLE IF NOT EXISTS {INBOX_MESSAGES_TABLE} ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  person_id INTEGER NOT NULL,"
    "  message_id TEXT NOT NULL,"
    "  thread_id TEXT,"
    "  received_at TEXT,"
    "  sender_key TEXT,"
    "  relation TEXT,"
    "  outcome TEXT NOT NULL,"
    "  reason TEXT,"
    "  classified INTEGER NOT NULL DEFAULT 0,"
    "  attempts INTEGER NOT NULL DEFAULT 0,"
    "  draft_id TEXT,"
    "  decision_id INTEGER,"
    "  flags TEXT NOT NULL DEFAULT '[]',"
    "  created_at TEXT NOT NULL,"
    "  updated_at TEXT NOT NULL,"
    "  UNIQUE(person_id, message_id)"
    ")",
    f"CREATE INDEX IF NOT EXISTS idx_{INBOX_MESSAGES_TABLE}_thread "
    f"ON {INBOX_MESSAGES_TABLE}(person_id, thread_id)",
    f"CREATE INDEX IF NOT EXISTS idx_{INBOX_MESSAGES_TABLE}_created "
    f"ON {INBOX_MESSAGES_TABLE}(person_id, created_at)",
    # Handle it for me, one row per person (absent: off). mode and levels are
    # the dial it had before Approvals decided, no longer read.
    f"CREATE TABLE IF NOT EXISTS {HANDLE_IT_TABLE} ("
    "  person_id INTEGER PRIMARY KEY,"
    "  enabled INTEGER NOT NULL DEFAULT 0,"
    "  levels TEXT NOT NULL DEFAULT '{}',"
    "  mode TEXT NOT NULL DEFAULT 'balanced',"
    "  updated_at TEXT,"
    "  updated_by TEXT"
    ")",
    # Every reply the inbox watcher sent on its own: ids and times only.
    f"CREATE TABLE IF NOT EXISTS {HANDLED_TABLE} ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  person_id INTEGER NOT NULL,"
    "  thread_id TEXT NOT NULL,"
    "  decision_id INTEGER,"
    "  sent_message_id TEXT,"
    "  sent_at TEXT NOT NULL"
    ")",
    f"CREATE INDEX IF NOT EXISTS idx_{HANDLED_TABLE}_person_sent "
    f"ON {HANDLED_TABLE}(person_id, sent_at)",
    # remind_me: plain text sent to the person who asked, when it is due
    # (delegation.reminders). Never a scheduled_actions row: that list is
    # everyone's, and its rows run an Executive turn when they fire.
    f"CREATE TABLE IF NOT EXISTS {REMINDERS_TABLE} ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  person_id INTEGER NOT NULL,"
    "  text TEXT NOT NULL,"
    "  due_at TEXT NOT NULL,"
    "  created_at TEXT NOT NULL,"
    "  claimed_at TEXT,"
    "  sent_at TEXT,"
    "  cancelled_at TEXT"
    ")",
    f"CREATE INDEX IF NOT EXISTS idx_{REMINDERS_TABLE}_due "
    f"ON {REMINDERS_TABLE}(claimed_at, cancelled_at, due_at)",
    # The threads of someone's own mailbox read_my_email opened for them
    # (delegation.mail_reads): where, never what. One row per thread, the
    # subject and sender as one line each and never a message's text, so a
    # later conversation can open it again; mailbox is the address it was
    # read from, so a list never points into another mailbox.
    f"CREATE TABLE IF NOT EXISTS {MAIL_READ_TABLE} ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  person_id INTEGER NOT NULL,"
    "  mailbox TEXT NOT NULL,"
    "  thread_id TEXT NOT NULL,"
    "  subject TEXT NOT NULL DEFAULT '',"
    "  sender TEXT NOT NULL DEFAULT '',"
    "  read_at TEXT NOT NULL,"
    "  UNIQUE(person_id, mailbox, thread_id)"
    ")",
    f"CREATE INDEX IF NOT EXISTS idx_{MAIL_READ_TABLE}_person_read "
    f"ON {MAIL_READ_TABLE}(person_id, read_at)",
    # Act as me in training, per setting (delegation.training.SETTINGS):
    # one row per person and setting, absent means not in training.
    f"CREATE TABLE IF NOT EXISTS {TRAINING_TABLE} ("
    "  person_id INTEGER NOT NULL,"
    "  setting TEXT NOT NULL,"
    "  enabled INTEGER NOT NULL DEFAULT 0,"
    "  updated_at TEXT,"
    "  updated_by TEXT,"
    "  PRIMARY KEY (person_id, setting)"
    ")",
    # The AI disclosure line on what is sent as a person (delegation.disclosure):
    # one row per person who changed theirs. NULL follows the install's
    # AI_DISCLOSURE_HANDLE_IT / AI_DISCLOSURE_APPROVED_SENDS.
    f"CREATE TABLE IF NOT EXISTS {DISCLOSURE_TABLE} ("
    "  person_id INTEGER PRIMARY KEY,"
    "  handle_it INTEGER,"
    "  approved_sends INTEGER,"
    "  updated_at TEXT,"
    "  updated_by TEXT"
    ")",
    # Read my calendar (delegation.calendar): one row per person who set it.
    # Absent means off.
    f"CREATE TABLE IF NOT EXISTS {CALENDAR_TABLE} ("
    "  person_id INTEGER PRIMARY KEY,"
    "  enabled INTEGER NOT NULL DEFAULT 0,"
    "  updated_at TEXT,"
    "  updated_by TEXT"
    ")",
    # The files save_my_email_attachment copied from someone's own mail into
    # the Executive's Drive or OneDrive (delegation.saved_files): whose each
    # one is, so the gateway hides it from everyone else's turns. The file's
    # id and name, never its content or the mail it came from.
    f"CREATE TABLE IF NOT EXISTS {SAVED_FILES_TABLE} ("
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "  person_id INTEGER NOT NULL,"
    "  provider TEXT NOT NULL,"
    "  file_id TEXT NOT NULL,"
    "  name TEXT NOT NULL DEFAULT '',"
    "  saved_at TEXT NOT NULL,"
    "  UNIQUE(provider, file_id)"
    ")",
    # Every "From your email" folder those files went in, made by the save
    # itself (never one found by name): current marks the one in use per
    # storage and Executive account; a retired one stays listed, since the
    # gateway keeps every one of them out of reach of the model.
    f"CREATE TABLE IF NOT EXISTS {SAVED_FOLDER_TABLE} ("
    "  provider TEXT NOT NULL,"
    "  account TEXT NOT NULL,"
    "  folder_id TEXT NOT NULL,"
    "  drive_id TEXT NOT NULL DEFAULT '',"
    "  created_at TEXT NOT NULL,"
    "  current INTEGER NOT NULL DEFAULT 1,"
    "  PRIMARY KEY (provider, folder_id)"
    ")",
    # One-time upgrades of what is stored, by name.
    f"CREATE TABLE IF NOT EXISTS {UPGRADES_TABLE} ("
    "  name TEXT PRIMARY KEY,"
    "  done_at TEXT NOT NULL"
    ")",
    # The files a reply card's reply carries (``delegation.card_work``): what
    # the Executive made or picked while doing what the email asked, attached
    # when the reply is sent. One row per file, in order.
    f"CREATE TABLE IF NOT EXISTS {CARD_FILES_TABLE} ("
    "  decision_id INTEGER NOT NULL,"
    "  position INTEGER NOT NULL,"
    "  name TEXT NOT NULL,"
    "  mime TEXT NOT NULL,"
    "  data BLOB NOT NULL,"
    "  created_at TEXT NOT NULL,"
    "  PRIMARY KEY (decision_id, position)"
    ")",
)


def _add_handle_it_mode(conn: sqlite3.Connection) -> None:
    """Give a table made before ``mode`` existed the column, never letting
    more go than before. Replies to people they know on their own (the old
    default) stay ``balanced``, or ``bold`` if strangers were handled too.
    Anyone who had known people on ask or off sent nothing on its own, and
    every mode does, so it is turned off for them to turn back on."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({HANDLE_IT_TABLE})")}
    if "mode" in columns:
        return
    conn.execute(f"ALTER TABLE {HANDLE_IT_TABLE} ADD COLUMN mode TEXT NOT NULL DEFAULT 'balanced'")
    for person_id, levels in conn.execute(f"SELECT person_id, levels FROM {HANDLE_IT_TABLE}").fetchall():  # noqa: S608
        try:
            chosen = json.loads(levels or "{}")
            known, stranger = chosen.get("reply_known", "handle"), chosen.get("reply_stranger")
        except (ValueError, AttributeError):
            continue
        if known != "handle":
            conn.execute(f"UPDATE {HANDLE_IT_TABLE} SET enabled = 0 WHERE person_id = ?", (person_id,))  # noqa: S608
        elif stranger == "handle":
            conn.execute(f"UPDATE {HANDLE_IT_TABLE} SET mode = 'bold' WHERE person_id = ?", (person_id,))  # noqa: S608
    conn.commit()


def _add_mailbox_drafts(conn: sqlite3.Connection) -> None:
    """Give a watch table made before ``mailbox_drafts`` existed the column,
    off: replies wait on their cards and no longer fill the person's Drafts."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({INBOX_WATCH_TABLE})")}
    if "mailbox_drafts" not in columns:
        conn.execute(f"ALTER TABLE {INBOX_WATCH_TABLE} ADD COLUMN mailbox_drafts INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def _add_draft_gone_at(conn: sqlite3.Connection) -> None:
    """Give a drafts table made before ``gone_at`` existed the column."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({DRAFTS_TABLE})")}
    if "gone_at" not in columns:
        conn.execute(f"ALTER TABLE {DRAFTS_TABLE} ADD COLUMN gone_at TEXT")
        conn.commit()


def _add_draft_session_id(conn: sqlite3.Connection) -> None:
    """Give a drafts table made before ``session_id`` existed the column."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({DRAFTS_TABLE})")}
    if "session_id" not in columns:
        conn.execute(f"ALTER TABLE {DRAFTS_TABLE} ADD COLUMN session_id TEXT")
        conn.commit()


def _add_draft_replaced_by(conn: sqlite3.Connection) -> None:
    """Give a drafts table made before ``replaced_by`` existed the column."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({DRAFTS_TABLE})")}
    if "replaced_by" not in columns:
        conn.execute(f"ALTER TABLE {DRAFTS_TABLE} ADD COLUMN replaced_by INTEGER")
        conn.commit()


def _add_draft_from_executive(conn: sqlite3.Connection) -> None:
    """Give a drafts table made before ``from_executive`` existed the column."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({DRAFTS_TABLE})")}
    if "from_executive" not in columns:
        conn.execute(f"ALTER TABLE {DRAFTS_TABLE} ADD COLUMN from_executive INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def _add_text_send_log_id(conn: sqlite3.Connection) -> None:
    """Give a text sends table made before ``log_id`` existed the column."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({TEXT_SENDS_TABLE})")}
    if "log_id" not in columns:
        conn.execute(f"ALTER TABLE {TEXT_SENDS_TABLE} ADD COLUMN log_id INTEGER")
        conn.commit()


def _add_text_send_asked_in(conn: sqlite3.Connection) -> None:
    """Give a text sends table made before ``asked_in`` existed the column."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({TEXT_SENDS_TABLE})")}
    if "asked_in" not in columns:
        conn.execute(f"ALTER TABLE {TEXT_SENDS_TABLE} ADD COLUMN asked_in TEXT")
        conn.commit()


def _add_text_conversation_note(conn: sqlite3.Connection) -> None:
    """Give a text conversations table made before ``note`` existed the columns."""
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({TEXT_CONVERSATIONS_TABLE})")}
    # Each column on its own, so one added before a crash never stops the other.
    if "note" not in columns:
        conn.execute(f"ALTER TABLE {TEXT_CONVERSATIONS_TABLE} ADD COLUMN note TEXT NOT NULL DEFAULT ''")
        conn.commit()
    if "note_at" not in columns:
        conn.execute(f"ALTER TABLE {TEXT_CONVERSATIONS_TABLE} ADD COLUMN note_at TEXT")
        conn.commit()


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the tables if missing. Idempotent."""
    for statement in _DDL:
        conn.execute(statement)
    _add_handle_it_mode(conn)
    _add_mailbox_drafts(conn)
    _add_draft_gone_at(conn)
    _add_draft_session_id(conn)
    _add_draft_replaced_by(conn)
    _add_draft_from_executive(conn)
    _add_text_send_log_id(conn)
    _add_text_send_asked_in(conn)
    _add_text_conversation_note(conn)
