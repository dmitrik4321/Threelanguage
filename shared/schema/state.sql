-- Threelanguage: клиентская база (общая DDL для Android Room и Desktop sqlite3)
-- Кодировка UTF-8; временные метки — unix ms (INTEGER).
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,            -- per_language_minutes, desired_retention, day_start_hour...
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS languages (
  id TEXT PRIMARY KEY,             -- 'en','es','zh'... (BCP-47 short)
  name_native TEXT NOT NULL,
  difficulty_bias REAL DEFAULT 0,  -- +1 для CJK в SRS
  budget_min INTEGER NOT NULL DEFAULT 15
);

CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY,
  lang_id TEXT NOT NULL REFERENCES languages(id),
  title TEXT NOT NULL,
  source TEXT NOT NULL,            -- builtin|ugc|apkg_import
  version INTEGER NOT NULL,
  level_scheme TEXT NOT NULL       -- CEFR|HSK|JLPT|TOPIK|NONE
);

CREATE TABLE IF NOT EXISTS cards (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES courses(id),
  text TEXT NOT NULL,              -- карточка на выучиваемом языке (иероглифы ок)
  reading TEXT,                    -- пиньинь/кана/ромадзи/IPA
  senses_json TEXT NOT NULL,       -- [{lang,text,tags}] переводы/значения
  examples_json TEXT,              -- [{target,gloss,audio_ref}]
  audio_refs TEXT,                 -- json list локальных кэшей mp3
  script_tag TEXT,                 -- lat|cyrl|hani|hira|kana|hangul
  level INTEGER,                   -- 1..6 схемы курса
  diag_score REAL                  -- сложность для placement
);

CREATE TABLE IF NOT EXISTS card_states (
  card_id INTEGER PRIMARY KEY REFERENCES cards(id),
  stability REAL NOT NULL DEFAULT 0,
  difficulty REAL NOT NULL DEFAULT 5,
  due_ms INTEGER NOT NULL DEFAULT 0,
  reps INTEGER NOT NULL DEFAULT 0,
  lapses INTEGER NOT NULL DEFAULT 0,
  state TEXT NOT NULL DEFAULT 'new',   -- new|learning|review|relearn
  afk_excluded_last INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY,
  card_id INTEGER NOT NULL REFERENCES cards(id),
  session_id INTEGER NOT NULL REFERENCES sessions(id),
  kind TEXT NOT NULL,               -- CHOICE_R2T, LISTEN_TYPE, SPEAK_REPEAT...
  grade INTEGER,                    -- 1..4; NULL если afk_excluded
  correct REAL,                     -- 0..1 частичный кредит
  active_ms INTEGER NOT NULL,
  weight_ms INTEGER NOT NULL,       -- после АФК-коррекции
  afk_suspect INTEGER NOT NULL DEFAULT 0,
  hint_used INTEGER DEFAULT 0,
  replays INTEGER DEFAULT 0,
  self_corrected INTEGER DEFAULT 0,
  answered_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS afk_events (
  attempt_tmp_id INTEGER,           -- связь после записи attempts (session-local uid)
  t_rel_ms INTEGER NOT NULL,
  event TEXT NOT NULL               -- input|focus_out|focus_in|mic_on|mic_off
);

CREATE TABLE IF NOT EXISTS pace_stats (
  kind TEXT NOT NULL,
  samples_json TEXT NOT NULL,       -- [active_ms,...] окно 30 «чистых»
  PRIMARY KEY (kind)
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY,
  lang_id TEXT NOT NULL REFERENCES languages(id),
  mode TEXT NOT NULL,               -- full|micro|placement
  started_at_ms INTEGER NOT NULL,
  ended_at_ms INTEGER,
  wall_ms INTEGER,
  effective_ms INTEGER,             -- Σ weight_ms (АФК-weight!)
  lesson_score INTEGER,             -- 0..100 (NULL у placement)
  placement_theta REAL,
  placement_level INTEGER,
  xp_awarded INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS day_progress (                  -- прогресс-бар дня
  day_key TEXT NOT NULL,            -- YYYY-MM-DD в таймзоне пользователя (день с day_start_hour)
  lang_id TEXT NOT NULL REFERENCES languages(id),
  effective_ms INTEGER NOT NULL DEFAULT 0,
  streak_days INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day_key, lang_id)
);

CREATE TABLE IF NOT EXISTS mascot_state (
  lang_id TEXT PRIMARY KEY REFERENCES languages(id),
  mood TEXT NOT NULL DEFAULT 'idle',      -- idle|cheer|angry|sad|sleep
  updated_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_events (                  -- append-only журнал для .threesave/API
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  device_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  pushed INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_card_states_due ON card_states(due_ms);
CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts(session_id);
CREATE INDEX IF NOT EXISTS idx_cards_course_level ON cards(course_id, level);
