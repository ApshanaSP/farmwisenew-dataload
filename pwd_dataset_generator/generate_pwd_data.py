#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_pwd_data.py
====================
Synthetic, "live-looking" seed data for the PWD (Public Works Department) slice of the
Collector's District Intelligence Dashboard - Chennai District (FarmwiseAI Task 6).

Standard library only, Python 3.10+, Windows / Linux / macOS.

How realism and stability work
------------------------------
* NOW = current Asia/Kolkata time (or --now). Window = NOW - 90 days .. NOW.
* Every calendar day is generated from its own RNG (base seed + date + stream name), so the rows
  of a day never change between runs. Each record's whole lifecycle (acceptance, completion,
  resolution ...) is drawn up-front; the status you see is simply "how far NOW has got" along
  that lifecycle. Re-running tomorrow adds the new day, drops the oldest one, advances statuses.
* IDs are day-blocked (see *_BLOCK) so they are stable across runs; numeric gaps are expected.
* Lake / reservoir storage is simulated day by day from ID_ANCHOR, so history is reproducible.

Outputs (UTF-8 with BOM): pwd_offices, pwd_assets, pwd_water_levels, pwd_works, pwd_incidents,
pwd_tasks, pwd_announcements (.csv). No scores, flags, language, period or audit columns.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

# ----------------------------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------------------------
GRIEVANCE_ID_FORMAT = "GRV-{date:%Y%m%d}-{seq:05d}"

IST = timezone(timedelta(hours=5, minutes=30))
WINDOW_DAYS = 180
ID_ANCHOR = date(2024, 1, 1)          # ID blocks, event lattice and lake simulation start here
MIN_NOW = datetime(2024, 4, 1, tzinfo=IST)
WORKS_CATALOG_START = date(2022, 7, 1)
INC_BLOCK, TSK_BLOCK, ANN_BLOCK = 60, 20, 4
EVENT_BLOCK_DAYS = 30
MCFT_PER_CUSEC_DAY = 0.0864           # 1 cusec for 24 h = 86,400 ft3

SEED = 42  # overwritten from --seed

DEFAULT_TALUKS = [
    ("TLK01", "Alandur", 12.998, 80.201), ("TLK02", "Ambattur", 13.114, 80.154),
    ("TLK03", "Aminjikarai", 13.073, 80.224), ("TLK04", "Ayanavaram", 13.098, 80.233),
    ("TLK05", "Egmore", 13.078, 80.261), ("TLK06", "Guindy", 13.007, 80.213),
    ("TLK07", "Madhavaram", 13.148, 80.231), ("TLK08", "Maduravoyal", 13.066, 80.170),
    ("TLK09", "Mambalam", 13.037, 80.223), ("TLK10", "Mylapore", 13.034, 80.268),
    ("TLK11", "Perambur", 13.117, 80.233), ("TLK12", "Purasawalkam", 13.088, 80.255),
    ("TLK13", "Sholinganallur", 12.901, 80.227), ("TLK14", "Thiruvottiyur", 13.160, 80.300),
    ("TLK15", "Tondiarpet", 13.126, 80.288), ("TLK16", "Velachery", 12.979, 80.221),
]

LOCALITIES = {
    "TLK01": ["Alandur", "St. Thomas Mount", "Nanganallur", "Meenambakkam", "Adambakkam",
              "Palavanthangal", "Nandambakkam", "Moovarasampet"],
    "TLK02": ["Ambattur", "Ambattur OT", "Korattur", "Padi", "Mogappair", "Pattaravakkam",
              "Athipet", "Ambattur Industrial Estate"],
    "TLK03": ["Aminjikarai", "Anna Nagar", "Arumbakkam", "Shenoy Nagar", "Anna Nagar West",
              "NSK Nagar", "Choolaimedu"],
    "TLK04": ["Ayanavaram", "Villivakkam", "Kolathur", "Periyar Nagar", "ICF Colony", "Jawahar Nagar"],
    "TLK05": ["Egmore", "Chetpet", "Chintadripet", "Periamet", "Vepery", "Pudupet", "Nungambakkam"],
    "TLK06": ["Guindy", "Saidapet", "Ekkatuthangal", "Little Mount", "Jafferkhanpet",
              "Guindy Industrial Estate"],
    "TLK07": ["Madhavaram", "Madhavaram Milk Colony", "Puzhal", "Retteri", "Mathur", "Manjambakkam"],
    "TLK08": ["Maduravoyal", "Valasaravakkam", "Porur", "Alapakkam", "Nerkundram", "Koyambedu",
              "Virugambakkam", "Nolambur"],
    "TLK09": ["West Mambalam", "T. Nagar", "K.K. Nagar", "Ashok Nagar", "Kodambakkam", "Vadapalani",
              "M.G.R. Nagar"],
    "TLK10": ["Mylapore", "Mandaveli", "Adyar", "Kotturpuram", "Triplicane", "R.A. Puram", "Alwarpet",
              "Santhome", "Royapettah"],
    "TLK11": ["Perambur", "Sembiam", "Vyasarpadi", "Kodungaiyur", "Jamalia", "Perambur Barracks"],
    "TLK12": ["Purasawalkam", "Kilpauk", "Otteri", "Pulianthope", "Choolai", "Pattalam"],
    "TLK13": ["Sholinganallur", "Perungudi", "Thoraipakkam", "Karapakkam", "Semmancheri",
              "Okkiyam Thoraipakkam", "Pallikaranai", "Neelankarai", "Injambakkam"],
    "TLK14": ["Thiruvottiyur", "Ennore", "Manali", "Kathivakkam", "Ernavoor", "Wimco Nagar"],
    "TLK15": ["Tondiarpet", "Old Washermanpet", "Royapuram", "Korukkupet", "Kasimedu",
              "New Washermanpet", "George Town"],
    "TLK16": ["Velachery", "Taramani", "Vijaya Nagar", "Baby Nagar", "Ram Nagar", "Dhandeeswaram",
              "Thiruvanmiyur"],
}

# (name, asset_type, taluk number, locality, capacity_mcft)
ASSET_DEFS = [
    ("Adambakkam Lake", "lake", 1, "Adambakkam", 28), ("Palavanthangal Lake", "lake", 1, "Palavanthangal", 14),
    ("Ambattur Lake", "lake", 2, "Ambattur", 110), ("Korattur Lake", "lake", 2, "Korattur", 150),
    ("Mogappair Eri", "lake", 2, "Mogappair", 18), ("Kolathur Lake", "lake", 4, "Kolathur", 30),
    ("Chetpet Lake", "lake", 5, "Chetpet", 6), ("Madhavaram Lake", "lake", 7, "Madhavaram", 65),
    ("Retteri Lake", "lake", 7, "Retteri", 95), ("Porur Lake", "lake", 8, "Porur", 48),
    ("Nolambur Lake", "lake", 8, "Nolambur", 12), ("Perungudi Lake", "lake", 13, "Perungudi", 40),
    ("Velachery Lake", "lake", 16, "Velachery", 22),
    ("Adyar River - Nandambakkam stretch", "river_stretch", 1, "Nandambakkam", None),
    ("Adyar River - Jafferkhanpet stretch", "river_stretch", 6, "Jafferkhanpet", None),
    ("Adyar River - Saidapet stretch", "river_stretch", 6, "Saidapet", None),
    ("Adyar River - Kotturpuram stretch", "river_stretch", 10, "Kotturpuram", None),
    ("Adyar River - Estuary stretch", "river_stretch", 10, "Santhome", None),
    ("Cooum River - Nerkundram stretch", "river_stretch", 8, "Nerkundram", None),
    ("Cooum River - Koyambedu stretch", "river_stretch", 8, "Koyambedu", None),
    ("Cooum River - Arumbakkam stretch", "river_stretch", 3, "Arumbakkam", None),
    ("Cooum River - Chetpet stretch", "river_stretch", 5, "Chetpet", None),
    ("Kosasthalaiyar River - Manali stretch", "river_stretch", 14, "Manali", None),
    ("Buckingham Canal - Ennore stretch", "canal", 14, "Ennore", None),
    ("Buckingham Canal - Korukkupet stretch", "canal", 15, "Korukkupet", None),
    ("Buckingham Canal - Basin Bridge stretch", "canal", 12, "Pulianthope", None),
    ("Buckingham Canal - Mandaveli stretch", "canal", 10, "Mandaveli", None),
    ("Buckingham Canal - Thiruvanmiyur stretch", "canal", 16, "Thiruvanmiyur", None),
    ("Buckingham Canal - Okkiyam stretch", "canal", 13, "Okkiyam Thoraipakkam", None),
    ("Otteri Nullah - Kilpauk stretch", "canal", 12, "Kilpauk", None),
    ("Otteri Nullah - Ayanavaram stretch", "canal", 4, "Ayanavaram", None),
    ("Otteri Nullah - Perambur stretch", "canal", 11, "Perambur", None),
    ("Captain Cotton Canal - Vyasarpadi stretch", "canal", 11, "Vyasarpadi", None),
    ("Virugambakkam-Arumbakkam Canal", "canal", 8, "Virugambakkam", None),
    ("Veerangal Odai", "canal", 16, "Velachery", None),
    ("Anna Nagar Macro Drain", "drain", 3, "Anna Nagar", None),
    ("Villivakkam Macro Drain", "drain", 4, "Villivakkam", None),
    ("Mambalam Canal Macro Drain", "drain", 9, "T. Nagar", None),
    ("Nanganallur Macro Drain", "drain", 1, "Nanganallur", None),
    ("Guindy Industrial Estate Storm Water Channel", "drain", 6, "Guindy Industrial Estate", None),
    ("Pallikaranai Marsh Outlet Drain", "drain", 13, "Pallikaranai", None),
    ("Kodungaiyur Canal", "drain", 11, "Kodungaiyur", None),
    ("Kathivakkam Macro Drain", "drain", 14, "Kathivakkam", None),
    ("Tondiarpet Macro Drain", "drain", 15, "Tondiarpet", None),
    ("Madhavaram Surplus Drain", "drain", 7, "Madhavaram Milk Colony", None),
    ("Valasaravakkam Macro Drain", "drain", 8, "Valasaravakkam", None),
    ("Velachery Lake Surplus Sluice", "sluice", 16, "Velachery", None),
    ("Ambattur Lake Sluice", "sluice", 2, "Ambattur", None),
    ("Korattur Lake Sluice", "sluice", 2, "Korattur", None),
    ("Retteri Lake Sluice", "sluice", 7, "Retteri", None),
    ("Adambakkam Lake Sluice", "sluice", 1, "Adambakkam", None),
    ("Otteri Nullah Regulator, Basin Bridge", "sluice", 12, "Pulianthope", None),
    ("Ennore Creek Regulator", "sluice", 14, "Ennore", None),
    ("Adyar River Bund - Jafferkhanpet", "bund", 6, "Jafferkhanpet", None),
    ("Adyar River Bund - Kotturpuram", "bund", 10, "Kotturpuram", None),
    ("Cooum River Bund - Arumbakkam", "bund", 3, "Arumbakkam", None),
    ("Velachery Lake Bund", "bund", 16, "Velachery", None),
    ("Retteri Lake Bund", "bund", 7, "Retteri", None),
    ("Korattur Lake Bund", "bund", 2, "Korattur", None),
    ("Kosasthalaiyar Bund - Manali", "bund", 14, "Manali", None),
    ("Porur Lake Bund", "bund", 8, "Porur", None),
    ("Rajiv Gandhi Govt General Hospital - Tower Block", "govt_building", 5, "Periamet", None),
    ("Institute of Child Health, Egmore", "govt_building", 5, "Egmore", None),
    ("Govt Kilpauk Medical College Hospital", "govt_building", 12, "Kilpauk", None),
    ("Govt Stanley Hospital - OP Block", "govt_building", 15, "Old Washermanpet", None),
    ("Govt Royapettah Hospital", "govt_building", 10, "Royapettah", None),
    ("Govt Peripheral Hospital, Periyar Nagar", "govt_building", 4, "Periyar Nagar", None),
    ("Govt Peripheral Hospital, K.K. Nagar", "govt_building", 9, "K.K. Nagar", None),
    ("King Institute of Preventive Medicine", "govt_building", 6, "Guindy", None),
    ("Govt Hr Sec School, Ashok Nagar", "govt_building", 9, "Ashok Nagar", None),
    ("Govt Girls Hr Sec School, Ambattur", "govt_building", 2, "Ambattur", None),
    ("Govt Polytechnic College, Taramani", "govt_building", 16, "Taramani", None),
    ("Ezhilagam Govt Office Complex", "govt_building", 10, "Triplicane", None),
    ("Collectorate Building, Rajaji Salai", "govt_building", 15, "George Town", None),
    ("Sholinganallur Taluk Office", "govt_building", 13, "Sholinganallur", None),
    ("Maraimalai Adigal Bridge, Saidapet", "bridge", 6, "Saidapet", None),
    ("Thiru. Vi. Ka. Bridge, Adyar", "bridge", 10, "Adyar", None),
    ("Napier Bridge", "bridge", 10, "Triplicane", None),
    ("Aminjikarai Cooum Bridge", "bridge", 3, "Aminjikarai", None),
    ("Basin Bridge", "bridge", 12, "Pulianthope", None),
    ("Manali Kosasthalaiyar Causeway", "bridge", 14, "Manali", None),
    ("Okkiyam Madavu Bridge", "bridge", 13, "Okkiyam Thoraipakkam", None),
]
# Upstream reservoirs (outside the 16 taluks -> taluk_code blank): name, locality, cap, lat, lon, downstream taluks
RESERVOIR_DEFS = [
    ("Chembarambakkam Reservoir", "Chembarambakkam", 3645, 12.9720, 80.0520, ["TLK01", "TLK06", "TLK10"]),
    ("Puzhal Reservoir (Red Hills)", "Puzhal", 3300, 13.1640, 80.1780, ["TLK07", "TLK14"]),
    ("Poondi Reservoir (Sathyamoorthy Sagar)", "Poondi", 3231, 13.1940, 79.8600, ["TLK07", "TLK14"]),
    ("Cholavaram Reservoir", "Cholavaram", 1081, 13.2300, 80.1450, ["TLK07", "TLK14"]),
    ("Kannankottai-Thervoy Kandigai Reservoir", "Kannankottai", 500, 13.4000, 79.9650, []),
]

ALL = "ALL"
# name, wing, designation, taluk numbers, routing role, email local part
OFFICE_DEFS = [
    ("Office of the Chief Engineer, PWD (Chennai Region)", "HQ", "Chief Engineer", ALL, "hq", "ce.chennai"),
    ("Buildings Circle, Chennai", "Buildings", "Superintending Engineer", ALL, "circle", "se.bldg.chennai"),
    ("Buildings Division (North), Chennai", "Buildings", "Executive Engineer", [4, 5, 7, 11, 12, 14, 15], "division", "ee.bldg.north"),
    ("Buildings Division (South), Chennai", "Buildings", "Executive Engineer", [1, 6, 9, 10, 13, 16], "division", "ee.bldg.south"),
    ("Buildings Division (West), Chennai", "Buildings", "Executive Engineer", [2, 3, 8], "division", "ee.bldg.west"),
    ("Medical Buildings Division, Chennai", "Buildings", "Executive Engineer", ALL, "medical", "ee.medical.bldg"),
    ("Educational Buildings Division, Chennai", "Buildings", "Executive Engineer", ALL, "education", "ee.edu.bldg"),
    ("Buildings Sub-Division, Guindy", "Buildings", "Assistant Executive Engineer", [1, 6, 16], "subdivision", "aee.bldg.guindy"),
    ("Buildings Sub-Division, Egmore", "Buildings", "Assistant Executive Engineer", [3, 5, 12], "subdivision", "aee.bldg.egmore"),
    ("Buildings Sub-Division, Tondiarpet", "Buildings", "Assistant Executive Engineer", [11, 14, 15], "subdivision", "aee.bldg.tondiarpet"),
    ("WRD Chennai Circle", "Water Resources", "Superintending Engineer", ALL, "circle", "se.wrd.chennai"),
    ("Adyar Basin Division", "Water Resources", "Executive Engineer", [1, 6, 9, 10, 13, 16], "division", "ee.adyar"),
    ("Cooum Basin Division", "Water Resources", "Executive Engineer", [3, 5, 8, 10, 12], "division", "ee.cooum"),
    ("Kosasthalaiyar Basin Division", "Water Resources", "Executive Engineer", [2, 7, 14], "division", "ee.kosasthalaiyar"),
    ("Buckingham Canal & Otteri Nullah Division", "Water Resources", "Executive Engineer", [4, 10, 11, 12, 13, 14, 15], "division", "ee.bcanal"),
    ("Chennai Lakes Sub-Division, Ambattur", "Water Resources", "Assistant Executive Engineer", [1, 2, 4, 7, 13, 16], "subdivision", "aee.lakes"),
    ("Adyar Sub-Division, Saidapet", "Water Resources", "Assistant Executive Engineer", [6, 9, 10], "subdivision", "aee.saidapet"),
    ("North Chennai Drainage Sub-Division", "Water Resources", "Assistant Executive Engineer", [4, 7, 11, 15], "subdivision", "aee.northdrain"),
    ("Flood Monitoring Cell & Control Room", "Water Resources", "Executive Engineer", ALL, "flood_cell", "floodcell"),
    ("Water Bodies Protection (Encroachment) Cell", "Water Resources", "Assistant Executive Engineer", ALL, "encroachment_cell", "aee.wbp"),
    ("Chennai Reservoirs Division, Puzhal", "Water Resources", "Executive Engineer", [2, 7, 14], "reservoir", "ee.reservoirs"),
]

FIRST_NAMES = ["Senthil Kumar", "Murugesan", "Lakshmi", "Kavitha", "Rajendran", "Anbalagan", "Selvi",
               "Karthikeyan", "Saravanan", "Revathi", "Balasubramanian", "Meenakshi", "Thirumalai",
               "Ilango", "Vasanthi", "Arulmozhi", "Sivakumar", "Jayanthi", "Palanisamy", "Gunasekaran",
               "Nandhini", "Venkatesan", "Kalaiselvi", "Muthukumar", "Sumathi", "Elangovan", "Tamilselvan",
               "Bharathi", "Dhanalakshmi", "Prabhakaran", "Sangeetha", "Manikandan", "Ezhil", "Chitra"]
INITIALS = ["R.", "S.", "K.", "M.", "P.", "A.", "V.", "T.", "N.", "G.", "C.", "J."]

# Tamil Nadu public holidays: fixed dates + approximate variable ones.
FIXED_HOLIDAYS = {(1, 1), (1, 15), (1, 16), (1, 17), (1, 26), (4, 14), (5, 1), (8, 15), (10, 2), (12, 25)}
VARIABLE_HOLIDAYS = {
    2024: ["01-25", "03-29", "04-11", "04-21", "06-17", "07-17", "08-26", "09-07", "09-16", "10-11", "10-12", "10-31"],
    2025: ["02-11", "03-30", "03-31", "04-10", "04-18", "06-07", "07-06", "08-16", "08-27", "09-05", "10-01", "10-20"],
    2026: ["02-01", "03-19", "03-21", "03-31", "04-03", "05-27", "06-26", "08-26", "09-04", "09-14", "10-19", "10-20", "11-08"],
    2027: ["01-23", "03-10", "03-26", "05-17", "06-15", "08-25", "09-04", "10-08", "10-09", "10-28"],
    2028: ["02-11", "02-27", "04-14", "05-05", "06-04", "08-13", "08-23", "09-26", "09-27", "10-17"],
}

# Monthly rain regime: (probability of a rainy day, mean mm on rainy day, spatial coverage)
MONTH_RAIN = {1: (0.12, 5, 0.8), 2: (0.05, 3, 0.6), 3: (0.04, 3, 0.5), 4: (0.06, 5, 0.5), 5: (0.12, 8, 0.5),
              6: (0.30, 8, 0.45), 7: (0.36, 10, 0.45), 8: (0.42, 12, 0.5), 9: (0.40, 12, 0.55),
              10: (0.55, 18, 0.8), 11: (0.60, 18, 0.9), 12: (0.45, 18, 0.85)}
NE_MONSOON = (10, 11, 12)
SW_MONSOON = (6, 7, 8, 9)

SOURCES = ["field_staff", "control_room", "collector_office", "citizen_grievance", "media"]
INCIDENT_TYPES = ["waterlogging", "canal_overflow", "bund_breach", "lake_surplus", "sluice_failure",
                  "building_crack", "wall_collapse", "encroachment", "blocked_drain", "bridge_damage"]
EMERGENCY_TYPES = {"waterlogging", "canal_overflow", "bund_breach", "lake_surplus", "wall_collapse"}

BASE_RATE = {1: 11.5, 2: 11.5, 3: 11.5, 4: 11.5, 5: 11.0, 6: 8.0, 7: 8.5, 8: 9.0, 9: 9.0, 10: 9.0, 11: 9.0, 12: 8.5}
BASE_TYPE_W = {"blocked_drain": 20, "building_crack": 15, "encroachment": 16, "sluice_failure": 8,
               "bridge_damage": 5, "wall_collapse": 5, "waterlogging": 6, "canal_overflow": 3, "bund_breach": 0.6}
RAIN_TYPE_W = {"waterlogging": 55, "canal_overflow": 18, "blocked_drain": 14, "wall_collapse": 6,
               "building_crack": 4, "bund_breach": 3, "bridge_damage": 2}
ASSET_TYPES_FOR = {
    "waterlogging": ["drain"], "canal_overflow": ["canal", "river_stretch", "drain"], "bund_breach": ["bund", "lake"],
    "lake_surplus": ["lake"], "sluice_failure": ["sluice"], "building_crack": ["govt_building"],
    "wall_collapse": ["govt_building", "canal", "river_stretch"], "encroachment": ["lake", "canal", "river_stretch", "drain"],
    "blocked_drain": ["drain", "canal"], "bridge_damage": ["bridge"],
}
ASSET_OPTIONAL = {"waterlogging": 0.75, "wall_collapse": 0.35, "canal_overflow": 0.05, "blocked_drain": 0.05,
                  "encroachment": 0.03}  # probability of leaving asset_id blank

SOURCE_W = {
    "waterlogging": {"citizen_grievance": 34, "control_room": 26, "field_staff": 20, "media": 8, "collector_office": 12},
    "canal_overflow": {"field_staff": 34, "control_room": 26, "citizen_grievance": 22, "media": 8, "collector_office": 10},
    "bund_breach": {"field_staff": 35, "control_room": 30, "citizen_grievance": 15, "media": 10, "collector_office": 10},
    "lake_surplus": {"field_staff": 45, "control_room": 25, "citizen_grievance": 15, "media": 10, "collector_office": 5},
    "sluice_failure": {"field_staff": 60, "control_room": 10, "citizen_grievance": 20, "collector_office": 8, "media": 2},
    "building_crack": {"field_staff": 40, "citizen_grievance": 22, "collector_office": 28, "media": 8, "control_room": 2},
    "wall_collapse": {"citizen_grievance": 30, "control_room": 25, "field_staff": 25, "media": 10, "collector_office": 10},
    "encroachment": {"field_staff": 38, "citizen_grievance": 36, "collector_office": 14, "media": 10, "control_room": 2},
    "blocked_drain": {"citizen_grievance": 40, "field_staff": 32, "control_room": 14, "collector_office": 8, "media": 6},
    "bridge_damage": {"field_staff": 40, "citizen_grievance": 25, "media": 15, "collector_office": 12, "control_room": 8},
}
LANG_BY_SOURCE = {"citizen_grievance": (0.30, 0.25), "media": (0.28, 0.08), "field_staff": (0.14, 0.14),
                  "control_room": (0.12, 0.12), "collector_office": (0.12, 0.05)}  # (tamil, tanglish)

PEOPLE_RANGE = {"waterlogging": (30, 600), "canal_overflow": (50, 1200), "bund_breach": (200, 3000),
                "lake_surplus": (40, 800), "sluice_failure": (10, 300), "building_crack": (10, 400),
                "wall_collapse": (5, 120), "encroachment": (20, 500), "blocked_drain": (20, 500),
                "bridge_damage": (100, 5000)}
PEOPLE_BLANK = {"encroachment": 0.45, "sluice_failure": 0.35, "building_crack": 0.30}
NICE_NUMBERS = [5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 80, 100, 120, 150, 200, 250, 300, 400, 500, 600,
                750, 1000, 1200, 1500, 2000, 2500, 3000, 4000, 5000]
ROUNDER = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000]

# Incident self-resolution (days, median) for incidents without a Collector task
RESOLVE_MEDIAN_D = {"waterlogging": 1.2, "canal_overflow": 2.0, "bund_breach": 2.0, "lake_surplus": 5.0,
                    "sluice_failure": 7.0, "building_crack": 14.0, "wall_collapse": 6.0, "encroachment": 18.0,
                    "blocked_drain": 4.0, "bridge_damage": 15.0}
TASK_WORK_MEDIAN_D = {"waterlogging": 0.5, "canal_overflow": 1.0, "bund_breach": 0.8, "lake_surplus": 2.0,
                      "sluice_failure": 5.0, "building_crack": 12.0, "wall_collapse": 5.0, "encroachment": 10.0,
                      "blocked_drain": 3.0, "bridge_damage": 14.0, "bund_weak": 12.0}
TASK_DUE_DAYS = {"waterlogging": (1, 3), "canal_overflow": (2, 5), "bund_breach": (1, 3), "lake_surplus": (3, 7),
                 "sluice_failure": (10, 25), "building_crack": (30, 60), "wall_collapse": (7, 21),
                 "encroachment": (30, 60), "blocked_drain": (7, 21), "bridge_damage": (30, 60), "bund_weak": (30, 60)}

WORK_TYPE_BASE_W = {"desilting": 5, "bund_strengthening": 2, "canal_lining": 1, "sluice_repair": 1.2,
                    "building_construction": 0.8, "building_repair": 3, "bridge_repair": 0.8,
                    "encroachment_removal": 1.3, "flood_mitigation": 1.2}
WORK_ASSET_TYPES = {"desilting": ["lake", "river_stretch", "canal", "drain"],
                    "bund_strengthening": ["bund", "lake", "river_stretch"], "canal_lining": ["canal", "drain"],
                    "sluice_repair": ["sluice"], "building_construction": ["govt_building"],
                    "building_repair": ["govt_building"], "bridge_repair": ["bridge"],
                    "encroachment_removal": ["lake", "canal", "river_stretch", "drain"],
                    "flood_mitigation": ["river_stretch", "canal", "drain", "lake"]}
WORK_DUR = {"desilting": (25, 90), "bund_strengthening": (45, 150), "canal_lining": (90, 300),
            "sluice_repair": (20, 75), "building_construction": (270, 720), "building_repair": (30, 150),
            "bridge_repair": (60, 240), "encroachment_removal": (7, 45), "flood_mitigation": (60, 270)}
WORK_AMOUNT = {"desilting": (8, 120), "bund_strengthening": (25, 350), "canal_lining": (150, 1800),
               "sluice_repair": (5, 60), "building_construction": (300, 4500), "building_repair": (10, 250),
               "bridge_repair": (40, 900), "encroachment_removal": (2, 25), "flood_mitigation": (80, 2500)}

# Hour-of-day weights
FIELD_W = [0.3, 0.2, 0.15, 0.15, 0.2, 0.6, 1.6, 4.0, 5.0, 5.0, 4.0, 2.4, 1.8, 1.5, 1.6, 2.2, 3.6, 4.6, 4.6, 4.0, 2.8, 1.6, 0.9, 0.5]
CONTROL_W = [2.0, 1.9, 1.7, 1.6, 1.6, 1.8, 2.2, 2.6, 3.0, 3.0, 3.0, 3.0, 2.9, 2.9, 3.0, 3.0, 3.1, 3.2, 3.2, 3.1, 2.9, 2.6, 2.4, 2.2]
OFFICE_W = [0, 0, 0, 0, 0, 0, 0, 0, 0.2, 0.8, 3.0, 4.0, 4.0, 2.0, 3.5, 4.0, 3.5, 2.5, 0.5, 0, 0, 0, 0, 0]
MEDIA_W = [0.2, 0.1, 0.1, 0.1, 0.2, 0.6, 1.5, 2.5, 3.0, 3.0, 2.8, 2.6, 2.4, 2.4, 2.4, 2.4, 2.6, 2.8, 3.0, 3.0, 2.8, 2.4, 1.2, 0.6]

# ----------------------------------------------------------------------------------------------
# Text resources
# ----------------------------------------------------------------------------------------------
STREETS = ["Main Rd", "2nd Main Rd", "3rd Cross St", "1st Avenue", "Kamarajar St", "Gandhi St", "Nehru St",
           "Bharathi St", "Periyar St", "Vinayagar Koil St", "Mettu St", "Pillaiyar Koil St", "Canal Bank Rd",
           "Lake View Rd", "School St", "Market Rd", "Railway Station Rd", "Bajanai Koil St", "Mosque St",
           "Church St", "Thiruvalluvar St", "VOC St", "Rajiv Gandhi St", "MGR St", "Ambedkar St", "4th St",
           "5th Cross St", "East St", "West St", "North St", "South St", "Old Colony 1st St", "New Colony 2nd St",
           "Ganesh Nagar 1st St", "Indira Nagar 3rd Ave", "Balaji Nagar Main Rd", "Sathya Nagar 2nd St",
           "Kalaignar Nagar", "Subramaniyar Koil St", "Anna St", "Kannadasan St", "Sannathi St", "Tank St",
           "Bazaar St", "Ellaiamman Koil St", "Srinivasa Nagar 6th St", "Jeevan Nagar 2nd St", "Karpagam Ave"]
LANDMARKS = {
    "en": ["near bus stop", "opp. ration shop", "near Amma Unavagam", "behind govt school", "near EB office",
           "near Murugan temple", "near church", "near masjid", "opp. petrol bunk", "near PHC", "near railway gate",
           "near subway", "near park", "near OHT water tank", "near community hall", "near police booth",
           "next to market", "near ATM", "near marriage hall", "opp. medical shop"],
    "ta": ["பேருந்து நிறுத்தம் அருகில்", "ரேஷன் கடை எதிரில்", "அம்மா உணவகம் அருகில்", "அரசு பள்ளி பின்புறம்",
           "மின்வாரிய அலுவலகம் அருகில்", "முருகன் கோவில் அருகில்", "சர்ச் அருகில்", "பள்ளிவாசல் அருகில்",
           "பெட்ரோல் பங்க் எதிரில்", "சுகாதார நிலையம் அருகில்", "ரயில்வே கேட் அருகில்", "சுரங்கப்பாதை அருகில்",
           "பூங்கா அருகில்", "தண்ணீர் தொட்டி அருகில்", "சமுதாய கூடம் அருகில்", "மார்க்கெட் அருகில்"],
    "tg": ["bus stop pakkathula", "ration kadai opposite", "amma unavagam kitta", "govt school pinnadi",
           "EB office kitta", "murugan kovil pakkam", "church pakkathula", "masjid kitta", "petrol bunk opposite",
           "PHC pakkathula", "railway gate kitta", "subway kitta", "park pakkathula", "water tank kitta",
           "market pakkathula", "marriage hall kitta"],
}
TIME_PHRASES = {
    "en": ["Since last night.", "Since morning.", "From 5 am.", "For past 3 hrs.", "Since yesterday evening.",
           "After today's rain.", "For 2 days now.", "Since noon.", "", "Still continuing.", "Since 4 days.", ""],
    "ta": ["நேற்று இரவு முதல்.", "காலை முதல்.", "கடந்த 3 மணி நேரமாக.", "நேற்று மாலை முதல்.",
           "இன்று பெய்த மழைக்குப் பிறகு.", "இரண்டு நாட்களாக.", "", "ஒரு வாரமாக."],
    "tg": ["nethu night la irundhu.", "morning la irundhu.", "3 hours ah.", "nethu evening la irundhu.",
           "inniku mazhai ku apram.", "2 naala.", "", "oru vaarama."],
}
PLEAS = {
    "en": ["Pls take action.", "Kindly attend immediately.", "Residents facing hardship.", "Urgent.",
           "Need pumping.", "", "School children affected.", "Elderly people struggling.", "Requesting inspection.",
           "No one has come so far.", "Pls send team.", "Kindly do the needful."],
    "ta": ["உடனடியாக நடவடிக்கை எடுக்கவும்.", "பொதுமக்கள் மிகவும் அவதிப்படுகின்றனர்.", "அவசரம்.", "",
           "விரைவில் சரி செய்ய வேண்டுகிறோம்.", "இதுவரை யாரும் வரவில்லை."],
    "tg": ["pls action edunga.", "sir konjam seekiram paarunga.", "romba kashtama irukku.", "urgent sir.", "",
           "yaarum vara la.", "please help pannunga."],
}
DEPTH = {"en": ["ankle-deep", "about half a foot", "knee-deep", "about 1.5 ft", "nearly 2 ft", "waist-level"],
         "ta": ["கணுக்கால் அளவு", "அரை அடி", "முழங்கால் அளவு", "ஒன்றரை அடி", "இரண்டு அடி", "இடுப்பளவு"],
         "tg": ["ankle level", "half adi", "muttu alavu", "1.5 adi", "2 adi", "idupu alavu"]}
GENERIC_ASSET = {
    "en": {"blocked_drain": "the storm water drain", "canal_overflow": "the canal", "wall_collapse": "the canal bank",
           "encroachment": "the water body", "bund_breach": "the bund", "lake_surplus": "the lake",
           "sluice_failure": "the sluice", "building_crack": "the govt building", "bridge_damage": "the bridge",
           "waterlogging": "the drain"},
    "ta": {"blocked_drain": "மழைநீர் வடிகால்", "canal_overflow": "கால்வாய்", "wall_collapse": "கால்வாய் கரை",
           "encroachment": "நீர்நிலை", "bund_breach": "கரை", "lake_surplus": "ஏரி", "sluice_failure": "மதகு",
           "building_crack": "அரசு கட்டிடம்", "bridge_damage": "பாலம்", "waterlogging": "வடிகால்"},
    "tg": {"blocked_drain": "drain", "canal_overflow": "canal", "wall_collapse": "canal karai",
           "encroachment": "eri", "bund_breach": "karai", "lake_surplus": "eri", "sluice_failure": "madagu",
           "building_crack": "govt building", "bridge_damage": "bridge", "waterlogging": "drain"},
}

TPL = {"en": {}, "ta": {}, "tg": {}}
TPL["en"]["waterlogging"] = [
    "Rain water stagnant {depth} on {street}, {loc} {lm}. {tp} {plea}",
    "Waterlogging reported at {street}, {loc} - {depth} water, vehicles unable to move. {plea}",
    "Water entered houses at {loc} ({street}) {tp_l} {depth} inside. {plea}",
    "{loc}: {street} fully flooded {lm}, {depth}. Drain not taking water. {plea}",
    "Heavy stagnation {lm} in {loc}, approx {depth}. Two-wheelers stranded. {plea}",
    "Low lying area {street}, {loc} inundated. Water level {depth}. {tp} {plea}",
    "Pls send pumpset to {street}, {loc}. Water {depth} {lm}. {tp}",
    "{depth} water on {street} near {loc} bus route, traffic diverted. {tp}",
]
TPL["en"]["canal_overflow"] = [
    "{asset} overflowing near {street}, {loc}. Water entering nearby houses. {plea}",
    "Water level in {asset} at brim {lm}, {loc}. Risk of overflow into streets. {tp}",
    "{asset} spilling over bank at {loc} ({street}). {plea}",
    "Canal water flowing into {street}, {loc} from {asset}. {tp} {plea}",
    "Overflow from {asset} {lm} - {loc} residents alarmed. {plea}",
    "{asset}: bank level crossed at {loc}, water on road {depth}. {tp}",
]
TPL["en"]["bund_breach"] = [
    "Breach noticed in {asset} near {loc} {lm}. Water gushing out. {plea}",
    "{asset} damaged/breached at {loc}, approx {n10} m length. {plea}",
    "Seepage turned to breach at {asset}, {loc}. Sand bags required urgently. {tp}",
    "Bund cut in {asset} ({loc}), water entering {street}. {tp} {plea}",
    "Portion of {asset} collapsed near {street}, {loc}. {plea}",
]
TPL["en"]["lake_surplus"] = [
    "{asset} full and surplussing through weir, {loc}. {plea}",
    "Surplus water from {asset} flowing into {street}, {loc}. {tp}",
    "{asset} reached FTL, surplus course {lm} overflowing. {plea}",
    "Kalingal of {asset} running, water on {street}, {loc}. {plea}",
    "{asset} brimming; surplus channel near {street} choked, water spreading in {loc}. {tp}",
]
TPL["en"]["sluice_failure"] = [
    "{asset} shutter not operating, stuck half open. {loc}. {plea}",
    "Sluice at {asset} ({loc}) leaking heavily {lm}. {plea}",
    "{asset}: gate screw rod broken, unable to regulate flow. {tp} {plea}",
    "Shutter of {asset} jammed with debris, {loc}. {tp}",
    "{asset} gate rusted, cannot be closed fully. Water escaping towards {street}. {plea}",
]
TPL["en"]["building_crack"] = [
    "Cracks observed on wall of {asset}, {loc}. {plea}",
    "Roof slab of {asset} leaking, plaster falling. {tp} {plea}",
    "Structural crack in {asset} block, {loc}. Staff worried. {plea}",
    "{asset}: ceiling plaster fell in one room. No injury. {tp} {plea}",
    "Hairline cracks widening on pillars at {asset}, {loc}. Requesting inspection.",
    "Water seepage and cracks in {asset} staircase area, {loc}. {plea}",
    "Sunshade portion of {asset} broken and hanging, {loc}. {plea}",
]
TPL["en"]["wall_collapse"] = [
    "Compound wall collapsed at {asset}, {loc}. {tp} {plea}",
    "Retaining wall near {street}, {loc} caved in {lm}. {plea}",
    "Portion of compound wall fell on road at {street}, {loc}. {plea}",
    "Old wall collapsed after rain {lm}, {loc}. Debris on road. {plea}",
    "Wall along {asset} gave way near {street}, {loc}. {tp}",
]
TPL["en"]["encroachment"] = [
    "Encroachment on {asset} at {loc} - new huts coming up {lm}. {plea}",
    "Illegal construction inside {asset} waterspread area, {loc}. {plea}",
    "Debris dumping & encroachment along {asset} near {street}, {loc}. {plea}",
    "{asset} boundary encroached by shop owners at {loc}. {plea}",
    "Private party fencing part of {asset}, {loc}. {tp} {plea}",
    "Compound wall being built inside {asset} limits near {street}, {loc}. {plea}",
]
TPL["en"]["blocked_drain"] = [
    "{asset} blocked with silt and plastic at {loc} {lm}. Water not draining. {plea}",
    "Macro drain choked near {street}, {loc}. Stagnation. {tp} {plea}",
    "{asset} mouth blocked, backflow into {street}, {loc}. {plea}",
    "Silt accumulated in {asset} {lm}, {loc}. Desilting needed. {plea}",
    "Flow obstructed in {asset} at {loc} - water hyacinth & debris. {plea}",
    "Culvert of {asset} near {street} fully choked, {loc}. {tp}",
]
TPL["en"]["bridge_damage"] = [
    "Parapet wall of {asset} damaged, {loc}. Dangerous for vehicles. {plea}",
    "Cracks seen on deck of {asset} {lm}, {loc}. {plea}",
    "{asset}: railing broken and potholes on approach, {loc}. {plea}",
    "Erosion below {asset} abutment after flow, {loc}. {tp} {plea}",
    "Expansion joint of {asset} damaged, vehicles jumping. {plea}",
]
TPL["ta"]["waterlogging"] = [
    "{street}, {loc} பகுதியில் {depth} மழைநீர் தேங்கியுள்ளது. {tp} {plea}",
    "{loc} {lm} சாலையில் தண்ணீர் தேங்கி வாகனங்கள் செல்ல முடியவில்லை. {plea}",
    "{loc} ({street}) வீடுகளுக்குள் மழைநீர் புகுந்தது, {depth} தண்ணீர். {plea}",
    "{tp} {loc} {street} தாழ்வான பகுதியில் தண்ணீர் வடியவில்லை. {plea}",
    "{loc} {lm} {depth} தண்ணீர், பள்ளி குழந்தைகள் செல்ல சிரமம். {plea}",
]
TPL["ta"]["canal_overflow"] = [
    "{asset} நிரம்பி {loc} {street} தெருவுக்குள் தண்ணீர் வருகிறது. {plea}",
    "{loc} {lm} {asset} கரையை தாண்டி நீர் வழிகிறது. {tp} {plea}",
    "{asset} நீர்மட்டம் உயர்ந்து குடியிருப்புகளுக்குள் புகும் அபாயம், {loc} {street}. {plea}",
]
TPL["ta"]["bund_breach"] = [
    "{asset} கரையில் உடைப்பு ஏற்பட்டு தண்ணீர் வெளியேறுகிறது, {loc}. {plea}",
    "{loc} {lm} {asset} கரை உடைந்துள்ளது. மணல் மூட்டைகள் தேவை. {plea}",
    "{tp} {asset} கரையில் கசிவு அதிகரித்து உடைப்பு, {street} பகுதிக்குள் நீர். {plea}",
]
TPL["ta"]["lake_surplus"] = [
    "{asset} நிரம்பி கலங்கல் வழியாக உபரி நீர் வெளியேறுகிறது, {loc}. {plea}",
    "{asset} உபரி நீர் {street}, {loc} பகுதிக்குள் செல்கிறது. {tp}",
    "{loc} {asset} முழு கொள்ளளவை எட்டியது, உபரி நீர் சாலையில். {plea}",
]
TPL["ta"]["sluice_failure"] = [
    "{asset} மதகு ஷட்டர் பழுதடைந்து இயங்கவில்லை, {loc}. {plea}",
    "{loc} {asset} மதகில் அதிக நீர் கசிவு. {tp} {plea}",
    "{asset} மதகு கதவு பாதியில் சிக்கியுள்ளது, {street} பக்கம் தண்ணீர் செல்கிறது. {plea}",
]
TPL["ta"]["building_crack"] = [
    "{asset} கட்டிட சுவரில் விரிசல் ஏற்பட்டுள்ளது, {loc}. {plea}",
    "{asset} மேற்கூரையில் விரிசல், மழைநீர் கசிகிறது. {tp} {plea}",
    "{loc} {asset} அறையில் மேற்கூரை பூச்சு விழுந்தது. {plea}",
    "{asset} தூண்களில் விரிசல் பெரிதாகி வருகிறது, {loc}. {tp}",
]
TPL["ta"]["wall_collapse"] = [
    "{loc} {street} அருகே சுற்றுச்சுவர் இடிந்து விழுந்தது. {plea}",
    "{asset} சுற்றுச்சுவர் மழையால் இடிந்தது, {loc}. {tp} {plea}",
    "{lm} பழைய சுவர் சாலையில் சரிந்து விழுந்தது, {loc}. {plea}",
]
TPL["ta"]["encroachment"] = [
    "{asset} கரையில் ஆக்கிரமிப்பு செய்து கட்டுமானம் நடைபெறுகிறது, {loc}. {plea}",
    "{loc} {asset} நீர்ப்பிடிப்பு பகுதியில் ஆக்கிரமிப்பு, புதிய குடிசைகள். {plea}",
    "{asset} அருகே {street} பகுதியில் கட்டிட கழிவுகள் கொட்டி ஆக்கிரமிப்பு. {tp} {plea}",
]
TPL["ta"]["blocked_drain"] = [
    "{asset} அடைப்பு, தண்ணீர் வடியவில்லை, {loc}. {plea}",
    "{loc} {lm} {asset} வண்டல் மற்றும் குப்பை சேர்ந்து அடைத்துள்ளது. {plea}",
    "{street}, {loc}: {asset} அடைப்பால் கழிவுநீர் பின்னோக்கி வருகிறது. {tp} {plea}",
    "{asset} ஆகாயத்தாமரை மண்டி ஓட்டம் தடைபட்டுள்ளது, {loc}. {plea}",
]
TPL["ta"]["bridge_damage"] = [
    "{asset} தடுப்புச் சுவர் சேதமடைந்துள்ளது, {loc}. {plea}",
    "{loc} {asset} மேல் விரிசல் தெரிகிறது. {plea}",
    "{asset} இணைப்பு சாலையில் அரிப்பு, வாகன ஓட்டிகள் அச்சம். {tp} {plea}",
]
TPL["tg"]["waterlogging"] = [
    "{street} {loc} la {depth} thanni thengi irukku, vandi poga mudiyala. {tp} {plea}",
    "{loc} {lm} rain water romba stagnant ah irukku. {plea}",
    "veetukulla thanni vandhuduchu {loc} {street}, {depth}. {plea}",
    "{tp} {loc} low area la thanni poga maatengudhu, motor venum. {plea}",
    "{loc} {street} road full ah thanni, {depth}. {plea}",
]
TPL["tg"]["canal_overflow"] = [
    "{asset} full ah overflow aagudhu, {loc} {street} kulla thanni varudhu. {plea}",
    "{loc} {lm} {asset} karai thaandi thanni pogudhu. {tp} {plea}",
    "{asset} level romba yeriduchu {loc}, veedu kulla vara chance irukku. {plea}",
]
TPL["tg"]["bund_breach"] = [
    "{asset} karai odanjiduchu, thanni veliya pogudhu {loc}. {plea}",
    "{loc} {lm} {asset} bund break aayiduchu, sand bag venum. {plea}",
    "{tp} {asset} karai la leak jaasthi aagi odanjiduchu, {street} kulla thanni. {plea}",
]
TPL["tg"]["lake_surplus"] = [
    "{asset} full, surplus thanni {street} {loc} kulla varudhu. {plea}",
    "{loc} {asset} kalingal la thanni pogudhu, road full ah thanni. {tp}",
    "{asset} full capacity reach aayiduchu {loc}. {plea}",
]
TPL["tg"]["sluice_failure"] = [
    "{asset} shutter work aagala {loc}. {plea}",
    "{loc} {asset} shutter stuck, thanni leak aagitte irukku. {tp} {plea}",
    "{asset} gate open aagala, flow control panna mudiyala. {plea}",
]
TPL["tg"]["building_crack"] = [
    "{asset} building wall la crack vandhuruku {loc}. {plea}",
    "{asset} roof la crack, mazhai thanni leak aagudhu. {tp} {plea}",
    "{loc} {asset} la ceiling plaster vizhundhuduchu. {plea}",
]
TPL["tg"]["wall_collapse"] = [
    "{loc} {street} kitta compound wall vizhundhuduchu. {plea}",
    "{asset} pakkam wall mazhai la odanju vizhundhuchu {loc}. {tp} {plea}",
    "{lm} pazhaya wall road la vizhundhuduchu {loc}. {plea}",
]
TPL["tg"]["encroachment"] = [
    "{asset} karai la aakramippu, veedu kattranga {loc}. {plea}",
    "{loc} {asset} la illegal ah fencing pottu irukanga. {plea}",
    "{asset} pakkathula {street} la debris kotti encroach panranga. {tp} {plea}",
]
TPL["tg"]["blocked_drain"] = [
    "{asset} full block, thanni poga maatengudhu {loc}. {plea}",
    "{loc} {lm} {asset} la silt, plastic ellam adaichirukku. {plea}",
    "{street} {loc} la {asset} block aagi drainage water thirumbi varudhu. {tp} {plea}",
]
TPL["tg"]["bridge_damage"] = [
    "{asset} side wall damage aayiduchu {loc}. {plea}",
    "{loc} {asset} la crack theriyudhu, bayama irukku. {plea}",
    "{asset} approach road la pallam, erosion aagudhu. {tp} {plea}",
]

SOURCE_PREFIX = {
    "control_room": ["CR: ", "Control room msg - ", "Call recd: ", "Flood cell: ", "Ph call from resident - "],
    "field_staff": ["AE site report: ", "Field: ", "JE inspected - ", "Site visit - ", "Patrol team: "],
    "media": ["Local TV news: ", "Social media post: ", "News report - ", "WhatsApp fwd: ", "X post: "],
    "collector_office": ["Collr inspection: ", "From Collectorate - ", "DRO visit: ", "As per Collector instructions, "],
    "citizen_grievance": ["Sir, ", "Respected sir, ", "Complaint: ", "Dear sir, ", ""],
}
ABBREV = [("road", "rd"), ("Road", "Rd"), ("near", "nr"), ("opposite", "opp"), ("please", "pls"),
          ("immediately", "immdtly"), ("water", "watr"), ("street", "st"), ("residents", "residnts"),
          ("people", "ppl"), ("required", "reqd"), ("received", "recd"), ("and", "&"), ("houses", "hses")]

TASK_TITLE = {
    "waterlogging": ["Pump out stagnant water - {street}, {loc}", "Clear waterlogging at {loc} ({street})",
                     "Deploy pumpset & drain water - {loc}"],
    "canal_overflow": ["Inspect & control overflow in {asset} at {loc}", "Sandbag bank of {asset} near {loc}"],
    "bund_breach": ["Plug breach in {asset}", "Emergency closure of breach - {asset}, {loc}"],
    "lake_surplus": ["Monitor surplus course of {asset} & clear obstructions", "Clear surplus channel of {asset}"],
    "sluice_failure": ["Repair shutter of {asset}", "Attend sluice gate defect - {asset}"],
    "building_crack": ["Structural inspection & repair - {asset}", "Attend roof leak / cracks - {asset}"],
    "wall_collapse": ["Clear debris & rebuild wall - {loc}", "Remove collapsed wall debris near {street}, {loc}"],
    "encroachment": ["Remove encroachment on {asset} at {loc}", "Survey & evict encroachment - {asset}"],
    "blocked_drain": ["Desilt / clear blockage in {asset} near {loc}", "Clear choked {asset} at {street}"],
    "bridge_damage": ["Repair parapet/deck of {asset}", "Inspect damage to {asset}"],
}
GRIEVANCE_KINDS = [  # (task itype, asset types, title templates, weight)
    ("blocked_drain", ["drain", "canal"], ["Petition: desilting of {asset} near {loc}",
                                           "Grievance - silt & garbage in {asset}, {loc}"], 30),
    ("encroachment", ["lake", "canal", "river_stretch"], ["Petition: remove encroachment on {asset}, {loc}",
                                                          "Grievance - illegal construction in {asset}"], 25),
    ("building_crack", ["govt_building"], ["Petition: repair leaking roof / cracks at {asset}",
                                           "Grievance - unsafe building condition, {asset}"], 18),
    ("sluice_failure", ["sluice"], ["Petition: {asset} shutter not closing properly"], 8),
    ("bund_weak", ["bund", "lake"], ["Petition: strengthen weak bund of {asset}",
                                     "Grievance - seepage through {asset} bund, {loc}"], 9),
    ("blocked_drain", ["lake", "canal"], ["Petition: remove water hyacinth in {asset}"], 10),
]
COMPLETION_REMARKS = {
    "waterlogging": ["water pumped out, road clear", "2 pumpsets deployed, water drained", "stagnation cleared by evening",
                     "drained, silt removed at inlet", "cleared. monitoring", "water receded, inlet cleaned"],
    "canal_overflow": ["bank raised with sand bags", "flow normal now, debris removed at bridge", "overflow controlled",
                       "sandbagging done approx 60m", "vent cleared, level coming down"],
    "bund_breach": ["breach plugged with sand bags & gravel", "bund closed, permanent strengthening proposed",
                    "breach closed, watch posted", "closed with sandbags, jcb used"],
    "lake_surplus": ["surplus course cleared, flow smooth", "weir obstruction removed", "monitoring, no damage",
                     "surplus channel cleaned"],
    "sluice_failure": ["shutter repaired & greased", "screw rod replaced", "gate freed, working ok", "rubber seal changed"],
    "building_crack": ["crack sealed, plastering done", "roof waterproofing done", "inspected, minor cracks attended",
                       "temp props given, estimate sent for major repair", "weathering course redone"],
    "wall_collapse": ["debris cleared, barricade put", "wall rebuilt approx 12m", "debris removed, estimate for new wall sent"],
    "encroachment": ["encroachment removed with police help", "notice served & removed", "fencing removed, boundary stones fixed",
                     "huts removed, revenue dept present"],
    "blocked_drain": ["desilting done, debris cleared", "silt removed 3 loads", "blockage cleared, flow ok",
                      "drain mouth cleaned, hyacinth removed", "desilted, silt shifted"],
    "bridge_damage": ["parapet repaired", "railing fixed, potholes patched", "erosion protection w/ boulders done"],
    "bund_weak": ["bund strengthened with earth & sand bags", "seepage point attended", "bund top raised"],
}
GENERIC_REMARKS = ["work completed", "wrk completed", "completed as per instructions", "done. photo attached",
                   "attended & completed", "work under progress, now completed"]
REMARK_SUFFIX = ["", "", "", " - AE", " (JE)", " pls verify", ".", " - photo uploaded"]
VERIFY_OK = ["verified, ok", "site inspected, satisfactory", "photo ok, closed", "verified by DRO", "ok",
             "satisfactory", "field verified by RDO, fine", "checked, work ok", "verified. good work"]
REOPEN_REASONS = ["photo not clear, re-submit", "water still stagnant at site, redo", "debris not removed fully",
                  "only partial work done", "wrong location photo uploaded", "residents complain issue persists",
                  "silt dumped on bank itself, remove"]
REWORK_REMARKS = ["rework done, debris cleared fully", "redone as per Collr remarks", "reattended, photo re-uploaded",
                  "balance work completed"]
VERIFY_AFTER_REWORK = ["rework verified, ok", "re-inspected, satisfactory now", "ok now, closed"]
UNIQ_SUFFIX = [" Door no {n} side.", " Near {n}th lamp post.", " Plot {n} area.", " Ref call {n}.", " Block {n}."]

WORK_TITLE = {
    "desilting": ["Desilting of {asset} (Ch. {a}-{b} km)", "Desilting & removal of water hyacinth in {asset}",
                  "Pre-monsoon desilting of {asset} - {fy}"],
    "bund_strengthening": ["Strengthening of {asset} bund (Ch. {a}-{b} km)", "Raising & strengthening of {asset} - {fy}"],
    "canal_lining": ["Lining of {asset} (Ch. {a}-{b} km)", "Construction of RCC retaining wall along {asset}"],
    "sluice_repair": ["Repairs to shutters of {asset}", "Replacement of gates & hoist - {asset}"],
    "building_construction": ["Construction of additional block at {asset}", "Construction of new wing - {asset} ({fy})"],
    "building_repair": ["Special repairs to {asset}", "Repairs & renovation - {asset} ({fy})", "Roof waterproofing - {asset}"],
    "bridge_repair": ["Repairs & rehabilitation of {asset}", "Strengthening of {asset} parapet & deck"],
    "encroachment_removal": ["Removal of encroachments in {asset}", "Eviction & fencing - {asset} ({fy})"],
    "flood_mitigation": ["Flood mitigation works - {asset}", "Construction of flood regulator & retaining wall - {asset}"],
}

FORBIDDEN_TOKENS = {"severity", "priority", "score", "duplicate", "dup", "cluster", "language", "lang", "week",
                    "month", "quarter", "district", "ward", "delay", "overdue", "total", "count", "created",
                    "updated", "modified", "flag", "rank"}

SCHEMA = {
    "pwd_offices.csv": ["office_id", "office_name", "wing", "officer_name", "designation", "phone", "email",
                        "taluk_codes_covered"],
    "pwd_assets.csv": ["asset_id", "asset_name", "asset_type", "taluk_code", "locality", "latitude", "longitude",
                       "capacity_mcft"],
    "pwd_water_levels.csv": ["asset_id", "reading_date", "storage_mcft", "outflow_cusecs"],
    "pwd_works.csv": ["work_id", "work_title", "work_type", "asset_id", "office_id", "sanctioned_amount_lakh",
                      "expenditure_lakh", "start_date", "target_date", "completion_date", "status",
                      "physical_progress_pct"],
    "pwd_incidents.csv": ["incident_id", "reported_at", "report_source", "taluk_code", "locality", "latitude",
                          "longitude", "asset_id", "incident_type", "description", "people_affected_est",
                          "service_disruption", "access_blocked", "casualty_reported", "status", "resolved_at"],
    "pwd_tasks.csv": ["task_id", "grievance_id", "incident_id", "task_title", "taluk_code", "locality", "latitude",
                      "longitude", "assigned_office_id", "assigned_at", "due_date", "accepted_at", "completed_at",
                      "completion_remarks", "completion_photo_path", "verified_at", "verification_remarks", "status"],
    "pwd_announcements.csv": ["announcement_id", "published_at", "title", "summary", "category",
                              "taluk_codes_affected"],
}


# ----------------------------------------------------------------------------------------------
# Generic helpers
# ----------------------------------------------------------------------------------------------
def R(*parts) -> random.Random:
    """Deterministic RNG for a named stream (string seeds are hashed with SHA-512 by CPython)."""
    return random.Random("|".join(str(p) for p in (SEED,) + parts))


def ist(d: date, h: int, m: int = 0, s: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=IST)


def fuzz(dt: datetime, rng: random.Random) -> datetime:
    """Irregular seconds, never a round hh:00:00."""
    dt = dt.replace(second=rng.randrange(60), microsecond=0)
    if dt.minute == 0 and dt.second == 0:
        dt = dt.replace(second=rng.randint(1, 59))
    return dt


def ts(dt: datetime | None) -> str:
    return dt.isoformat(timespec="seconds") if dt else ""


def ds(d: date | None) -> str:
    return d.isoformat() if d else ""


def time_on(d: date, weights, rng) -> datetime:
    h = rng.choices(range(24), weights=weights)[0]
    return fuzz(ist(d, h, rng.randrange(60)), rng)


def is_holiday(d: date) -> bool:
    return (d.month, d.day) in FIXED_HOLIDAYS or f"{d.month:02d}-{d.day:02d}" in VARIABLE_HOLIDAYS.get(d.year, [])


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and not is_holiday(d)


def next_office_time(t: datetime, rng, p_off: float = 0.06) -> datetime:
    """First plausible office-hours moment (weekday 10:00-18:00) after t; occasionally after hours."""
    if rng.random() < p_off:
        c = t + timedelta(minutes=rng.uniform(15, 180))
        if 7 <= c.hour < 22:
            return fuzz(c, rng)
    for i in range(0, 30):
        dd = t.date() + timedelta(days=i)
        if not is_workday(dd):
            continue
        start = ist(dd, 10, 0) + timedelta(minutes=rng.expovariate(1 / 95.0))
        if i == 0:
            start = max(start, t + timedelta(minutes=rng.uniform(4, 75)))
        if start.date() != dd or start.hour >= 18:
            continue
        return fuzz(start, rng)
    return fuzz(t + timedelta(days=1), rng)


def to_field_hours(t: datetime, rng) -> datetime:
    if 8 <= t.hour < 19:
        return fuzz(t, rng)
    if t.hour < 8:
        return fuzz(t.replace(hour=rng.randint(8, 11), minute=rng.randrange(60)), rng)
    nd = t.date() + timedelta(days=1)
    return fuzz(ist(nd, rng.randint(8, 12), rng.randrange(60)), rng)


def offset(lat, lon, dist_m, bearing_deg):
    b = math.radians(bearing_deg)
    return (lat + dist_m * math.cos(b) / 111320.0,
            lon + dist_m * math.sin(b) / (111320.0 * math.cos(math.radians(lat))))


def jitter(rng, lat, lon, lo, hi):
    return offset(lat, lon, rng.uniform(lo, hi), rng.uniform(0, 360))


def dist_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    lim, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= lim:
            return k
        k += 1


def loguniform(rng, lo, hi):
    return math.exp(rng.uniform(math.log(lo), math.log(hi)))


def human_round(rng, x):
    """Round like a person reporting a crowd: 20, 50, 100, 200 ..."""
    x = max(2.0, x)
    if x < 5:
        return rng.choice([2, 3, 4, 5])
    best = min(NICE_NUMBERS, key=lambda n: abs(math.log(n) - math.log(x)))
    if rng.random() < 0.3:
        best = min(ROUNDER, key=lambda n: abs(math.log(n) - math.log(best)))
    return best


def wchoice(rng, weights: dict):
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


def add_typos(rng, s: str) -> str:
    words = s.split(" ")
    for _ in range(rng.randint(1, 2)):
        r = rng.random()
        if r < 0.45:
            cands = [i for i, w in enumerate(words) for a, _ in ABBREV if w.strip(".,") == a]
            if cands:
                i = rng.choice(cands)
                core = words[i].strip(".,")
                repl = dict(ABBREV)[core]
                words[i] = words[i].replace(core, repl)
                continue
        cands = [i for i, w in enumerate(words) if len(w) > 5 and w.isalpha() and w.isascii()]
        if not cands:
            continue
        i = rng.choice(cands)
        w = words[i]
        j = rng.randint(1, len(w) - 2)
        words[i] = (w[:j] + w[j + 1:]) if r < 0.75 else (w[:j] + w[j + 1] + w[j] + w[j + 2:])
    return " ".join(words)


def clean_text(s: str) -> str:
    s = " ".join(s.split())
    for a, b in ((" .", "."), (" ,", ","), ("..", "."), (". .", "."), ("( ", "("), (" )", ")")):
        s = s.replace(a, b)
    return s.strip()


def short_asset(name: str) -> str:
    return name.split(" - ")[0]


# ----------------------------------------------------------------------------------------------
# World building (static tables)
# ----------------------------------------------------------------------------------------------
class World:
    pass


def load_taluks(path):
    if not path:
        return [dict(code=c, name=n, lat=la, lon=lo) for c, n, la, lo in DEFAULT_TALUKS]
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append(dict(code=r["taluk_code"].strip(), name=r["taluk_name"].strip(),
                             lat=float(r["latitude"]), lon=float(r["longitude"])))
    if not rows:
        sys.exit("taluk master is empty")
    return rows


def build_static(W):
    W.taluk_by_code = {t["code"]: t for t in W.taluks}
    W.codes = [t["code"] for t in W.taluks]
    # localities
    W.loc = {}
    for t in W.taluks:
        names = LOCALITIES.get(t["code"]) or [t["name"]]
        lst = []
        for i, n in enumerate(names):
            rng = R("locality", t["code"], n)
            la, lo = jitter(rng, t["lat"], t["lon"], 0, 250) if i == 0 else jitter(rng, t["lat"], t["lon"], 200, 900)
            lst.append((n, la, lo))
        W.loc[t["code"]] = lst
    W.loc_xy = {(c, n): (la, lo) for c, lst in W.loc.items() for n, la, lo in lst}
    # neighbours by centroid distance
    W.neigh = {}
    for t in W.taluks:
        others = sorted((dist_m(t["lat"], t["lon"], o["lat"], o["lon"]), o["code"]) for o in W.taluks if o is not t)
        W.neigh[t["code"]] = [c for _, c in others]
    # assets
    W.assets = []
    n = 0
    for name, atype, tnum, locality, cap in ASSET_DEFS:
        code = f"TLK{tnum:02d}"
        if code not in W.taluk_by_code:
            continue
        n += 1
        if (code, locality) in W.loc_xy:
            la, lo = W.loc_xy[(code, locality)]
        else:
            t = W.taluk_by_code[code]
            la, lo = jitter(R("assetloc", name), t["lat"], t["lon"], 200, 900)
        la, lo = jitter(R("asset", name), la, lo, 80, 300)
        W.assets.append(dict(asset_id=f"PWD-AST-{n:04d}", asset_name=name, asset_type=atype, taluk_code=code,
                             locality=locality, lat=la, lon=lo, capacity=cap, downstream=[code] + W.neigh[code][:1]))
    for name, locality, cap, la, lo, downstream in RESERVOIR_DEFS:
        n += 1
        W.assets.append(dict(asset_id=f"PWD-AST-{n:04d}", asset_name=name, asset_type="lake", taluk_code="",
                             locality=locality, lat=la, lon=lo, capacity=cap,
                             downstream=[c for c in downstream if c in W.taluk_by_code]))
    W.asset_by_id = {a["asset_id"]: a for a in W.assets}
    W.urban_assets = [a for a in W.assets if a["taluk_code"]]
    W.assets_by_taluk_type = defaultdict(list)
    for a in W.urban_assets:
        W.assets_by_taluk_type[(a["taluk_code"], a["asset_type"])].append(a)
    W.lakes = [a for a in W.assets if a["capacity"]]
    # offices
    W.offices = []
    rng = R("offices")
    used_names = set()
    for i, (oname, wing, desig, tnums, role, email) in enumerate(OFFICE_DEFS, start=1):
        codes = list(W.codes) if tnums == ALL else [f"TLK{x:02d}" for x in tnums if f"TLK{x:02d}" in W.taluk_by_code]
        while True:
            person = f"{rng.choice(INITIALS)} {rng.choice(FIRST_NAMES)}"
            if person not in used_names:
                used_names.add(person)
                break
        W.offices.append(dict(office_id=f"PWD-OFF-{i:03d}", office_name=oname, wing=wing, officer_name=person,
                              designation=desig, phone=f"+91-44-0000-{1000 + i * 7:04d}",
                              email=f"{email}@example.tn.gov.in", codes=codes, role=role))
    W.office_by_id = {o["office_id"]: o for o in W.offices}


def pick_office(rng, W, wing, taluk, itype=None, asset=None):
    roles = {"division", "subdivision"}
    if wing == "Water Resources":
        if itype in ("waterlogging", "canal_overflow", "bund_breach", "lake_surplus"):
            roles.add("flood_cell")
        if itype == "encroachment":
            roles.add("encroachment_cell")
    if wing == "Buildings" and asset:
        nm = asset["asset_name"]
        if any(k in nm for k in ("Hospital", "Institute", "Health")):
            roles.add("medical")
        if any(k in nm for k in ("School", "College", "Polytechnic")):
            roles.add("education")
    cands = [o for o in W.offices if o["wing"] == wing and o["role"] in roles and taluk in o["codes"]]
    if not cands:
        cands = [o for o in W.offices if o["wing"] == wing and taluk in o["codes"] and o["role"] != "hq"]
    weights = [3 if o["role"] in ("medical", "education", "encroachment_cell") else
               (2 if o["role"] == "subdivision" else 1.5) for o in cands]
    return rng.choices(cands, weights=weights)[0]


# ----------------------------------------------------------------------------------------------
# Rain, events, lakes
# ----------------------------------------------------------------------------------------------
def build_events(W, first: date, last: date):
    events = {}
    b0 = max(0, (first - ID_ANCHOR).days // EVENT_BLOCK_DAYS - 1)
    b1 = (last - ID_ANCHOR).days // EVENT_BLOCK_DAYS + 1
    breach_taluks = sorted({a["taluk_code"] for a in W.urban_assets if a["asset_type"] in ("bund", "lake")})
    for b in range(b0, b1 + 1):
        rng = R("event", b)
        d = ID_ANCHOR + timedelta(days=b * EVENT_BLOCK_DAYS + rng.randint(3, 26))
        m = d.month
        if m >= 6:
            kind = "heavy_rain" if rng.random() < 0.78 else "bund_breach"
        else:
            kind = "heavy_rain" if rng.random() < 0.5 else "bund_breach"
        breach = kind == "bund_breach" or (m in NE_MONSOON and rng.random() < 0.35)
        pool = breach_taluks if (breach and breach_taluks) else W.codes
        center = rng.choice(pool)
        cluster = [center] + W.neigh[center][:rng.randint(2, 5)]
        ev = dict(date=d, kind=kind, breach=breach, center=center, cluster=cluster,
                  peak_hour=rng.choice([1, 3, 4, 5, 14, 16, 17, 19, 21, 23]), breach_asset=None)
        if breach:
            cands = (W.assets_by_taluk_type[(center, "bund")] or W.assets_by_taluk_type[(center, "lake")])
            ev["breach_asset"] = rng.choice(cands)["asset_id"] if cands else None
            if not ev["breach_asset"]:
                ev["breach"] = False
                ev["kind"] = "heavy_rain"
        events[d] = ev
    return events


def simulate_rain(W, first: date, last: date):
    rain = {}
    d = first
    while d <= last:
        rng = R("rain", d.isoformat())
        p, mean, cov = MONTH_RAIN[d.month]
        wet = rng.random() < p
        base = rng.expovariate(1.0 / mean) if wet else 0.0
        day = {}
        for c in W.codes:
            u1, u2, u3 = rng.random(), rng.lognormvariate(0, 0.55), rng.random()
            if wet and u1 < cov:
                v = base * u2
            else:
                v = u3 * 1.2 if u3 < 0.15 else 0.0
            day[c] = v
        ev = W.events.get(d)
        if ev and ev["kind"] == "heavy_rain":
            for c in W.codes:
                if c == ev["center"]:
                    day[c] = rng.uniform(110, 230)
                elif c in ev["cluster"]:
                    day[c] = rng.uniform(55, 150)
                else:
                    day[c] = rng.uniform(15, 50) if d.month in NE_MONSOON else rng.uniform(0, 28)
        elif ev and ev["breach"]:  # dry-season breach: a little local rain at most
            day[ev["center"]] = max(day[ev["center"]], rng.uniform(0, 12))
        rain[d] = {c: (round(v, 1) if v >= 0.1 else 0.0) for c, v in day.items()}
        d += timedelta(days=1)
    return rain


def apply_rain_csv(W, path):
    covered = set()
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                d = date.fromisoformat(r["date"].strip()[:10])
                c = r["taluk_code"].strip()
                v = float(r["rainfall_mm"])
            except (KeyError, ValueError):
                continue
            if d in W.rain and c in W.rain[d]:
                W.rain[d][c] = round(max(0.0, v), 1)
                covered.add(d)
    # lattice heavy-rain events on days the CSV says were dry become bund-breach events
    for d, ev in W.events.items():
        if d in covered and ev["kind"] == "heavy_rain":
            mx = max(W.rain[d].values())
            if mx < 40:
                cands = [a for a in W.urban_assets if a["asset_type"] in ("bund", "lake")]
                a = R("csvbreach", d).choice(cands)
                ev.update(kind="bund_breach", breach=True, breach_asset=a["asset_id"], center=a["taluk_code"],
                          cluster=[a["taluk_code"]] + W.neigh[a["taluk_code"]][:3])
            else:
                top = max(W.rain[d], key=W.rain[d].get)
                ev.update(center=top, cluster=[c for c, v in W.rain[d].items() if v >= 0.45 * mx])
    return covered


def simulate_lakes(W, first: date, last: date):
    """Daily water balance for every lake/reservoir. Returns {asset_id: {date: (storage, outflow, surplus)}}."""
    out = {}
    for a in W.lakes:
        cap = a["capacity"]
        reservoir = not a["taluk_code"]
        prm = R("lakeparams", a["asset_id"])
        k = prm.uniform(0.0018, 0.0026) if reservoir else prm.uniform(0.0027, 0.0036)
        supply_rate = prm.uniform(0.0022, 0.0034) if reservoir else 0.0
        s = cap * (0.78 if reservoir else 0.70)
        w = 0.0
        series = {}
        d = first
        while d <= last:
            day = W.rain[d]
            r = (sum(day.values()) / len(day)) * 1.05 if reservoir else day[a["taluk_code"]]
            w = 0.8 * w + r
            eff = max(0.0, r - 12.0)
            inflow = cap * k * eff * (0.35 + min(1.0, w / 110.0))
            base_in = 0.0 if reservoir else cap * 0.0005          # urban runoff / sewage
            dry = d.month in (3, 4, 5, 6)
            evap_rate = (0.0022 if dry else 0.0012) if reservoir else (0.005 if dry else 0.003)  # evap + seepage
            s = s + inflow + base_in - s * evap_rate
            supply = 0.0
            if reservoir and s > 0.15 * cap:
                supply = cap * supply_rate * min(1.0, s / (0.5 * cap))
                s -= supply
            spill = 0.0
            thr = (0.92 if d.month in NE_MONSOON else 0.97) if reservoir else 0.985
            if s > cap * thr:
                spill = s - cap * thr
                s = cap * thr
            s = max(s, cap * 0.02)
            outflow = int(round((supply + spill) / MCFT_PER_CUSEC_DAY))
            series[d] = (round(s, 1) if reservoir else round(s, 2), outflow, spill > 0.0005 * cap)
            d += timedelta(days=1)
        out[a["asset_id"]] = series
    return out


def surplus_started(W, asset_id, d, quiet_days=10):
    """True if the water body surplusses on d but did not in the previous `quiet_days` days."""
    ser = W.levels[asset_id]
    if not ser.get(d, (0, 0, False))[2]:
        return False
    return not any(ser.get(d - timedelta(days=i), (0, 0, False))[2] for i in range(1, quiet_days + 1))


# ----------------------------------------------------------------------------------------------
# Descriptions
# ----------------------------------------------------------------------------------------------
def pick_lang(rng, source):
    ta, tg = LANG_BY_SOURCE[source]
    u = rng.random()
    return "ta" if u < ta else ("tg" if u < ta + tg else "en")


def describe(rng, itype, lang, locality, asset, source, rain_mm, exclude=()):
    table = TPL[lang][itype]
    idxs = [i for i in range(len(table)) if (lang, itype, i) not in exclude] or list(range(len(table)))
    i = rng.choice(idxs)
    if asset:
        aname = asset["asset_name"] if (lang == "en" and rng.random() < 0.55) else short_asset(asset["asset_name"])
    else:
        aname = GENERIC_ASSET[lang][itype]
    lvl = 0 if rain_mm < 20 else (1 if rain_mm < 45 else (2 if rain_mm < 80 else 3))
    depth = DEPTH[lang][min(5, max(0, lvl + rng.randint(-1, 2)))]
    ctx = dict(loc=locality, asset=aname, street=rng.choice(STREETS), lm=rng.choice(LANDMARKS[lang]),
               depth=depth, tp=rng.choice(TIME_PHRASES[lang]),
               plea=rng.choice([p for p in PLEAS[lang] if itype in EMERGENCY_TYPES or "pump" not in p.lower()]),
               n10=rng.choice([5, 8, 10, 12, 15, 20, 25, 30]))
    ctx["tp_l"] = ctx["tp"].rstrip(".").lower() + ("," if ctx["tp"] else "")
    s = clean_text(table[i].format(**ctx))
    if lang == "en":
        if rng.random() < 0.45:
            s = rng.choice(SOURCE_PREFIX[source]) + (s[0].lower() + s[1:] if s[:1].isupper() and rng.random() < 0.5 else s)
        if rng.random() < 0.15:
            s = add_typos(rng, s)
        if rng.random() < 0.08:
            s = s.lower()
    elif lang == "tg":
        if rng.random() < 0.5:
            s = s.lower()
        if rng.random() < 0.1:
            s = add_typos(rng, s)
    s = clean_text(s)
    s = s[0].upper() + s[1:] if (lang == "en" and s[:1].islower() and rng.random() < 0.7) else s
    return s, (lang, itype, i)


# ----------------------------------------------------------------------------------------------
# Incidents + tasks per day
# ----------------------------------------------------------------------------------------------
def choose_asset(rng, W, itype, taluk=None, force_optional=None):
    """Returns (taluk, asset|None)."""
    types = ASSET_TYPES_FOR[itype]
    p_blank = ASSET_OPTIONAL.get(itype, 0.0) if force_optional is None else force_optional
    if taluk:
        cands = [a for t in types for a in W.assets_by_taluk_type[(taluk, t)]]
        if cands and rng.random() >= p_blank:
            return taluk, rng.choice(cands)
        return taluk, None
    cands = [a for a in W.urban_assets if a["asset_type"] in types]
    if rng.random() < p_blank or not cands:
        return rng.choice(W.codes), None
    a = rng.choice(cands)
    return a["taluk_code"], a


def people_est(rng, itype, rain_mm, blank_boost=0.0):
    if rng.random() < PEOPLE_BLANK.get(itype, 0.18) + blank_boost:
        return None, None
    lo, hi = PEOPLE_RANGE[itype]
    raw = loguniform(rng, lo, hi)
    if itype in EMERGENCY_TYPES:
        raw *= 1 + min(2.0, rain_mm / 100.0)
    return human_round(rng, raw), raw


def disruption(rng, itype, rain_mm):
    heavy = rain_mm > 70
    table = {
        "waterlogging": (0.25, 0.5, 0.25) if heavy else (0.45, 0.45, 0.10),
        "canal_overflow": (0.25, 0.5, 0.25) if heavy else (0.45, 0.45, 0.10),
        "bund_breach": (0.05, 0.45, 0.5), "lake_surplus": (0.4, 0.45, 0.15), "sluice_failure": (0.6, 0.35, 0.05),
        "building_crack": (0.6, 0.35, 0.05), "wall_collapse": (0.35, 0.45, 0.2), "encroachment": (0.85, 0.14, 0.01),
        "blocked_drain": (0.45, 0.45, 0.1), "bridge_damage": (0.35, 0.45, 0.2),
    }[itype]
    return rng.choices(["none", "partial", "full"], weights=table)[0]


ACCESS_P = {"waterlogging": 0.35, "canal_overflow": 0.35, "bund_breach": 0.5, "lake_surplus": 0.25,
            "sluice_failure": 0.03, "building_crack": 0.05, "wall_collapse": 0.5, "encroachment": 0.03,
            "blocked_drain": 0.1, "bridge_damage": 0.4}
CASUALTY_P = {"waterlogging": 0.004, "canal_overflow": 0.006, "bund_breach": 0.04, "lake_surplus": 0.004,
              "sluice_failure": 0.0, "building_crack": 0.004, "wall_collapse": 0.06, "encroachment": 0.0,
              "blocked_drain": 0.0, "bridge_damage": 0.02}


def source_for(rng, itype, d, exclude=()):
    w = dict(SOURCE_W[itype])
    if not is_workday(d):
        w["collector_office"] *= 0.3
    for e in exclude:
        w.pop(e, None)
    if not w:
        w = {s: 1 for s in SOURCES}
    return wchoice(rng, w)


def report_time(rng, d, source):
    wt = {"control_room": CONTROL_W, "media": MEDIA_W,
          "collector_office": OFFICE_W if is_workday(d) else FIELD_W}.get(source, FIELD_W)
    return time_on(d, wt, rng)


def new_incident(rng, W, d, itype, taluk, asset, source=None, t=None, rain_mm=0.0, event=False):
    source = source or source_for(rng, itype, d)
    t = t or report_time(rng, d, source)
    if asset:
        locality = asset["locality"]
        lat, lon = jitter(rng, asset["lat"], asset["lon"], 30, 300)
    else:
        locality, la, lo = rng.choice(W.loc[taluk])
        lat, lon = jitter(rng, la, lo, 30, 450)
    lang = pick_lang(rng, source)
    desc, tkey = describe(rng, itype, lang, locality, asset, source, rain_mm)
    ppl, raw = people_est(rng, itype, rain_mm)
    acc = 1 if rng.random() < ACCESS_P[itype] * (1.4 if rain_mm > 70 else 1.0) else 0
    cas = 1 if rng.random() < CASUALTY_P[itype] * (2.5 if event else 1.0) else 0
    return dict(_t=t, _day=d, _rain=rain_mm, _event=event, _tkeys=[tkey], _raw=raw, _primary=True, _group=None,
                report_source=source, taluk_code=taluk, locality=locality, lat=lat, lon=lon,
                asset_id=asset["asset_id"] if asset else "", _asset=asset, incident_type=itype, description=desc,
                people_affected_est=ppl, service_disruption=disruption(rng, itype, rain_mm),
                access_blocked=acc, casualty_reported=cas)


def make_dup(rng, W, prim, used_sources, day_end):
    src = source_for(rng, prim["incident_type"], prim["_day"], exclude=used_sources)
    gap = rng.choice([rng.uniform(4, 40), rng.uniform(30, 180), rng.uniform(120, 420)])
    t = prim["_t"] + timedelta(minutes=gap)
    if t > day_end:
        return None
    t = fuzz(t, rng)
    if t <= prim["_t"]:
        t = prim["_t"] + timedelta(seconds=rng.randint(61, 400))
    lat, lon = offset(prim["lat"], prim["lon"], rng.uniform(50, 300), rng.uniform(0, 360))
    locality = prim["locality"]
    if rng.random() < 0.2:
        near = sorted(W.loc[prim["taluk_code"]], key=lambda x: dist_m(lat, lon, x[1], x[2]))
        locality = near[0][0]
    lang = pick_lang(rng, src)
    if rng.random() < 0.55:
        lang = rng.choice([x for x in ("en", "ta", "tg") if x != prim["_tkeys"][0][0]])
    asset = prim["_asset"] if (prim["_asset"] and rng.random() < 0.85) else None
    desc, tkey = describe(rng, prim["incident_type"], lang, locality, asset or prim["_asset"], src, prim["_rain"],
                          exclude=prim["_tkeys"])
    prim["_tkeys"].append(tkey)
    if prim["_raw"] is None or rng.random() < 0.25:
        ppl = None if rng.random() < 0.6 else human_round(rng, loguniform(rng, *PEOPLE_RANGE[prim["incident_type"]]))
    else:
        ppl = human_round(rng, prim["_raw"] * rng.uniform(0.4, 2.2))
    sd = prim["service_disruption"]
    if rng.random() < 0.3:
        sd = rng.choice(["none", "partial", "full"])
    acc = prim["access_blocked"] if rng.random() < 0.85 else 1 - prim["access_blocked"]
    cas = prim["casualty_reported"] if rng.random() < 0.8 else 0
    return dict(_t=t, _day=prim["_day"], _rain=prim["_rain"], _event=prim["_event"], _tkeys=[tkey], _raw=None,
                _primary=False, _group=prim, report_source=src, taluk_code=prim["taluk_code"], locality=locality,
                lat=lat, lon=lon, asset_id=asset["asset_id"] if asset else "", _asset=asset,
                incident_type=prim["incident_type"], description=desc, people_affected_est=ppl,
                service_disruption=sd, access_blocked=acc, casualty_reported=cas)


def task_probability(inc):
    p = 0.16
    p += 0.18 if inc["access_blocked"] else 0.0
    p += 0.35 if inc["casualty_reported"] else 0.0
    p += {"bund_breach": 0.45, "wall_collapse": 0.15, "bridge_damage": 0.15, "encroachment": 0.12,
          "building_crack": 0.12, "canal_overflow": 0.08}.get(inc["incident_type"], 0.0)
    p += 0.35 if inc["report_source"] == "collector_office" else 0.0
    return min(0.95, p)


def task_timeline(rng, origin: datetime, itype: str, emergency: bool):
    """Full lifecycle drawn up-front; NOW later decides how much of it has happened."""
    tl = {}
    if emergency:
        assigned = next_office_time(origin + timedelta(hours=rng.uniform(0.3, 4)), rng, p_off=0.55)
    else:
        assigned = next_office_time(origin + timedelta(hours=rng.uniform(2, 60)), rng)
    tl["assigned"] = assigned
    lo, hi = TASK_DUE_DAYS[itype]
    tl["due"] = assigned.date() + timedelta(days=rng.randint(lo, hi))
    u = rng.random()
    if emergency:
        acc_h = rng.uniform(0.3, 6) if u < 0.8 else rng.uniform(20, 70)
        acc = assigned + timedelta(hours=acc_h)
        acc = fuzz(acc, rng) if rng.random() < 0.6 else next_office_time(acc, rng, p_off=0.2)
    else:
        acc_h = rng.uniform(1, 20) if u < 0.38 else (rng.uniform(24, 120) if u < 0.60 else rng.uniform(7 * 24, 40 * 24))
        acc = next_office_time(assigned + timedelta(hours=acc_h), rng, p_off=0.12)
    tl["accepted"] = acc
    tl["start"] = acc + timedelta(hours=rng.uniform(0.5, 30))
    work_d = TASK_WORK_MEDIAN_D[itype] * rng.lognormvariate(0, 0.8)
    if rng.random() < 0.22:
        work_d += rng.uniform(10, 40)  # "pending material" stalls
    done = tl["start"] + timedelta(days=work_d)
    done = max(done, assigned + timedelta(hours=rng.uniform(48, 72)))  # photo upload & closure lag
    tl["completed"] = to_field_hours(done, rng)
    u = rng.random()
    vd = rng.uniform(0.5, 5) if u < 0.30 else (rng.uniform(5, 18) if u < 0.66 else rng.uniform(18, 42))
    tl["review"] = next_office_time(tl["completed"] + timedelta(days=vd), rng, p_off=0.03)
    tl["rejected"] = rng.random() < 0.20
    tl["rework"] = to_field_hours(tl["review"] + timedelta(days=rng.uniform(5, 20)), rng)
    tl["final"] = next_office_time(tl["rework"] + timedelta(days=rng.uniform(1, 9)), rng, p_off=0.03)
    rk = itype if itype in COMPLETION_REMARKS else "blocked_drain"
    base = rng.choice(COMPLETION_REMARKS[rk]) if rng.random() < 0.8 else rng.choice(GENERIC_REMARKS)
    tl["remarks"] = base + rng.choice(REMARK_SUFFIX)
    tl["verify_ok"] = rng.choice(VERIFY_OK)
    tl["reject_reason"] = rng.choice(REOPEN_REASONS)
    tl["rework_remarks"] = rng.choice(REWORK_REMARKS)
    tl["verify_after_rework"] = rng.choice(VERIFY_AFTER_REWORK)
    return tl


def incident_lifecycle(rng, inc):
    itype = inc["incident_type"]
    emergency = itype in EMERGENCY_TYPES
    resp_h = rng.uniform(1.5, 10) if emergency else rng.uniform(6, 96)
    inc["_ip"] = inc["_t"] + timedelta(hours=resp_h)
    if rng.random() < 0.06:  # long pending
        inc["_res"] = None
        if rng.random() < 0.4:
            inc["_ip"] = None
        return
    dur_d = RESOLVE_MEDIAN_D[itype] * rng.lognormvariate(0, 0.7)
    inc["_res"] = inc["_t"] + timedelta(days=max(dur_d, resp_h / 24 + 0.1))
    inc["_res"] = fuzz(inc["_res"], rng)


def gen_day(W, d: date):
    """All incidents + tasks whose origin is day d (full lifecycles, not yet NOW-filtered)."""
    rng = R("inc", d.isoformat())
    rain = W.rain[d]
    ev = W.events.get(d)
    mean_rain = sum(rain.values()) / len(rain)
    day_end = ist(d, 23, 59, 50)
    prims = []
    # 1. background incidents
    mult = 1.0 if is_workday(d) else (0.65 if is_holiday(d) else 0.72)
    tw = dict(BASE_TYPE_W)
    if mean_rain > 5:
        tw["waterlogging"] *= 3
        tw["blocked_drain"] *= 1.5
        tw["canal_overflow"] *= 2
        tw["wall_collapse"] *= 1.5
    for _ in range(poisson(rng, BASE_RATE[d.month] * mult)):
        itype = wchoice(rng, tw)
        taluk, asset = choose_asset(rng, W, itype)
        prims.append(new_incident(rng, W, d, itype, taluk, asset, rain_mm=rain[taluk]))
    # 2. rain-driven incidents
    peak = ist(d, ev["peak_hour"] if ev else rng.randint(0, 23), rng.randrange(60))
    for c in W.codes:
        r = rain[c]
        lam = max(0.0, r - 18.0) / 22.0 * (1.3 if r > 60 else 1.0)
        for _ in range(poisson(rng, lam)):
            w = dict(RAIN_TYPE_W)
            if r < 80:
                w.pop("bund_breach")
            if r < 60:
                w.pop("bridge_damage")
            itype = wchoice(rng, w)
            taluk, asset = choose_asset(rng, W, itype, taluk=c)
            src = source_for(rng, itype, d)
            if rng.random() < 0.5:
                t = peak + timedelta(minutes=rng.gauss(90, 150))
                t = fuzz(min(max(t, ist(d, 0, 1)), day_end), rng)
            else:
                t = report_time(rng, d, src)
            prims.append(new_incident(rng, W, d, itype, taluk, asset, source=src, t=t, rain_mm=r,
                                      event=bool(ev)))
    # 3. lake surplus reports
    prev = d - timedelta(days=1)
    for a in W.lakes:
        if not a["taluk_code"]:
            continue
        today_s = W.levels[a["asset_id"]].get(d)
        if not today_s or not today_s[2]:
            continue
        first = surplus_started(W, a["asset_id"], d)
        if rng.random() < (0.85 if first else 0.12):
            t = time_on(d, FIELD_W, rng)
            prims.append(new_incident(rng, W, d, "lake_surplus", a["taluk_code"], a, t=t,
                                      rain_mm=rain[a["taluk_code"]], event=bool(ev)))
    # 4. event specifics: bund breach burst
    forced_groups = {}
    if ev and ev["breach"] and ev["breach_asset"]:
        a = W.asset_by_id[ev["breach_asset"]]
        tb = ist(d, rng.choice([2, 3, 4, 5, 6, 18, 20, 22]) if ev["kind"] == "bund_breach" else min(23, ev["peak_hour"] + 2),
                 rng.randrange(60))
        tb = fuzz(tb, rng)
        prim = new_incident(rng, W, d, "bund_breach", a["taluk_code"], a, source=rng.choice(["field_staff", "control_room"]),
                            t=tb, rain_mm=max(rain[a["taluk_code"]], 60.0), event=True)
        prims.append(prim)
        forced_groups[id(prim)] = rng.choice([3, 4])
        for _ in range(rng.randint(6, 12)):
            c = a["taluk_code"] if rng.random() < 0.7 else rng.choice(W.neigh[a["taluk_code"]][:2])
            t = tb + timedelta(minutes=rng.uniform(30, 600))
            if t > day_end:
                continue
            taluk, asset = choose_asset(rng, W, "waterlogging", taluk=c)
            prims.append(new_incident(rng, W, d, "waterlogging", taluk, asset, t=fuzz(t, rng),
                                      rain_mm=max(rain[c], 45.0), event=True))
    # cap per day so IDs stay inside the day block
    if len(prims) > 44:
        keep_forced = [p for p in prims if id(p) in forced_groups]
        others = [p for p in prims if id(p) not in forced_groups]
        prims = keep_forced + rng.sample(others, 44 - len(keep_forced))
        prims.sort(key=lambda p: p["_t"])
    # 5. near-duplicate reports
    allinc = list(prims)
    p_group = 0.10 if ev else 0.045
    for prim in prims:
        size = forced_groups.get(id(prim))
        if size is None:
            if rng.random() >= p_group:
                continue
            size = rng.choices([2, 3, 4], weights=[55, 30, 15])[0]
        used = {prim["report_source"]}
        for _ in range(size - 1):
            if len(allinc) >= INC_BLOCK - 1:
                break
            dup = make_dup(rng, W, prim, used, day_end)
            if dup:
                used.add(dup["report_source"])
                allinc.append(dup)
    # 6. lifecycles + Collector tasks
    tasks = []
    for inc in prims:
        incident_lifecycle(rng, inc)
        if rng.random() < task_probability(inc):
            itype = inc["incident_type"]
            asset = inc["_asset"]
            wing = "Buildings" if (asset and asset["asset_type"] == "govt_building") else "Water Resources"
            office = pick_office(rng, W, wing, inc["taluk_code"], itype, asset)
            tl = task_timeline(rng, inc["_t"], itype, itype in EMERGENCY_TYPES)
            aname = asset["asset_name"] if asset else GENERIC_ASSET["en"][itype]
            title = rng.choice(TASK_TITLE[itype]).format(asset=aname, loc=inc["locality"], street=rng.choice(STREETS))
            lat, lon = offset(inc["lat"], inc["lon"], rng.uniform(0, 25), rng.uniform(0, 360))
            tasks.append(dict(_tl=tl, _inc=inc, grievance_id="", task_title=title, taluk_code=inc["taluk_code"],
                              locality=inc["locality"], lat=lat, lon=lon, assigned_office_id=office["office_id"]))
            # incident closes when the task work is done
            inc["_ip"] = min(x for x in (inc["_ip"], tl["accepted"]) if x) if inc["_ip"] else tl["accepted"]
            inc["_res"] = fuzz(tl["completed"] + timedelta(hours=rng.uniform(0.2, 5)), rng)
    for inc in allinc:
        if inc["_primary"]:
            continue
        pr = inc["_group"]
        inc["_ip"] = max(inc["_t"] + timedelta(minutes=rng.uniform(10, 90)), pr["_ip"]) if pr["_ip"] else None
        if pr["_res"]:
            inc["_res"] = fuzz(max(pr["_res"], inc["_t"] + timedelta(hours=1)) + timedelta(hours=rng.uniform(0.1, 4)), rng)
        else:
            inc["_res"] = None
    # 7. grievance-origin tasks (CM cell / petition day)
    seqs = set()
    for _ in range(poisson(rng, 0.95 if is_workday(d) else 0.35)):
        itype, atypes, titles, _w = rng.choices(GRIEVANCE_KINDS, weights=[g[3] for g in GRIEVANCE_KINDS])[0]
        cands = [a for a in W.urban_assets if a["asset_type"] in atypes]
        asset = rng.choice(cands)
        wing = "Buildings" if asset["asset_type"] == "govt_building" else "Water Resources"
        office = pick_office(rng, W, wing, asset["taluk_code"], itype if itype != "bund_weak" else None, asset)
        gdate = d - timedelta(days=rng.choice([0, 0, 1, 1, 2, 3, 5]))
        seq = rng.randint(100, 99999)
        while seq in seqs:
            seq = rng.randint(100, 99999)
        seqs.add(seq)
        origin = time_on(d, OFFICE_W if is_workday(d) else FIELD_W, rng)
        tl = task_timeline(rng, origin, itype, False)
        lat, lon = jitter(rng, asset["lat"], asset["lon"], 30, 300)
        tasks.append(dict(_tl=tl, _inc=None, grievance_id=GRIEVANCE_ID_FORMAT.format(date=gdate, seq=seq),
                          task_title=rng.choice(titles).format(asset=asset["asset_name"], loc=asset["locality"]),
                          taluk_code=asset["taluk_code"], locality=asset["locality"], lat=lat, lon=lon,
                          assigned_office_id=office["office_id"]))
    # IDs (stable, day-blocked)
    allinc.sort(key=lambda x: x["_t"])
    base = (d - ID_ANCHOR).days
    for k, inc in enumerate(allinc):
        inc["incident_id"] = f"PWD-INC-{base * INC_BLOCK + k + 1:06d}"
    tasks.sort(key=lambda x: x["_tl"]["assigned"])
    tasks = tasks[:TSK_BLOCK]
    for k, t in enumerate(tasks):
        t["task_id"] = f"PWD-TSK-{base * TSK_BLOCK + k + 1:05d}"
    return allinc, tasks


def incident_status(inc, now):
    if inc["_res"] and inc["_res"] <= now:
        return "resolved"
    if inc["_ip"] and inc["_ip"] <= now:
        return "in_progress"
    return "open"


def task_row(t, now):
    tl = t["_tl"]
    row = dict(task_id=t["task_id"], grievance_id=t["grievance_id"],
               incident_id=t["_inc"]["incident_id"] if t["_inc"] else "", task_title=t["task_title"],
               taluk_code=t["taluk_code"], locality=t["locality"], latitude=f"{t['lat']:.6f}",
               longitude=f"{t['lon']:.6f}", assigned_office_id=t["assigned_office_id"], assigned_at=ts(tl["assigned"]),
               due_date=ds(tl["due"]), accepted_at="", completed_at="", completion_remarks="",
               completion_photo_path="", verified_at="", verification_remarks="", status="assigned")
    if tl["accepted"] > now:
        return row
    row["accepted_at"] = ts(tl["accepted"])
    row["status"] = "accepted" if tl["start"] > now else "in_progress"
    if tl["completed"] > now:
        return row
    photo = f"uploads/pwd/{t['task_id']}.jpg"
    row.update(completed_at=ts(tl["completed"]), completion_remarks=tl["remarks"], completion_photo_path=photo,
               status="completed_pending_verification")
    if tl["review"] > now:
        return row
    if not tl["rejected"]:
        row.update(verified_at=ts(tl["review"]), verification_remarks=tl["verify_ok"], status="verified")
        return row
    if tl["rework"] > now:
        row.update(verified_at=ts(tl["review"]), verification_remarks=tl["reject_reason"], status="reopened")
        return row
    row.update(completed_at=ts(tl["rework"]), completion_remarks=tl["rework_remarks"])
    if tl["final"] > now:
        return row
    row.update(verified_at=ts(tl["final"]), verification_remarks=tl["verify_after_rework"], status="verified")
    return row


# ----------------------------------------------------------------------------------------------
# Works
# ----------------------------------------------------------------------------------------------
def fy_label(d: date):
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def build_works_catalog(W, last: date):
    works = []
    y, m = WORKS_CATALOG_START.year, WORKS_CATALOG_START.month
    n = 0
    while (y, m) <= (last.year, last.month):
        rng = R("works", y, m)
        season = {8: 2.0, 9: 2.2, 10: 1.6, 7: 1.3, 6: 1.0, 11: 0.5, 12: 0.5}.get(m, 0.8)
        cnt = poisson(rng, 19 * season)
        for _ in range(cnt):
            tw = dict(WORK_TYPE_BASE_W)
            if m in (7, 8, 9, 10):
                tw["desilting"] *= 2.5
                tw["bund_strengthening"] *= 2.0
            if m in (11, 12):
                tw["desilting"] *= 0.3
            wtype = wchoice(rng, tw)
            cands = [a for a in W.assets if a["asset_type"] in WORK_ASSET_TYPES[wtype]]
            if wtype not in ("bund_strengthening", "flood_mitigation", "sluice_repair"):
                cands = [a for a in cands if a["taluk_code"]]
            a = rng.choice(cands)
            n += 1
            start = date(y, m, 1) + timedelta(days=rng.randint(0, 27))
            lo, hi = WORK_DUR[wtype]
            planned = rng.randint(lo, hi)
            lag = rng.random() < 0.16
            factor = rng.uniform(1.25, 2.0) if lag else rng.uniform(0.82, 1.02)
            mobilize = rng.randint(3, 12) if rng.random() < 0.3 else 0
            hold = rng.random() < 0.07
            hold_len = rng.randint(20, 90)
            alo, ahi = WORK_AMOUNT[wtype]
            amount = round(loguniform(rng, alo, ahi), 2)
            if a["taluk_code"]:
                wing = "Buildings" if a["asset_type"] == "govt_building" else "Water Resources"
                office = pick_office(rng, W, wing, a["taluk_code"])
            else:
                office = next(o for o in W.offices if o["role"] == "reservoir")
            ca, cb = sorted([round(rng.uniform(0, 3.5), 2), round(rng.uniform(0, 3.5), 2)])
            if cb - ca < 0.3:
                cb = round(ca + rng.uniform(0.3, 1.5), 2)
            title = rng.choice(WORK_TITLE[wtype]).format(asset=a["asset_name"], a=f"{ca:.2f}", b=f"{cb:.2f}",
                                                         fy=fy_label(start))
            works.append(dict(work_id=f"PWD-WRK-{n:05d}", work_title=title, work_type=wtype, asset_id=a["asset_id"],
                              office_id=office["office_id"], amount=amount, start=start,
                              target=start + timedelta(days=planned), mobilize=mobilize,
                              actual=max(mobilize + 3, int(round(planned * factor))),
                              hold_at=(int(rng.uniform(0.25, 0.7) * planned) + mobilize) if hold else None,
                              hold_len=hold_len,
                              cost_factor=rng.uniform(0.82, 1.08) if rng.random() < 0.85 else rng.uniform(1.0, 1.10),
                              bill_lag=rng.uniform(0.7, 0.97), prog_noise=rng.uniform(0.9, 1.04)))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return works


def work_state(w, on: date):
    """Status/progress/expenditure of work w as seen on date `on`."""
    el = (on - w["start"]).days
    if el < 0:
        return None
    if el < w["mobilize"]:
        return dict(status="sanctioned", pct=0, exp=0.0, completion=None)
    actual, work_el = w["actual"], el
    if w["hold_at"] is not None and el >= w["hold_at"]:
        if el < w["hold_at"] + w["hold_len"]:
            f = (w["hold_at"] - w["mobilize"]) / max(1, actual - w["mobilize"])
            pct = max(1, min(95, int(100 * f * w["prog_noise"])))
            return dict(status="on_hold", pct=pct,
                        exp=round(w["amount"] * w["cost_factor"] * pct / 100 * w["bill_lag"], 2), completion=None)
        actual += w["hold_len"]                       # resumed after the hold
        work_el = el - w["hold_len"]
    if el >= actual:
        return dict(status="completed", pct=100, exp=round(w["amount"] * w["cost_factor"], 2),
                    completion=w["start"] + timedelta(days=actual))
    f = (work_el - w["mobilize"]) / max(1, w["actual"] - w["mobilize"])
    pct = max(1, min(99, int(100 * f * w["prog_noise"])))
    return dict(status="in_progress", pct=pct, exp=round(w["amount"] * w["cost_factor"] * pct / 100 * w["bill_lag"], 2),
                completion=None)


# ----------------------------------------------------------------------------------------------
# Announcements
# ----------------------------------------------------------------------------------------------
def names_of(W, codes):
    return ", ".join(W.taluk_by_code[c]["name"] for c in codes)


def gen_announcements_day(W, d: date):
    rng = R("ann", d.isoformat())
    items = []  # (priority, dt, title, summary, category, codes)

    def office_t():
        if is_workday(d):
            return time_on(d, OFFICE_W, rng)
        return time_on(d, FIELD_W, rng) if rng.random() < 0.5 else None

    ev_today = W.events.get(d)
    ev_tomorrow = W.events.get(d + timedelta(days=1))
    ev_yday = W.events.get(d - timedelta(days=1))
    if ev_tomorrow and ev_tomorrow["kind"] == "heavy_rain":
        cl = ev_tomorrow["cluster"]
        items.append((1, fuzz(ist(d, rng.randint(15, 19), rng.randrange(60)), rng),
                      f"IMD heavy rain warning for {d + timedelta(days=1):%d %b}: PWD on alert",
                      f"IMD has forecast heavy to very heavy rain. Pump sets, JCBs and sand bags pre-positioned in "
                      f"{names_of(W, cl)}. Field staff to stay at vulnerable points; control room working 24x7.",
                      "advisory", cl))
    if ev_today:
        cl = ev_today["cluster"]
        if ev_today["kind"] == "heavy_rain":
            items.append((0, fuzz(ist(d, ev_today["peak_hour"], rng.randrange(60)) + timedelta(minutes=rng.uniform(20, 90)), rng),
                          f"Heavy rain alert - {names_of(W, cl[:3])} and surroundings",
                          f"Very heavy rainfall recorded in {names_of(W, cl)}. Public in low-lying areas advised to "
                          f"avoid water bodies and canal banks. Report stagnation to the PWD control room.",
                          "alert", cl))
        if ev_today["breach"] and ev_today["breach_asset"]:
            a = W.asset_by_id[ev_today["breach_asset"]]
            items.append((0, fuzz(ist(d, rng.randint(7, 22), rng.randrange(60)), rng),
                          f"Alert: breach reported in {a['asset_name']}",
                          f"A breach has been reported in {a['asset_name']} near {a['locality']}. Residents of "
                          f"{a['locality']} and nearby streets advised to move to higher floors / relief centres. "
                          f"Closure works under way.", "alert", ev_today["cluster"][:3]))
    if ev_yday:
        t = office_t() or fuzz(ist(d, rng.randint(10, 17), rng.randrange(60)), rng)
        if ev_yday["breach"] and ev_yday["breach_asset"]:
            a = W.asset_by_id[ev_yday["breach_asset"]]
            items.append((2, t, f"Breach in {short_asset(a['asset_name'])} closed",
                          f"The breach in {a['asset_name']} was closed using sand bags and gravel. Permanent "
                          f"strengthening proposal sent for sanction. Dewatering continuing in {a['locality']}.",
                          "work_update", [a["taluk_code"]]))
        else:
            n_p = rng.randint(18, 60)
            items.append((2, t, f"Post-rain restoration: {n_p} pump sets deployed",
                          f"{n_p} pump sets and {rng.randint(6, 20)} JCBs deployed in {names_of(W, ev_yday['cluster'])}. "
                          f"Water drained from most locations; canals flowing freely.", "work_update", ev_yday["cluster"]))
    # lake / reservoir surplus (alert only when surplus starts after a dry spell)
    for a in W.lakes:
        if not surplus_started(W, a["asset_id"], d):
            continue
        s_today = W.levels[a["asset_id"]][d]
        t = fuzz(ist(d, rng.randint(7, 11), rng.randrange(60)), rng)
        if a["taluk_code"]:
            items.append((1, t, f"{a['asset_name']} surplussing",
                          f"{a['asset_name']} has reached full capacity and is surplussing. Residents along the "
                          f"surplus course in {a['locality']} advised caution; field staff monitoring.",
                          "alert", a["downstream"]))
        else:
            items.append((0, t, f"Surplus release from {short_asset(a['asset_name'])}: {s_today[1]} cusecs",
                          f"Water released from {a['asset_name']} at {s_today[1]} cusecs. People living along the "
                          f"river banks in {names_of(W, a['downstream']) or 'downstream villages'} alerted.",
                          "alert", a["downstream"]))
    # heavy rain (non-event)
    mx = max(W.rain[d].values())
    if not ev_today and mx >= 90 and rng.random() < 0.7:
        cl = [c for c, v in W.rain[d].items() if v >= 50]
        items.append((1, fuzz(ist(d, rng.randint(8, 21), rng.randrange(60)), rng), f"Rain alert: {mx:.0f} mm recorded",
                      f"Heavy rain recorded in {names_of(W, cl)}. Public requested not to venture near canals.",
                      "alert", cl))
    # work updates from actual works
    done_today = [w for w in W.works if w["start"] <= d and (ws := work_state(w, d)) and ws["completion"] == d]
    for w in done_today:
        if rng.random() < 0.22:
            t = office_t()
            if t:
                a = W.asset_by_id[w["asset_id"]]
                items.append((3, t, f"Work completed: {w['work_title']}",
                              f"{w['work_title']} completed at a cost of Rs.{w['amount'] * w['cost_factor']:.2f} lakh.",
                              "work_update", [a["taluk_code"]] if a["taluk_code"] else a["downstream"]))
    # routine
    if is_workday(d) and rng.random() < 0.33:
        t = office_t()
        cat = rng.choices(["advisory", "work_update", "press_release"], weights=[3, 4, 3])[0]
        codes = rng.sample(W.codes, rng.randint(1, 3)) if rng.random() < 0.7 else []
        if cat == "work_update":
            active = [w for w in W.works if (ws := work_state(w, d)) and ws["status"] == "in_progress"]
            if active:
                w = rng.choice(active)
                ws = work_state(w, d)
                a = W.asset_by_id[w["asset_id"]]
                items.append((4, t, f"Progress: {w['work_title']} - {ws['pct']}% done",
                              f"{w['work_type'].replace('_', ' ').capitalize()} at {a['asset_name']} is {ws['pct']}% "
                              f"complete. Target date {w['target']:%d-%m-%Y}.", "work_update",
                              [a["taluk_code"]] if a["taluk_code"] else a["downstream"]))
        elif cat == "advisory":
            m = d.month
            if m in (8, 9, 10, 11, 12):
                ttl = f"Monsoon preparedness: report stagnation (ref {d:%d%m})"
                sm = (f"Public may report water stagnation, canal overflow or weak bunds to the PWD control room. "
                      f"{rng.randint(80, 300)} pump sets kept ready across the district.")
            elif m in (1, 2, 3, 4, 5):
                ttl = f"Protect water bodies: no dumping in lakes and canals (ref {d:%d%m})"
                sm = "Dumping of debris or encroachment in lakes, canals and river margins is an offence. Report violations to PWD."
            else:
                ttl = f"Pre-monsoon desilting drive - public cooperation sought (ref {d:%d%m})"
                sm = "Desilting of canals and macro drains is under way. Residents requested not to dump waste into waterways."
            items.append((4, t, ttl, sm, "advisory", codes))
        else:
            c = rng.choice(W.codes)
            ttl = rng.choice([f"Chief Engineer reviews flood-mitigation works in {W.taluk_by_code[c]['name']}",
                              f"Collector inspects PWD works in {W.taluk_by_code[c]['name']} taluk",
                              f"PWD completes {rng.randint(12, 80)} km of desilting so far this season"])
            items.append((4, t, ttl + f" ({d:%d %b})",
                          "Officials reviewed progress of ongoing works, instructed contractors to speed up and "
                          "complete before the monsoon.", "press_release", [c]))
    items = [i for i in items if i[1] is not None]
    items.sort(key=lambda x: (x[0], x[1]))
    items = sorted(items[:ANN_BLOCK], key=lambda x: x[1])
    base = (d - ID_ANCHOR).days
    out = []
    for k, (_p, t, title, summary, cat, codes) in enumerate(items):
        out.append(dict(announcement_id=f"PWD-ANN-{base * ANN_BLOCK + k + 1:04d}", _t=t, title=title, summary=summary,
                        category=cat, taluk_codes_affected="|".join(sorted(set(codes)))))
    return out


# ----------------------------------------------------------------------------------------------
# Output + validation
# ----------------------------------------------------------------------------------------------
def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction="raise")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def parse_ts(s):
    return datetime.fromisoformat(s) if s else None


def validate(W, tables, now, wstart):
    errors, warnings = [], []

    def err(msg):
        if len(errors) < 60:
            errors.append(msg)

    today = now.date()
    # schema / forbidden columns
    for fname, rows in tables.items():
        for col in SCHEMA[fname]:
            if set(col.split("_")) & FORBIDDEN_TOKENS:
                err(f"{fname}: forbidden column {col}")
    ids = {k: {r[k2] for r in tables[f]} for k, (f, k2) in {
        "office": ("pwd_offices.csv", "office_id"), "asset": ("pwd_assets.csv", "asset_id"),
        "incident": ("pwd_incidents.csv", "incident_id")}.items()}
    codes = set(W.codes)
    for f, key in (("pwd_offices.csv", "office_id"), ("pwd_assets.csv", "asset_id"), ("pwd_works.csv", "work_id"),
                   ("pwd_incidents.csv", "incident_id"), ("pwd_tasks.csv", "task_id"),
                   ("pwd_announcements.csv", "announcement_id")):
        vals = [r[key] for r in tables[f]]
        if len(vals) != len(set(vals)):
            err(f"{f}: duplicate {key}")
    # offices
    heads = [o for o in tables["pwd_offices.csv"] if o["wing"] == "HQ"]
    if len(heads) != 1:
        err("offices: need exactly one HQ row")
    for o in tables["pwd_offices.csv"]:
        for c in o["taluk_codes_covered"].split("|"):
            if c and c not in codes:
                err(f"office {o['office_id']} covers unknown taluk {c}")
    # assets
    for a in tables["pwd_assets.csv"]:
        if a["taluk_code"]:
            if a["taluk_code"] not in codes:
                err(f"asset {a['asset_id']} bad taluk")
            t = W.taluk_by_code[a["taluk_code"]]
            if dist_m(float(a["latitude"]), float(a["longitude"]), t["lat"], t["lon"]) > 2000:
                err(f"asset {a['asset_id']} outside its taluk")
        elif a["asset_type"] != "lake":
            err(f"asset {a['asset_id']} blank taluk but not reservoir")
        if bool(a["capacity_mcft"]) != (a["asset_type"] == "lake"):
            err(f"asset {a['asset_id']} capacity only for lakes")
    cap = {a["asset_id"]: float(a["capacity_mcft"]) for a in tables["pwd_assets.csv"] if a["capacity_mcft"]}
    # water levels
    reading_today = False
    for r in tables["pwd_water_levels.csv"]:
        if r["asset_id"] not in cap:
            err(f"water level for non-lake {r['asset_id']}")
            continue
        s = float(r["storage_mcft"])
        if not (0 <= s <= cap[r["asset_id"]]):
            err(f"storage > capacity {r['asset_id']} {r['reading_date']}")
        d = date.fromisoformat(r["reading_date"])
        if d > today or d < wstart.date():
            err(f"reading outside window {r['reading_date']}")
        if d == today:
            reading_today = True
        if int(r["outflow_cusecs"]) < 0:
            err("negative outflow")
    if reading_today and (now.hour, now.minute) < (5, 50):
        err("today's lake reading exists before 06:00")
    # works
    for w in tables["pwd_works.csv"]:
        if w["asset_id"] not in ids["asset"] or w["office_id"] not in ids["office"]:
            err(f"work {w['work_id']} bad FK")
        sd = date.fromisoformat(w["start_date"])
        if sd > today:
            err(f"work {w['work_id']} starts after NOW")
        if date.fromisoformat(w["target_date"]) < sd:
            err(f"work {w['work_id']} target before start")
        if float(w["expenditure_lakh"]) > 1.1 * float(w["sanctioned_amount_lakh"]) + 1e-6:
            err(f"work {w['work_id']} expenditure > 110%")
        pct = int(w["physical_progress_pct"])
        if w["status"] == "completed":
            cd = date.fromisoformat(w["completion_date"])
            if pct != 100 or cd > today or cd < wstart.date() or cd < sd:
                err(f"work {w['work_id']} completion inconsistent")
        else:
            if w["completion_date"] or pct >= 100:
                err(f"work {w['work_id']} open but has completion / 100%")
        if w["status"] == "sanctioned" and pct != 0:
            err(f"work {w['work_id']} sanctioned with progress")
    # incidents
    recent, old = Counter(), Counter()
    for r in tables["pwd_incidents.csv"]:
        t = parse_ts(r["reported_at"])
        if t > now or t < wstart:
            err(f"incident {r['incident_id']} outside window")
        if r["taluk_code"] not in codes:
            err(f"incident {r['incident_id']} bad taluk")
        if r["asset_id"] and r["asset_id"] not in ids["asset"]:
            err(f"incident {r['incident_id']} bad asset")
        lat, lon = float(r["latitude"]), float(r["longitude"])
        tk = W.taluk_by_code[r["taluk_code"]]
        if dist_m(lat, lon, tk["lat"], tk["lon"]) > 2000:
            err(f"incident {r['incident_id']} coords outside taluk")
        if r["asset_id"]:
            a = W.asset_by_id[r["asset_id"]]
            if a["taluk_code"] != r["taluk_code"] or dist_m(lat, lon, a["lat"], a["lon"]) > 800:
                err(f"incident {r['incident_id']} far from asset")
        res = parse_ts(r["resolved_at"])
        if (r["status"] == "resolved") != bool(res):
            err(f"incident {r['incident_id']} status/resolved_at mismatch")
        if res and (res <= t or res > now):
            err(f"incident {r['incident_id']} resolved_at order")
        age_h = (now - t).total_seconds() / 3600
        if age_h <= 3:
            recent[r["status"]] += 1
        if age_h > 30 * 24:
            old[r["status"]] += 1
    if sum(recent.values()) >= 3 and recent["open"] / sum(recent.values()) < 0.6:
        err(f"incidents <3h old not mostly open: {dict(recent)}")
    if sum(old.values()) and old["resolved"] / sum(old.values()) < 0.7:
        err(f"incidents >30d old not mostly resolved: {dict(old)}")
    descs = [r["description"] for r in tables["pwd_incidents.csv"]]
    if len(descs) != len(set(descs)):
        err("identical incident descriptions found")
    # tasks
    ver_age, other_age = [], []
    for r in tables["pwd_tasks.csv"]:
        if r["incident_id"] and r["incident_id"] not in ids["incident"]:
            err(f"task {r['task_id']} bad incident FK")
        if r["grievance_id"] and not r["grievance_id"].startswith("GRV-"):
            err(f"task {r['task_id']} bad grievance id")
        if bool(r["grievance_id"]) == bool(r["incident_id"]):
            err(f"task {r['task_id']} must come from exactly one of grievance / incident")
        o = W.office_by_id.get(r["assigned_office_id"])
        if not o or r["taluk_code"] not in o["codes"]:
            err(f"task {r['task_id']} office does not cover taluk")
        tk = W.taluk_by_code[r["taluk_code"]]
        if dist_m(float(r["latitude"]), float(r["longitude"]), tk["lat"], tk["lon"]) > 2000:
            err(f"task {r['task_id']} coords outside taluk")
        seq = [parse_ts(r[k]) for k in ("assigned_at", "accepted_at", "completed_at", "verified_at")]
        present = [x for x in seq if x]
        if present != sorted(present) or any(x > now or x < wstart for x in present):
            err(f"task {r['task_id']} timestamp order/window")
        if date.fromisoformat(r["due_date"]) < seq[0].date():
            err(f"task {r['task_id']} due before assigned")
        st = r["status"]
        exp = {"assigned": (1, 0, 0, 0), "accepted": (1, 1, 0, 0), "in_progress": (1, 1, 0, 0),
               "completed_pending_verification": (1, 1, 1, 0), "verified": (1, 1, 1, 1), "reopened": (1, 1, 1, 1)}[st]
        if tuple(int(bool(x)) for x in seq) != exp:
            err(f"task {r['task_id']} status {st} inconsistent with timestamps")
        if bool(r["completion_photo_path"]) != bool(seq[2]):
            err(f"task {r['task_id']} photo path mismatch")
        if st == "reopened" and r["verification_remarks"] not in REOPEN_REASONS:
            err(f"task {r['task_id']} reopened without reason")
        age_h = (now - seq[0]).total_seconds() / 3600
        if age_h < 48 and st in ("completed_pending_verification", "verified", "reopened"):
            err(f"task {r['task_id']} assigned <48h ago already completed")
        if st == "completed_pending_verification" and (now - seq[2]).days > 45:
            err(f"task {r['task_id']} pending verification too old")
        (ver_age if st == "verified" else other_age).append(age_h)
    if ver_age and other_age and sorted(ver_age)[len(ver_age) // 2] <= sorted(other_age)[len(other_age) // 2]:
        err("verified tasks are not older than unverified ones")
    # announcements
    for r in tables["pwd_announcements.csv"]:
        t = parse_ts(r["published_at"])
        if t > now or t < wstart:
            err(f"announcement {r['announcement_id']} outside window")
        for c in r["taluk_codes_affected"].split("|"):
            if c and c not in codes:
                err(f"announcement {r['announcement_id']} bad taluk {c}")
    return errors, warnings


# ----------------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------------
def main():
    global SEED
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Generate synthetic PWD dataset (Chennai District).")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=os.path.join(".", "data", "pwd"))
    ap.add_argument("--now", help="ISO datetime override, e.g. 2026-09-25T14:30:00+05:30 (naive = IST)")
    ap.add_argument("--taluk-master", help="CSV: taluk_code,taluk_name,latitude,longitude")
    ap.add_argument("--rainfall-csv", help="CSV: date,taluk_code,rainfall_mm")
    args = ap.parse_args()
    SEED = args.seed

    if args.now:
        now = datetime.fromisoformat(args.now)
        now = now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)
    else:
        now = datetime.now(IST)
    now = now.replace(microsecond=0)
    if now < MIN_NOW:
        sys.exit(f"--now must be on/after {MIN_NOW.date()} (ID/simulation anchor {ID_ANCHOR})")
    wstart = now - timedelta(days=WINDOW_DAYS)
    today = now.date()

    W = World()
    W.taluks = load_taluks(args.taluk_master)
    build_static(W)
    sim_last = today + timedelta(days=1)
    W.events = build_events(W, ID_ANCHOR, sim_last)
    W.rain = simulate_rain(W, ID_ANCHOR, sim_last)
    if args.rainfall_csv:
        apply_rain_csv(W, args.rainfall_csv)
    W.levels = simulate_lakes(W, ID_ANCHOR, sim_last)
    W.works = build_works_catalog(W, today)

    # ---- incidents & tasks
    incidents, tasks = [], []
    d = wstart.date()
    while d <= today:
        inc_d, tsk_d = gen_day(W, d)
        kept = {id(i) for i in inc_d if wstart <= i["_t"] <= now}
        incidents += [i for i in inc_d if id(i) in kept]
        tasks += [t for t in tsk_d if wstart <= t["_tl"]["assigned"] <= now and (t["_inc"] is None or id(t["_inc"]) in kept)]
        d += timedelta(days=1)
    incidents.sort(key=lambda x: x["_t"])
    seen = set()
    for inc in sorted(incidents, key=lambda x: x["incident_id"]):
        rng = R("uniq", inc["incident_id"])
        while inc["description"] in seen:
            inc["description"] += rng.choice(UNIQ_SUFFIX).format(n=rng.randint(2, 99))
        seen.add(inc["description"])
    inc_rows = [dict(incident_id=i["incident_id"], reported_at=ts(i["_t"]), report_source=i["report_source"],
                     taluk_code=i["taluk_code"], locality=i["locality"], latitude=f"{i['lat']:.6f}",
                     longitude=f"{i['lon']:.6f}", asset_id=i["asset_id"], incident_type=i["incident_type"],
                     description=i["description"],
                     people_affected_est="" if i["people_affected_est"] is None else i["people_affected_est"],
                     service_disruption=i["service_disruption"], access_blocked=i["access_blocked"],
                     casualty_reported=i["casualty_reported"], status=(st := incident_status(i, now)),
                     resolved_at=ts(i["_res"]) if st == "resolved" else "") for i in incidents]
    tasks.sort(key=lambda t: t["_tl"]["assigned"])
    task_rows = [task_row(t, now) for t in tasks]

    # ---- water levels
    wl_rows = []
    for a in W.lakes:
        d = wstart.date()
        while d <= today:
            rt = ist(d, 6, 0) + timedelta(minutes=R("reading", d.isoformat()).randint(-8, 22))
            if wstart <= rt <= now:
                s, o, _sp = W.levels[a["asset_id"]][d]
                wl_rows.append(dict(asset_id=a["asset_id"], reading_date=ds(d), storage_mcft=s, outflow_cusecs=o))
            d += timedelta(days=1)

    # ---- works
    work_rows, behind = [], 0
    for w in W.works:
        st = work_state(w, today)
        if not st:
            continue
        if st["status"] == "completed" and st["completion"] < wstart.date():
            continue
        exp_pct = min(100, 100 * max(0, (today - w["start"]).days) / max(1, (w["target"] - w["start"]).days))
        if (st["status"] == "completed" and st["completion"] > w["target"]) or \
                (st["status"] != "completed" and (today > w["target"] or st["pct"] < exp_pct - 15)):
            behind += 1
        work_rows.append(dict(work_id=w["work_id"], work_title=w["work_title"], work_type=w["work_type"],
                              asset_id=w["asset_id"], office_id=w["office_id"],
                              sanctioned_amount_lakh=f"{w['amount']:.2f}", expenditure_lakh=f"{st['exp']:.2f}",
                              start_date=ds(w["start"]), target_date=ds(w["target"]),
                              completion_date=ds(st["completion"]), status=st["status"],
                              physical_progress_pct=st["pct"]))

    # ---- announcements
    ann_rows = []
    d = wstart.date()
    while d <= today:
        for a in gen_announcements_day(W, d):
            if wstart <= a["_t"] <= now:
                ann_rows.append(dict(announcement_id=a["announcement_id"], published_at=ts(a["_t"]), title=a["title"],
                                     summary=a["summary"], category=a["category"],
                                     taluk_codes_affected=a["taluk_codes_affected"]))
        d += timedelta(days=1)
    ann_rows.sort(key=lambda r: r["published_at"])

    office_rows = [dict(office_id=o["office_id"], office_name=o["office_name"], wing=o["wing"],
                        officer_name=o["officer_name"], designation=o["designation"], phone=o["phone"],
                        email=o["email"], taluk_codes_covered="|".join(o["codes"])) for o in W.offices]
    asset_rows = [dict(asset_id=a["asset_id"], asset_name=a["asset_name"], asset_type=a["asset_type"],
                       taluk_code=a["taluk_code"], locality=a["locality"], latitude=f"{a['lat']:.6f}",
                       longitude=f"{a['lon']:.6f}", capacity_mcft=a["capacity"] or "") for a in W.assets]

    tables = {"pwd_offices.csv": office_rows, "pwd_assets.csv": asset_rows, "pwd_water_levels.csv": wl_rows,
              "pwd_works.csv": work_rows, "pwd_incidents.csv": inc_rows, "pwd_tasks.csv": task_rows,
              "pwd_announcements.csv": ann_rows}
    os.makedirs(args.out, exist_ok=True)
    for fname, rows in tables.items():
        write_csv(os.path.join(args.out, fname), SCHEMA[fname], rows)

    # ---- report
    print(f"NOW            : {ts(now)}")
    print(f"Window         : {ts(wstart)}  ->  {ts(now)}")
    ev_in = sorted(e for e in W.events if wstart.date() <= e <= today)
    print("Event days     : " + ", ".join(f"{e} ({W.events[e]['kind']}{'+breach' if W.events[e]['breach'] and W.events[e]['kind'] != 'bund_breach' else ''}"
                                         f" @ {W.taluk_by_code[W.events[e]['center']]['name']})" for e in ev_in))
    print(f"Output folder  : {os.path.abspath(args.out)}\n")
    print("Row counts")
    for fname, rows in tables.items():
        print(f"  {fname:<24}{len(rows):>6}")
    langs = Counter(i["_tkeys"][0][0] for i in incidents)
    n = max(1, len(incidents))
    grouped = sum(1 for i in incidents if not i["_primary"]) + len({id(i["_group"]) for i in incidents if not i["_primary"]})
    print(f"\n(internal ground truth, not written) language mix: Tamil {langs['ta'] / n:.0%}, Tanglish {langs['tg'] / n:.0%}, "
          f"English {langs['en'] / n:.0%}; incidents in near-duplicate groups: {grouped / n:.1%}; "
          f"works behind schedule: {behind / max(1, len(work_rows)):.0%}")
    print("\nLast 5 incidents by reported_at")
    for r in inc_rows[-5:]:
        print(f"  {r['incident_id']}  {r['reported_at']}  {r['taluk_code']}  {r['incident_type']:<15}{r['status']:<12}"
              f"{r['report_source']:<18}{r['description'][:70]}")
    print("\nIncidents by taluk x status")
    tab = defaultdict(Counter)
    for r in inc_rows:
        tab[r["taluk_code"]][r["status"]] += 1
    print(f"  {'taluk':<22}{'open':>6}{'in_prog':>9}{'resolved':>10}{'all':>6}")
    for c in W.codes:
        t = tab[c]
        print(f"  {c + ' ' + W.taluk_by_code[c]['name']:<22}{t['open']:>6}{t['in_progress']:>9}{t['resolved']:>10}{sum(t.values()):>6}")
    tc = Counter(r["status"] for r in task_rows)
    nt = max(1, len(task_rows))
    print("\nTask status mix: " + ", ".join(f"{k} {v} ({v / nt:.0%})" for k, v in tc.most_common()))
    overdue = sum(1 for r in task_rows if not r["completed_at"] and date.fromisoformat(r["due_date"]) < today)
    print(f"Tasks not completed and past due_date (inferable, no flag stored): {overdue}")
    print("Work status mix: " + ", ".join(f"{k} {v}" for k, v in Counter(r["status"] for r in work_rows).most_common()))
    print("Announcements  : " + ", ".join(f"{k} {v}" for k, v in Counter(r["category"] for r in ann_rows).most_common()))
    groups = defaultdict(list)
    for i in incidents:
        groups[id(i["_group"] if not i["_primary"] else i)].append(i)
    shown = 0
    print("\nSample near-duplicate groups (ground truth, NOT in CSV)")
    for g in groups.values():
        if len(g) >= 3 and shown < 2:
            shown += 1
            for i in sorted(g, key=lambda x: x["_t"]):
                print(f"  {i['incident_id']}  {ts(i['_t'])}  {i['report_source']:<18}{i['lat']:.5f},{i['lon']:.5f}  {i['description'][:75]}")
            print()

    errors, warnings = validate(W, tables, now, wstart)
    for wmsg in warnings:
        print("WARN:", wmsg)
    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("VALIDATION PASSED: window, NOW-bound, FKs, timestamp order, storage<=capacity, schema, statuses")


if __name__ == "__main__":
    main()
