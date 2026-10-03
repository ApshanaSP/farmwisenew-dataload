/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `area_wards` (
  `area_id` int NOT NULL,
  `ward_number` int NOT NULL,
  `is_primary` tinyint(1) NOT NULL DEFAULT '0' COMMENT 'The ward containing the geocoded centre point of the area',
  `confidence` enum('verified','derived') NOT NULL DEFAULT 'derived',
  `source` varchar(255) DEFAULT NULL,
  `source_version` varchar(64) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`area_id`,`ward_number`),
  KEY `idx_area_wards_ward` (`ward_number`),
  CONSTRAINT `fk_area_wards_area` FOREIGN KEY (`area_id`) REFERENCES `gcc_areas` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_area_wards_ward` FOREIGN KEY (`ward_number`) REFERENCES `zone_wards` (`ward_number`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `complaint_categories` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(150) NOT NULL,
  `sort_order` int NOT NULL DEFAULT '0',
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `source` varchar(255) DEFAULT NULL COMMENT 'URL the category was imported from',
  `source_fetched_at` datetime DEFAULT NULL COMMENT 'When the source was read',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_complaint_categories_name` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=19 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `complaint_status_history` (
  `id` int NOT NULL AUTO_INCREMENT,
  `complaint_id` int NOT NULL,
  `status` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `stage` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `remarks` varchar(1000) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `changed_by` int DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `fk_history_user` (`changed_by`),
  KEY `idx_history_complaint` (`complaint_id`),
  CONSTRAINT `fk_history_complaint` FOREIGN KEY (`complaint_id`) REFERENCES `complaints` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_history_user` FOREIGN KEY (`changed_by`) REFERENCES `users` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=89263 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `complaint_subtypes` (
  `id` int NOT NULL AUTO_INCREMENT,
  `category_id` int NOT NULL,
  `gcc_id` int NOT NULL COMMENT 'Stable option value from the GCC page',
  `label` varchar(255) NOT NULL,
  `department_id` int DEFAULT NULL COMMENT 'NULL until a department mapping is verified',
  `mapping_status` enum('mapped','assumed','unmapped') NOT NULL DEFAULT 'unmapped',
  `is_frequent` tinyint(1) NOT NULL DEFAULT '0',
  `frequent_order` int DEFAULT NULL,
  `sort_order` int NOT NULL DEFAULT '0',
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_complaint_subtypes_gcc_id` (`gcc_id`),
  KEY `idx_complaint_subtypes_category` (`category_id`),
  KEY `idx_complaint_subtypes_frequent` (`is_frequent`,`frequent_order`),
  KEY `fk_subtype_department` (`department_id`),
  CONSTRAINT `fk_subtype_category` FOREIGN KEY (`category_id`) REFERENCES `complaint_categories` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_subtype_department` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=329 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `complaint_types` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL,
  `department_id` int NOT NULL,
  `is_frequent` tinyint(1) NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`),
  KEY `idx_complaint_types_dept` (`department_id`),
  CONSTRAINT `fk_complaint_types_dept` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=23 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `complaints` (
  `id` int NOT NULL AUTO_INCREMENT,
  `complaint_code` varchar(20) COLLATE utf8mb4_unicode_ci NOT NULL,
  `user_id` int DEFAULT NULL COMMENT 'NULL for guest complaints',
  `initials` varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `first_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `last_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `gender` enum('Male','Female','Transgender') COLLATE utf8mb4_unicode_ci NOT NULL,
  `street_address` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `pincode` varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Complainant PIN code. Optional; the reported location has its own.',
  `mobile_number` varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `phone_number` varchar(15) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `zone_id` int NOT NULL,
  `ward_number` int NOT NULL,
  `locality_id` int DEFAULT NULL,
  `street_id` int DEFAULT NULL,
  `specific_location` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `latitude` decimal(10,7) DEFAULT NULL,
  `longitude` decimal(10,7) DEFAULT NULL,
  `department_id` int NOT NULL,
  `complaint_type_id` int DEFAULT NULL,
  `title` varchar(200) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` varchar(400) COLLATE utf8mb4_unicode_ci NOT NULL,
  `media_path` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `is_anonymous` tinyint(1) NOT NULL DEFAULT '0',
  `needs_manual_review` tinyint(1) NOT NULL DEFAULT '0',
  `status` enum('Complaint Filed','Pending Approval','Approved by Department Officer','In Progress','Completed - Pending Collector Verification','Verified by Collector','Rejected') COLLATE utf8mb4_unicode_ci NOT NULL DEFAULT 'Complaint Filed',
  `rejected_stage` enum('Department Officer','Collector') COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `remarks` varchar(1000) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `complaint_subtype_id` int DEFAULT NULL COMMENT 'GCC subcomplaint. New complaints use this; legacy rows use complaint_type_id.',
  `gcc_area_id` int DEFAULT NULL,
  `gcc_locality_id` int DEFAULT NULL,
  `gcc_street_id` int DEFAULT NULL COMMENT 'NULL when the citizen typed the street manually',
  `manual_street_name` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Street typed by the citizen when it is missing from the GCC list',
  `street_type` varchar(60) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'Street / Road / Avenue / ... kept separate from the street name',
  `ward_source` enum('map_boundary','user_selected','legacy') COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'How ward_number was determined for this complaint',
  `location_pincode` varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL COMMENT 'PIN code of the reported location, distinct from the complainant pincode',
  `is_synthetic` tinyint(1) NOT NULL DEFAULT '0' COMMENT '1 = generated by scripts/generate-synthetic-grievances.js; 0 = real',
  PRIMARY KEY (`id`),
  UNIQUE KEY `complaint_code` (`complaint_code`),
  KEY `fk_complaints_zone` (`zone_id`),
  KEY `fk_complaints_locality` (`locality_id`),
  KEY `fk_complaints_street` (`street_id`),
  KEY `fk_complaints_type` (`complaint_type_id`),
  KEY `idx_complaints_user` (`user_id`),
  KEY `idx_complaints_status` (`status`),
  KEY `idx_complaints_department` (`department_id`),
  KEY `idx_complaints_code` (`complaint_code`),
  KEY `idx_complaints_subtype` (`complaint_subtype_id`),
  KEY `fk_complaints_gcc_area` (`gcc_area_id`),
  KEY `fk_complaints_gcc_street` (`gcc_street_id`),
  KEY `idx_complaints_gcc_locality` (`gcc_locality_id`),
  KEY `idx_complaints_synthetic` (`is_synthetic`,`created_at`),
  CONSTRAINT `fk_complaints_department` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`),
  CONSTRAINT `fk_complaints_gcc_area` FOREIGN KEY (`gcc_area_id`) REFERENCES `gcc_areas` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_complaints_gcc_locality` FOREIGN KEY (`gcc_locality_id`) REFERENCES `gcc_localities` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_complaints_gcc_street` FOREIGN KEY (`gcc_street_id`) REFERENCES `gcc_streets` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_complaints_locality` FOREIGN KEY (`locality_id`) REFERENCES `localities` (`id`),
  CONSTRAINT `fk_complaints_street` FOREIGN KEY (`street_id`) REFERENCES `streets` (`id`),
  CONSTRAINT `fk_complaints_subtype` FOREIGN KEY (`complaint_subtype_id`) REFERENCES `complaint_subtypes` (`id`) ON DELETE RESTRICT,
  CONSTRAINT `fk_complaints_type` FOREIGN KEY (`complaint_type_id`) REFERENCES `complaint_types` (`id`),
  CONSTRAINT `fk_complaints_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE SET NULL,
  CONSTRAINT `fk_complaints_zone` FOREIGN KEY (`zone_id`) REFERENCES `zones` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=16166 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `departments` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` varchar(500) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `name` (`name`)
) ENGINE=InnoDB AUTO_INCREMENT=17 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `gcc_areas` (
  `id` int NOT NULL AUTO_INCREMENT,
  `gcc_id` int NOT NULL COMMENT 'AREAID on the GCC PGR site',
  `name` varchar(190) NOT NULL,
  `zone_id` int DEFAULT NULL COMMENT 'Derived from area_wards; groups streets by zone. Not authoritative.',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_gcc_areas_gcc_id` (`gcc_id`),
  KEY `idx_gcc_areas_name` (`name`),
  KEY `idx_gcc_areas_zone` (`zone_id`),
  CONSTRAINT `fk_gcc_areas_zone` FOREIGN KEY (`zone_id`) REFERENCES `zones` (`id`) ON DELETE SET NULL
) ENGINE=InnoDB AUTO_INCREMENT=251 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `gcc_localities` (
  `id` int NOT NULL AUTO_INCREMENT,
  `gcc_id` int NOT NULL COMMENT 'Locality ID on the GCC PGR site',
  `name` varchar(190) NOT NULL,
  `area_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_gcc_localities_gcc_id` (`gcc_id`),
  KEY `idx_gcc_localities_area` (`area_id`),
  CONSTRAINT `fk_gcc_locality_area` FOREIGN KEY (`area_id`) REFERENCES `gcc_areas` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=4884 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `gcc_streets` (
  `id` int NOT NULL AUTO_INCREMENT,
  `gcc_id` int NOT NULL COMMENT 'STREETID on the GCC PGR site',
  `name` varchar(255) NOT NULL,
  `locality_id` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_gcc_streets_gcc_id` (`gcc_id`),
  KEY `idx_gcc_streets_locality` (`locality_id`),
  CONSTRAINT `fk_gcc_street_locality` FOREIGN KEY (`locality_id`) REFERENCES `gcc_localities` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=53801 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `localities` (
  `id` int NOT NULL AUTO_INCREMENT,
  `zone_id` int NOT NULL,
  `name` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_localities_zone` (`zone_id`),
  CONSTRAINT `fk_localities_zone` FOREIGN KEY (`zone_id`) REFERENCES `zones` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=33 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `locality_wards` (
  `locality_id` int NOT NULL,
  `ward_number` int NOT NULL,
  `confidence` enum('verified','derived') NOT NULL DEFAULT 'derived' COMMENT 'verified = from an authoritative published mapping; derived = computed',
  `source` varchar(255) DEFAULT NULL,
  `source_version` varchar(64) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`locality_id`,`ward_number`),
  KEY `idx_locality_wards_ward` (`ward_number`),
  CONSTRAINT `fk_locality_wards_locality` FOREIGN KEY (`locality_id`) REFERENCES `gcc_localities` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_locality_wards_ward` FOREIGN KEY (`ward_number`) REFERENCES `zone_wards` (`ward_number`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `password_resets` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int DEFAULT NULL,
  `purpose` enum('password_reset','mobile_verify','complaint_mobile_verify','email_verify') COLLATE utf8mb4_unicode_ci NOT NULL,
  `identifier` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL COMMENT 'email or mobile number the OTP was sent to',
  `otp_hash` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `expires_at` datetime NOT NULL,
  `attempts` int NOT NULL DEFAULT '0',
  `used` tinyint(1) NOT NULL DEFAULT '0',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_password_resets_identifier` (`identifier`),
  KEY `idx_password_resets_user` (`user_id`),
  KEY `idx_password_resets_lookup` (`identifier`,`purpose`,`used`,`created_at`),
  CONSTRAINT `fk_password_resets_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `police_incident_reports` (
  `report_id` varchar(24) COLLATE utf8mb4_unicode_ci NOT NULL,
  `incident_datetime` datetime NOT NULL,
  `reported_datetime` datetime NOT NULL,
  `station_code` varchar(12) COLLATE utf8mb4_unicode_ci NOT NULL,
  `taluk_code` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `locality` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `latitude` decimal(9,6) NOT NULL,
  `longitude` decimal(9,6) NOT NULL,
  `category` enum('ROAD_ACCIDENT','THEFT_BURGLARY','CHAIN_SNATCHING','TRAFFIC_OBSTRUCTION','PUBLIC_NUISANCE','CYBER_FRAUD','ASSAULT','CRIMES_AGAINST_WOMEN','MISSING_PERSON','PROTEST_LAW_AND_ORDER','DRUGS_ILLICIT_LIQUOR','WEATHER_EMERGENCY','MURDER','OTHER') COLLATE utf8mb4_unicode_ci NOT NULL,
  `title` varchar(120) COLLATE utf8mb4_unicode_ci NOT NULL,
  `description` varchar(400) COLLATE utf8mb4_unicode_ci NOT NULL,
  `persons_affected` smallint NOT NULL DEFAULT '0',
  `injured_count` smallint NOT NULL DEFAULT '0',
  `fatalities` smallint NOT NULL DEFAULT '0',
  `vulnerable_victim` tinyint(1) NOT NULL DEFAULT '0',
  `weapon_involved` tinyint(1) NOT NULL DEFAULT '0',
  `road_blocked` tinyint(1) NOT NULL DEFAULT '0',
  `blockage_minutes` smallint DEFAULT NULL,
  `crowd_estimate` smallint DEFAULT NULL,
  `is_weather_related` tinyint(1) NOT NULL DEFAULT '0',
  `source` enum('FIR','CONTROL_ROOM_112','PATROL','CITIZEN_GRIEVANCE','NEWS_REPORT') COLLATE utf8mb4_unicode_ci NOT NULL,
  `linked_grievance_code` varchar(20) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `status` enum('REPORTED','UNDER_INVESTIGATION','ACTION_TAKEN','CLOSED') COLLATE utf8mb4_unicode_ci NOT NULL,
  `response_minutes` smallint DEFAULT NULL,
  `closed_datetime` datetime DEFAULT NULL,
  PRIMARY KEY (`report_id`),
  KEY `idx_pir_taluk_time` (`taluk_code`,`incident_datetime`),
  KEY `idx_pir_category_time` (`category`,`incident_datetime`),
  KEY `idx_pir_status` (`status`),
  KEY `idx_pir_source` (`source`),
  KEY `fk_pir_station` (`station_code`),
  CONSTRAINT `fk_pir_station` FOREIGN KEY (`station_code`) REFERENCES `police_stations` (`station_code`),
  CONSTRAINT `chk_pir_blockage` CHECK (((`road_blocked` = true) or (`blockage_minutes` is null))),
  CONSTRAINT `chk_pir_closed` CHECK ((((`status` = _utf8mb4'CLOSED') and (`closed_datetime` is not null) and (`closed_datetime` > `reported_datetime`)) or ((`status` <> _utf8mb4'CLOSED') and (`closed_datetime` is null)))),
  CONSTRAINT `chk_pir_grievance` CHECK (((`source` = _utf8mb4'CITIZEN_GRIEVANCE') = (`linked_grievance_code` is not null))),
  CONSTRAINT `chk_pir_reported_after_incident` CHECK ((`reported_datetime` >= `incident_datetime`))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `police_stations` (
  `station_code` varchar(12) COLLATE utf8mb4_unicode_ci NOT NULL,
  `station_name` varchar(80) COLLATE utf8mb4_unicode_ci NOT NULL,
  `taluk_code` varchar(10) COLLATE utf8mb4_unicode_ci NOT NULL,
  `latitude` decimal(9,6) NOT NULL,
  `longitude` decimal(9,6) NOT NULL,
  PRIMARY KEY (`station_code`),
  KEY `idx_ps_taluk` (`taluk_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `reference_data_sources` (
  `dataset` varchar(64) NOT NULL,
  `source_url` varchar(500) DEFAULT NULL,
  `source_label` varchar(255) DEFAULT NULL,
  `is_official` tinyint(1) NOT NULL DEFAULT '0',
  `fetched_at` datetime DEFAULT NULL,
  `row_count` int DEFAULT NULL,
  `notes` text,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`dataset`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `schema_migrations` (
  `filename` varchar(255) NOT NULL,
  `applied_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`filename`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `streets` (
  `id` int NOT NULL AUTO_INCREMENT,
  `locality_id` int NOT NULL,
  `name` varchar(150) COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_streets_locality` (`locality_id`),
  CONSTRAINT `fk_streets_locality` FOREIGN KEY (`locality_id`) REFERENCES `localities` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB AUTO_INCREMENT=256 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `user_profiles` (
  `id` int NOT NULL AUTO_INCREMENT,
  `user_id` int NOT NULL,
  `first_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `last_name` varchar(100) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `gender` enum('Male','Female','Transgender') COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `date_of_birth` date DEFAULT NULL,
  `mobile_number` varchar(10) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `mobile_verified` tinyint(1) NOT NULL DEFAULT '0',
  `alternate_email` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `door_no_and_street` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `area` varchar(150) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `locality` varchar(150) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `pincode` varchar(6) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `zone_id` int DEFAULT NULL,
  `ward_number` int DEFAULT NULL,
  `aadhaar_number_encrypted` varchar(255) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `aadhaar_last4` char(4) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `profile_photo` varchar(500) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `user_id` (`user_id`),
  UNIQUE KEY `mobile_number` (`mobile_number`),
  KEY `fk_profiles_zone` (`zone_id`),
  CONSTRAINT `fk_profiles_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_profiles_zone` FOREIGN KEY (`zone_id`) REFERENCES `zones` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=10 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `email` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `password_hash` varchar(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `role` enum('collector','department_officer','citizen') COLLATE utf8mb4_unicode_ci NOT NULL,
  `department_id` int DEFAULT NULL,
  `dept_code` varchar(16) COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `is_active` tinyint(1) NOT NULL DEFAULT '1',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `email_verified_at` datetime DEFAULT NULL COMMENT 'When this email address was proven via a registration code. NULL = never verified.',
  `email_verification_exempt` tinyint(1) NOT NULL DEFAULT '0' COMMENT 'Grandfathered account that predates email verification; may sign in unverified.',
  PRIMARY KEY (`id`),
  UNIQUE KEY `email` (`email`),
  KEY `fk_users_department` (`department_id`),
  KEY `idx_users_role` (`role`),
  KEY `idx_users_email_verified` (`email_verified_at`),
  KEY `idx_users_dept_code` (`dept_code`),
  CONSTRAINT `fk_users_department` FOREIGN KEY (`department_id`) REFERENCES `departments` (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=37 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `zone_wards` (
  `ward_number` int NOT NULL,
  `zone_id` int NOT NULL,
  `source` varchar(255) DEFAULT NULL COMMENT 'Dataset the mapping came from',
  `source_version` varchar(64) DEFAULT NULL COMMENT 'fetchedAt of that dataset',
  PRIMARY KEY (`ward_number`),
  KEY `idx_zone_wards_zone` (`zone_id`),
  CONSTRAINT `fk_zone_wards_zone` FOREIGN KEY (`zone_id`) REFERENCES `zones` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `zones` (
  `id` int NOT NULL AUTO_INCREMENT,
  `zone_number` int NOT NULL,
  `zone_name` varchar(100) COLLATE utf8mb4_unicode_ci NOT NULL,
  `ward_start` int NOT NULL,
  `ward_end` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `zone_number` (`zone_number`)
) ENGINE=InnoDB AUTO_INCREMENT=16 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

