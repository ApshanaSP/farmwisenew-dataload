-- Police Department dataset for the Chennai District Collector Intelligence Dashboard.
-- Raw, observable facts only: no severity, priority, duplicate or cluster columns.
-- Safe to re-run: tables are created only if missing.

CREATE TABLE IF NOT EXISTS police_stations (
  station_code  VARCHAR(12)   NOT NULL,
  station_name  VARCHAR(80)   NOT NULL,
  -- References the taluk master (config/taluks.json today, Location Master table later).
  taluk_code    VARCHAR(10)   NOT NULL,
  latitude      DECIMAL(9,6)  NOT NULL,
  longitude     DECIMAL(9,6)  NOT NULL,
  PRIMARY KEY (station_code),
  KEY idx_ps_taluk (taluk_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS police_incident_reports (
  report_id              VARCHAR(24)   NOT NULL,             -- POL-YYYYMMDD-NNNN
  incident_datetime      DATETIME      NOT NULL,
  reported_datetime      DATETIME      NOT NULL,
  station_code           VARCHAR(12)   NOT NULL,
  -- References the taluk master (config/taluks.json today, Location Master table later).
  taluk_code             VARCHAR(10)   NOT NULL,
  locality               VARCHAR(80)   NOT NULL,
  latitude               DECIMAL(9,6)  NOT NULL,
  longitude              DECIMAL(9,6)  NOT NULL,
  category               ENUM('ROAD_ACCIDENT','THEFT_BURGLARY','CHAIN_SNATCHING','TRAFFIC_OBSTRUCTION',
                              'PUBLIC_NUISANCE','CYBER_FRAUD','ASSAULT','CRIMES_AGAINST_WOMEN',
                              'MISSING_PERSON','PROTEST_LAW_AND_ORDER','DRUGS_ILLICIT_LIQUOR',
                              'WEATHER_EMERGENCY','MURDER','OTHER') NOT NULL,
  title                  VARCHAR(120)  NOT NULL,
  description            VARCHAR(400)  NOT NULL,
  persons_affected       SMALLINT      NOT NULL DEFAULT 0,
  injured_count          SMALLINT      NOT NULL DEFAULT 0,
  fatalities             SMALLINT      NOT NULL DEFAULT 0,
  vulnerable_victim      BOOLEAN       NOT NULL DEFAULT FALSE,  -- child, elderly person or woman directly harmed
  weapon_involved        BOOLEAN       NOT NULL DEFAULT FALSE,
  road_blocked           BOOLEAN       NOT NULL DEFAULT FALSE,
  blockage_minutes       SMALLINT      NULL,                    -- only when road_blocked
  crowd_estimate         SMALLINT      NULL,                    -- only for protests and crowd events
  is_weather_related     BOOLEAN       NOT NULL DEFAULT FALSE,
  source                 ENUM('FIR','CONTROL_ROOM_112','PATROL','CITIZEN_GRIEVANCE','NEWS_REPORT') NOT NULL,
  linked_grievance_code  VARCHAR(20)   NULL,                    -- only when source = CITIZEN_GRIEVANCE
  status                 ENUM('REPORTED','UNDER_INVESTIGATION','ACTION_TAKEN','CLOSED') NOT NULL,
  response_minutes       SMALLINT      NULL,                    -- NULL for cyber and missing-person cases
  closed_datetime        DATETIME      NULL,                    -- only when CLOSED
  PRIMARY KEY (report_id),
  KEY idx_pir_taluk_time (taluk_code, incident_datetime),
  KEY idx_pir_category_time (category, incident_datetime),
  KEY idx_pir_status (status),
  KEY idx_pir_source (source),
  CONSTRAINT fk_pir_station FOREIGN KEY (station_code) REFERENCES police_stations (station_code),
  CONSTRAINT chk_pir_reported_after_incident CHECK (reported_datetime >= incident_datetime),
  CONSTRAINT chk_pir_closed CHECK (
    (status = 'CLOSED' AND closed_datetime IS NOT NULL AND closed_datetime > reported_datetime)
    OR (status <> 'CLOSED' AND closed_datetime IS NULL)
  ),
  CONSTRAINT chk_pir_blockage CHECK (road_blocked = TRUE OR blockage_minutes IS NULL),
  CONSTRAINT chk_pir_grievance CHECK ((source = 'CITIZEN_GRIEVANCE') = (linked_grievance_code IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
