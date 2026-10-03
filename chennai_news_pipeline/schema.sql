-- =====================================================================
-- Chennai News Dataset - MySQL tables (MySQL 5.7.8+ / 8.x, utf8mb4)
-- Create inside the dashboard database, e.g.:
--   mysql -u root -p district_collector_dashboard < schema.sql
-- run_pipeline.py also applies this file automatically when DB_* is set in .env.
-- Datetimes are stored as IST wall-clock time (Asia/Kolkata).
-- =====================================================================

CREATE TABLE IF NOT EXISTS news_raw (
  article_id      VARCHAR(40)   NOT NULL,
  fetched_at      DATETIME      NOT NULL,
  source_type     VARCHAR(20)   NOT NULL,
  source_name     VARCHAR(255)  NULL,
  source_feed     TEXT          NULL,
  query_used      VARCHAR(500)  NULL,
  edition         VARCHAR(10)   NULL,
  title           TEXT          NULL,
  link            TEXT          NULL,
  published       VARCHAR(100)  NULL,
  summary         MEDIUMTEXT    NULL,
  source_title    VARCHAR(255)  NULL,
  source_url      VARCHAR(1000) NULL,
  guid            TEXT          NULL,
  entry_json      LONGTEXT      NULL,
  PRIMARY KEY (article_id),
  INDEX idx_raw_fetched_at (fetched_at),
  INDEX idx_raw_source_type (source_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS news_processed (
  article_id             VARCHAR(40)   NOT NULL,
  fetched_at_ist         DATETIME      NOT NULL,
  published_at_ist       DATETIME      NULL,
  published_date         DATE          NULL,
  published_hour         TINYINT       NULL,
  day_of_week            VARCHAR(10)   NULL,
  age_hours              DECIMAL(10,2) NULL,
  title                  TEXT          NULL,
  title_clean            TEXT          NULL,
  title_normalized       TEXT          NULL,
  summary_clean          MEDIUMTEXT    NULL,
  body_clean             MEDIUMTEXT    NULL,
  full_text              MEDIUMTEXT    NULL,
  body_extracted         TINYINT(1)    NOT NULL DEFAULT 0,
  word_count             INT           NULL,
  char_count             INT           NULL,
  language               VARCHAR(10)   NULL,
  has_tamil_text         TINYINT(1)    NOT NULL DEFAULT 0,
  url                    TEXT          NULL,
  canonical_url          TEXT          NULL,
  source_name            VARCHAR(255)  NULL,
  source_domain          VARCHAR(255)  NULL,
  source_type            VARCHAR(20)   NULL,
  query_used             VARCHAR(500)  NULL,
  mentioned_taluks       JSON          NULL,
  mentioned_localities   JSON          NULL,
  first_mentioned_place  VARCHAR(100)  NULL,
  latitude               DECIMAL(9,6)  NULL,
  longitude              DECIMAL(9,6)  NULL,
  department             VARCHAR(100)  NULL,
  department_method      VARCHAR(20)   NULL,
  department_confidence  DECIMAL(5,3)  NULL,
  department_evidence    JSON          NULL,
  is_complaint           TINYINT(1)    NOT NULL DEFAULT 0,
  complaint_score        DECIMAL(6,2)  NULL,
  complaint_cues         JSON          NULL,
  complaint_method       VARCHAR(20)   NULL,
  PRIMARY KEY (article_id),
  INDEX idx_proc_published_at (published_at_ist),
  INDEX idx_proc_source_domain (source_domain),
  INDEX idx_proc_first_place (first_mentioned_place),
  INDEX idx_proc_fetched_at (fetched_at_ist),
  INDEX idx_proc_canonical_url (canonical_url(255)),
  INDEX idx_proc_department (department),
  INDEX idx_proc_is_complaint (is_complaint)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
