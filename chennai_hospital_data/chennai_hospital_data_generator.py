"""
Chennai District Collector Intelligence Dashboard
Hospital / Health dataset generator (persistent, deterministic, synthetic).

All values are FICTIONAL and intended only for dashboard development/testing.
They do not represent real hospital statistics or patient records.

Dashboard features this dataset feeds:
  * Interactive District Map  -> taluk, latitude, longitude, health_alert, alert_level
  * Health Department module  -> beds, staff, medicine, ambulance, disease burden
  * Health alerts per area    -> health_alert, alert_level (rule-derived)
  * Daily / weekly / monthly / quarterly trends -> one row per hospital per day,
    covering a rolling window of the last HISTORY_DAYS days

Behaviour:
  * First run        -> generate the last HISTORY_DAYS days up to today.
  * Same-day re-run  -> nothing generated, existing data displayed.
  * Later run        -> only missing dates are generated and appended, and
                        dates older than the rolling window are dropped.
  * Rows inside the window are never modified. Values for a (date, hospital)
    pair are seeded with SHA-256, so they are reproducible regardless of run order.
"""

import hashlib
import math
import os
import random
from datetime import date, datetime, timedelta

import pandas as pd

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_FILE = os.path.join(SCRIPT_DIR, "chennai_hospital_health_data.csv")

# Rolling window (in days, including today): older dates are dropped.
HISTORY_DAYS = 180

DATE_FORMAT = "%Y-%m-%d"

COLUMNS = [
    "hospital_name",
    "specialties",
    "address",
    "date",
    "taluk",
    "latitude",
    "longitude",
    "total_beds",
    "occupied_beds",
    "bed_occupancy_rate",
    "outpatient_count",
    "emergency_cases",
    "disease_type",
    "disease_cases",
    "vaccination_coverage",
    "medicine_availability",
    "doctor_count",
    "nurse_count",
    "ambulance_available",
    "health_alert",
    "alert_level",
]

MEDICINE_VALUES = ["Available", "Limited", "Critical"]
AMBULANCE_VALUES = ["Yes", "No"]
ALERT_LEVELS = ["Normal", "Watch", "Warning", "Critical"]

# Fixed-date public holidays (OP clinics run reduced hours).
FIXED_HOLIDAYS = {(1, 1), (1, 14), (1, 15), (1, 16), (1, 26), (4, 14),
                  (5, 1), (8, 15), (10, 2), (12, 25)}

# --------------------------------------------------------------------------
# Fixed hospital master data
# "profile" is internal (not written to CSV): capacity, staffing, patient
# volume and baseline occupancy/vaccination used to scale synthetic values.
# --------------------------------------------------------------------------
HOSPITALS = [
    {
        "hospital_name": "Rajiv Gandhi Government General Hospital",
        "specialties": "General Medicine, Cardiology",
        "address": "Park Town, Chennai 600003",
        "taluk": "Tondiarpet",
        "latitude": 13.0823, "longitude": 80.2757,
        "profile": {"beds": 3000, "doctors": 520, "nurses": 1350, "op": 12000,
                    "er": 650, "occ": 0.91, "vacc": 86},
    },
    {
        "hospital_name": "Stanley Medical College and Hospital",
        "specialties": "Plastic Surgery, Liver Transplant",
        "address": "Royapuram, Chennai 600001",
        "taluk": "Tondiarpet",
        "latitude": 13.1060, "longitude": 80.2872,
        "profile": {"beds": 1800, "doctors": 360, "nurses": 900, "op": 8000,
                    "er": 420, "occ": 0.87, "vacc": 83},
    },
    {
        "hospital_name": "Kilpauk Medical College and Hospital",
        "specialties": "Dermatology, Psychiatry",
        "address": "Kilpauk, Chennai 600010",
        "taluk": "Purasawalkam",
        "latitude": 13.0785, "longitude": 80.2425,
        "profile": {"beds": 1200, "doctors": 260, "nurses": 620, "op": 5500,
                    "er": 260, "occ": 0.80, "vacc": 81},
    },
    {
        "hospital_name": "Government Royapettah Hospital",
        "specialties": "Orthopedics, Neurology",
        "address": "Royapettah, Chennai 600014",
        "taluk": "Mylapore",
        "latitude": 13.0540, "longitude": 80.2640,
        "profile": {"beds": 850, "doctors": 190, "nurses": 450, "op": 4200,
                    "er": 320, "occ": 0.86, "vacc": 80},
    },
    {
        "hospital_name": "Tamil Nadu Govt Multi-Super Specialty Hospital",
        "specialties": "Neurology, Gastroenterology",
        "address": "Anna Salai, Chennai 600035",
        "taluk": "Mylapore",
        "latitude": 13.0690, "longitude": 80.2750,
        "profile": {"beds": 500, "doctors": 170, "nurses": 380, "op": 2500,
                    "er": 120, "occ": 0.76, "vacc": 82},
    },
    {
        "hospital_name": "Institute of Child Health and Hospital",
        "specialties": "Pediatrics, Neonatology",
        "address": "Egmore, Chennai 600008",
        "taluk": "Egmore",
        "latitude": 13.0745, "longitude": 80.2580,
        "profile": {"beds": 1000, "doctors": 210, "nurses": 560, "op": 4000,
                    "er": 220, "occ": 0.88, "vacc": 93},
    },
    {
        "hospital_name": "Obstetrics and Gynecology Hospital Egmore",
        "specialties": "Maternal Healthcare, Neonatology",
        "address": "Egmore, Chennai 600008",
        "taluk": "Egmore",
        "latitude": 13.0770, "longitude": 80.2610,
        "profile": {"beds": 800, "doctors": 150, "nurses": 480, "op": 2800,
                    "er": 160, "occ": 0.90, "vacc": 91},
    },
    {
        "hospital_name": "Government Peripheral Hospital Anna Nagar",
        "specialties": "General Medicine, Gynecology",
        "address": "Anna Nagar, Chennai 600102",
        "taluk": "Aminjikarai",
        "latitude": 13.0870, "longitude": 80.2100,
        "profile": {"beds": 200, "doctors": 45, "nurses": 110, "op": 1200,
                    "er": 70, "occ": 0.78, "vacc": 84},
    },
    {
        "hospital_name": "Govt Hospital for Thoracic Medicine",
        "specialties": "Pulmonology, Tuberculosis",
        "address": "Tambaram, Chennai 600047",
        "taluk": "Tambaram",
        "latitude": 12.9405, "longitude": 80.1320,
        "profile": {"beds": 750, "doctors": 110, "nurses": 320, "op": 1800,
                    "er": 90, "occ": 0.73, "vacc": 79},
    },
]

HOSPITAL_LOOKUP = {h["hospital_name"]: h for h in HOSPITALS}
MASTER_FIELDS = ["specialties", "address", "taluk", "latitude", "longitude"]

# Specialty -> {disease: baseline share of daily outpatients}
SPECIALTY_DISEASES = {
    "General Medicine": {"Diabetes": 0.070, "Hypertension": 0.065,
                         "Respiratory Infection": 0.050, "Viral Fever": 0.050,
                         "Dengue": 0.030},
    "Cardiology": {"Cardiac Conditions": 0.060, "Hypertension": 0.050},
    "Plastic Surgery": {"Burn Injuries": 0.030, "Reconstructive Trauma": 0.020},
    "Liver Transplant": {"Liver Disease": 0.035, "Hepatitis": 0.030},
    "Dermatology": {"Dermatological Conditions": 0.100, "Fungal Skin Infection": 0.060},
    "Psychiatry": {"Mental Health Conditions": 0.080},
    "Orthopedics": {"Orthopedic Injury": 0.110},
    "Neurology": {"Stroke": 0.030, "Neurological Disorders": 0.050},
    "Gastroenterology": {"Gastrointestinal Disorders": 0.070, "Liver Disease": 0.030},
    "Pediatrics": {"Pediatric Infection": 0.080, "Respiratory Infection": 0.070,
                   "Acute Diarrhoeal Disease": 0.050, "Dengue": 0.035},
    "Neonatology": {"Neonatal Conditions": 0.060},
    "Maternal Healthcare": {"Maternal Conditions": 0.140},
    "Gynecology": {"Maternal Conditions": 0.060, "Gynecological Conditions": 0.060},
    "Pulmonology": {"Respiratory Infection": 0.100, "Pneumonia": 0.060, "Asthma": 0.050},
    "Tuberculosis": {"Tuberculosis": 0.140},
}

# Chennai seasonality: disease -> (peak day-of-year, amplitude).
# NE monsoon (Oct-Dec) drives vector/water-borne peaks; summer drives diarrhoea.
DISEASE_SEASON = {
    "Dengue": (305, 0.90),
    "Viral Fever": (298, 0.45),
    "Respiratory Infection": (354, 0.35),
    "Pneumonia": (360, 0.30),
    "Asthma": (340, 0.25),
    "Pediatric Infection": (310, 0.35),
    "Acute Diarrhoeal Disease": (150, 0.40),
    "Hepatitis": (300, 0.35),
    "Fungal Skin Infection": (300, 0.35),
    "Orthopedic Injury": (320, 0.10),
}

# Diseases for which an unusually high share of patients raises a surge alert.
OUTBREAK_DISEASES = {"Dengue", "Viral Fever", "Acute Diarrhoeal Disease",
                     "Hepatitis", "Pediatric Infection", "Respiratory Infection"}


# --------------------------------------------------------------------------
# Deterministic randomness
# --------------------------------------------------------------------------
def stable_seed(*parts):
    """Reproducible integer seed from strings using SHA-256 (not hash())."""
    key = "|".join(str(p) for p in parts)
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16)


def smooth_noise(hospital_name, channel, day_ordinal, period):
    """
    Deterministic, slowly varying noise (~N(0,1)). Random knots every `period`
    days are cosine-interpolated so consecutive days move gradually instead of
    jumping, which makes trend charts look like real operational data.
    """
    k, offset = divmod(day_ordinal, period)
    a = random.Random(stable_seed(hospital_name, channel, k)).gauss(0, 1)
    b = random.Random(stable_seed(hospital_name, channel, k + 1)).gauss(0, 1)
    w = (1 - math.cos(math.pi * offset / period)) / 2
    return a * (1 - w) + b * w


def seasonal_factor(day, peak_doy, amplitude):
    doy = day.timetuple().tm_yday
    return 1 + amplitude * math.cos(2 * math.pi * (doy - peak_doy) / 365.25)


def diseases_for(hospital):
    shares = {}
    for spec in hospital["specialties"].split(","):
        for disease, share in SPECIALTY_DISEASES.get(spec.strip(), {}).items():
            shares[disease] = max(shares.get(disease, 0), share)
    return shares


# --------------------------------------------------------------------------
# Record generation
# --------------------------------------------------------------------------
def derive_alerts(occupancy_rate, medicine, ambulance, surge_disease, surge_ratio):
    """Rule-based health alert for the map and briefing panels."""
    alerts, level = [], 0
    if surge_disease:
        alerts.append(f"{surge_disease} Surge")
        level = max(level, 3 if surge_ratio >= 1.8 else 2)
    if occupancy_rate >= 95:
        alerts.append("Bed Capacity Critical")
        level = max(level, 3)
    elif occupancy_rate >= 90:
        alerts.append("High Bed Occupancy")
        level = max(level, 1)
    if medicine == "Critical":
        alerts.append("Medicine Shortage")
        level = max(level, 3)
    elif medicine == "Limited":
        alerts.append("Low Medicine Stock")
        level = max(level, 1)
    if ambulance == "No":
        alerts.append("Ambulance Unavailable")
        level = max(level, 2)
    return ("; ".join(alerts) if alerts else "None"), ALERT_LEVELS[level]


def generate_record(hospital, date_str):
    """Generate one synthetic record, fully determined by (date, hospital)."""
    name = hospital["hospital_name"]
    p = hospital["profile"]
    day = datetime.strptime(date_str, DATE_FORMAT).date()
    n = day.toordinal()
    rng = random.Random(stable_seed(date_str, name))  # daily jitter

    is_weekend = day.weekday() >= 5
    is_sunday = day.weekday() == 6
    is_holiday = (day.month, day.day) in FIXED_HOLIDAYS
    load_season = seasonal_factor(day, 320, 0.06)  # peak in mid-November

    # Beds: sanctioned capacity, minus a few beds closed for maintenance.
    total_beds = p["beds"] - int(abs(smooth_noise(name, "closed", n, 10)) * p["beds"] * 0.006)
    occ = (p["occ"] * load_season
           + 0.035 * smooth_noise(name, "occ", n, 6)
           + rng.gauss(0, 0.008)
           - (0.02 if is_sunday else 0))
    occ = min(0.995, max(0.50, occ))
    occupied_beds = min(total_beds, int(round(total_beds * occ)))
    bed_occupancy_rate = round(occupied_beds / total_beds * 100, 2)

    # Patient volume: OP drops on Sundays/holidays, emergencies rise slightly.
    op_factor = 0.30 if (is_sunday or is_holiday) else (0.75 if is_weekend else 1.0)
    if day.weekday() == 0 and not is_holiday:
        op_factor = 1.12  # Monday backlog
    outpatient_count = int(round(p["op"] * op_factor * load_season
                                 * math.exp(0.07 * smooth_noise(name, "op", n, 5))
                                 * rng.uniform(0.94, 1.06)))
    er_factor = 1.12 if (is_weekend or is_holiday) else 1.0
    emergency_cases = int(round(p["er"] * er_factor * load_season
                                * math.exp(0.10 * smooth_noise(name, "er", n, 4))
                                * rng.uniform(0.90, 1.10)))

    # Disease burden: the day's most reported condition at this hospital.
    # Shares are applied to a weekday-normalised OP base so Sundays do not
    # artificially suppress disease counts.
    op_base = p["op"] * load_season
    best, best_cases, best_ratio = None, -1, 0.0
    for disease, share in diseases_for(hospital).items():
        peak, amp = DISEASE_SEASON.get(disease, (0, 0.0))
        season = seasonal_factor(day, peak, amp)
        wave = math.exp(0.22 * smooth_noise(name, "dz:" + disease, n, 9))
        cases = op_base * share * season * wave * rng.uniform(0.92, 1.08)
        cases *= 0.55 if (is_sunday or is_holiday) else 1.0
        if cases > best_cases:
            best, best_cases, best_ratio = disease, cases, season * wave
    disease_type = best
    disease_cases = int(round(best_cases))
    surge_disease = disease_type if (disease_type in OUTBREAK_DISEASES and best_ratio >= 1.35) else None

    # Vaccination coverage: slow-moving catchment-area metric.
    vaccination_coverage = round(min(100.0, max(0.0,
        p["vacc"] + 2.5 * smooth_noise(name, "vacc", n, 21) + rng.gauss(0, 0.25))), 2)

    # Medicine stock: shortages persist for a few days rather than flickering.
    stock = smooth_noise(name, "stock", n, 7) + rng.gauss(0, 0.25)
    medicine_availability = "Critical" if stock > 1.9 else ("Limited" if stock > 1.05 else "Available")

    # Staff on duty: lower on weekends/holidays.
    duty = 0.72 if (is_sunday or is_holiday) else (0.85 if is_weekend else 1.0)
    doctor_count = max(1, int(round(p["doctors"] * duty
                                    * (0.93 + 0.03 * smooth_noise(name, "doc", n, 7))
                                    * rng.uniform(0.98, 1.02))))
    nurse_count = max(1, int(round(p["nurses"] * duty
                                   * (0.94 + 0.025 * smooth_noise(name, "nurse", n, 7))
                                   * rng.uniform(0.98, 1.02))))

    fleet = smooth_noise(name, "ambulance", n, 5) + rng.gauss(0, 0.3)
    ambulance_available = "No" if fleet > 1.75 else "Yes"

    health_alert, alert_level = derive_alerts(bed_occupancy_rate, medicine_availability,
                                              ambulance_available, surge_disease, best_ratio)

    return {
        "hospital_name": name,
        "specialties": hospital["specialties"],
        "address": hospital["address"],
        "date": date_str,
        "taluk": hospital["taluk"],
        "latitude": hospital["latitude"],
        "longitude": hospital["longitude"],
        "total_beds": total_beds,
        "occupied_beds": occupied_beds,
        "bed_occupancy_rate": bed_occupancy_rate,
        "outpatient_count": outpatient_count,
        "emergency_cases": emergency_cases,
        "disease_type": disease_type,
        "disease_cases": disease_cases,
        "vaccination_coverage": vaccination_coverage,
        "medicine_availability": medicine_availability,
        "doctor_count": doctor_count,
        "nurse_count": nurse_count,
        "ambulance_available": ambulance_available,
        "health_alert": health_alert,
        "alert_level": alert_level,
    }


def generate_for_dates(date_strings, existing_keys):
    """Generate records for the given dates, skipping existing hospital/date keys."""
    records = []
    for d in date_strings:
        for hospital in HOSPITALS:
            key = (hospital["hospital_name"], d)
            if key in existing_keys:
                continue  # duplicate protection: never regenerate/overwrite
            records.append(generate_record(hospital, d))
            existing_keys.add(key)
    return pd.DataFrame(records, columns=COLUMNS)


# --------------------------------------------------------------------------
# Storage helpers
# --------------------------------------------------------------------------
def load_existing():
    if not os.path.exists(CSV_FILE):
        return None
    # Read as strings so historical values are written back byte-for-byte.
    df = pd.read_csv(CSV_FILE, dtype=str, keep_default_na=False)
    if list(df.columns) != COLUMNS:
        raise SystemExit(
            f"Existing CSV has a different column layout:\n  {CSV_FILE}\n"
            "Move or rename it, then run again to build a fresh dataset.")
    return df


def date_range(start, end):
    """Inclusive list of date strings from start to end."""
    return [(start + timedelta(days=i)).strftime(DATE_FORMAT)
            for i in range((end - start).days + 1)]


def to_string_frame(df):
    """Normalise a frame to the exact string form used in the CSV."""
    out = df.copy()
    for col in ("bed_occupancy_rate", "vaccination_coverage"):
        out[col] = out[col].map(lambda v: f"{float(v):.2f}")
    for col in ("latitude", "longitude"):
        out[col] = out[col].map(lambda v: f"{float(v):.4f}")
    return out.astype(str)


def save(df):
    df = df.sort_values(["date", "hospital_name"]).reset_index(drop=True)
    tmp = CSV_FILE + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, CSV_FILE)  # atomic write so a crash never corrupts history
    return df


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
def validate(df, original=None):
    errors = []

    if list(df.columns) != COLUMNS:
        raise ValueError(f"Column mismatch: {list(df.columns)}")

    if (df == "").any().any() or df.isna().any().any():
        errors.append("Missing values found.")

    if df.duplicated(subset=["hospital_name", "date"]).any():
        errors.append("Duplicate hospital/date combinations found.")

    try:
        dates = pd.to_datetime(df["date"], format=DATE_FORMAT, errors="raise")
        expected_days = (dates.max() - dates.min()).days + 1
        if dates.nunique() != expected_days:
            errors.append("Gaps found in the date sequence.")
    except Exception as exc:
        errors.append(f"Invalid date(s): {exc}")

    counts = df.groupby("date")["hospital_name"].nunique()
    bad = counts[counts != len(HOSPITALS)]
    if not bad.empty:
        errors.append(f"Dates without exactly {len(HOSPITALS)} hospitals: {list(bad.index)}")

    unknown = set(df["hospital_name"]) - set(HOSPITAL_LOOKUP)
    if unknown:
        errors.append(f"Unknown hospital names: {unknown}")

    for name, grp in df.groupby("hospital_name"):
        master = HOSPITAL_LOOKUP.get(name)
        if master is None:
            continue
        for field in MASTER_FIELDS:
            expected = master[field]
            if isinstance(expected, float):
                expected = f"{expected:.4f}"
            if (grp[field] != expected).any():
                errors.append(f"Master field '{field}' changed for {name}")

    int_cols = ["total_beds", "occupied_beds", "outpatient_count", "emergency_cases",
                "disease_cases", "doctor_count", "nurse_count"]
    num = df[int_cols + ["bed_occupancy_rate", "vaccination_coverage"]].apply(
        pd.to_numeric, errors="coerce")
    if num.isna().any().any():
        errors.append("Non-numeric values in numeric columns.")
    if (num < 0).any().any():
        errors.append("Negative values found.")
    if (num["occupied_beds"] > num["total_beds"]).any():
        errors.append("occupied_beds exceeds total_beds.")
    expected_rate = (num["occupied_beds"] / num["total_beds"] * 100).round(2)
    if ((expected_rate - num["bed_occupancy_rate"]).abs() > 0.005).any():
        errors.append("bed_occupancy_rate incorrectly calculated.")
    if ((num["vaccination_coverage"] < 0) | (num["vaccination_coverage"] > 100)).any():
        errors.append("vaccination_coverage outside 0-100.")

    if not df["medicine_availability"].isin(MEDICINE_VALUES).all():
        errors.append("Invalid medicine_availability values.")
    if not df["ambulance_available"].isin(AMBULANCE_VALUES).all():
        errors.append("Invalid ambulance_available values.")
    if not df["alert_level"].isin(ALERT_LEVELS).all():
        errors.append("Invalid alert_level values.")

    # Historical integrity: every original row must survive unchanged.
    if original is not None and not original.empty:
        key = ["hospital_name", "date"]
        merged = original.merge(df, on=key, how="left", suffixes=("_old", "_new"),
                                indicator=True)
        if (merged["_merge"] != "both").any():
            errors.append("Historical records were removed.")
        for col in COLUMNS:
            if col not in key and (merged[f"{col}_old"] != merged[f"{col}_new"]).any():
                errors.append(f"Historical values modified in column '{col}'.")

    if errors:
        raise ValueError("Validation failed:\n  - " + "\n  - ".join(errors))


# --------------------------------------------------------------------------
# Display
# --------------------------------------------------------------------------
def display(df, date_str):
    subset = df[df["date"] == date_str]
    cols = ["hospital_name", "taluk", "total_beds", "occupied_beds", "bed_occupancy_rate",
            "outpatient_count", "emergency_cases", "disease_type", "disease_cases",
            "vaccination_coverage", "medicine_availability", "doctor_count",
            "nurse_count", "ambulance_available", "alert_level", "health_alert"]
    print(f"\n--- Hospital data for {date_str} ({len(subset)} records) ---")
    with pd.option_context("display.max_columns", None, "display.width", 300,
                           "display.max_colwidth", 48):
        print(subset[cols].to_string(index=False))
    print(f"\nDataset: {df['date'].min()} to {df['date'].max()} | "
          f"{df['date'].nunique()} days | {len(df)} records")
    print(f"CSV location: {CSV_FILE}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    today = date.today()  # used ONLY to decide which dates to generate
    today_str = today.strftime(DATE_FORMAT)
    window_start = today - timedelta(days=HISTORY_DAYS - 1)
    existing = load_existing()

    # CASE 1: first ever run -> build the initial history window.
    if existing is None or existing.empty:
        dates = date_range(window_start, today)
        print("No existing dataset found.")
        print(f"Generating {len(dates)} days of synthetic hospital data...")
        print(f"Date range: {dates[0]} to {dates[-1]}")
        new_df = to_string_frame(generate_for_dates(dates, set()))
        validate(new_df)
        final = save(new_df)
        print(f"Generated {len(new_df)} records.")
        print("Dataset saved successfully.")
        display(final, today_str)
        return

    stored_dates = set(existing["date"])
    latest_str = max(stored_dates)
    latest = datetime.strptime(latest_str, DATE_FORMAT).date()
    window_start_str = window_start.strftime(DATE_FORMAT)

    # Rolling window: dates older than the window are dropped, and every date
    # inside the window that is not stored yet is generated.
    expired = sorted(d for d in stored_dates if d < window_start_str)
    kept = existing[existing["date"] >= window_start_str]
    required = date_range(window_start, max(today, latest))
    missing = [d for d in required if d not in stored_dates]

    # CASE 2: nothing missing and nothing expired.
    if not missing and not expired:
        print("Dataset already contains today's data.")
        print("No new data generated.")
        print(f"Displaying existing data for {today_str}.")
        display(existing, today_str)
        return

    # CASE 3: missing dates to add and/or expired dates to drop.
    print(f"Existing latest date: {latest_str}")
    print(f"Today's date: {today_str}\n")
    if not missing:
        print("No new dates to generate.")
    elif len(missing) == 1:
        print("Generating data for:")
        print(missing[0])
    else:
        print(f"Missing dates detected: {len(missing)}")
        if len(missing) <= 10:
            print("\n".join(missing))
        else:
            print(f"{missing[0]} ... {missing[-1]}")
        print("\nGenerating missing daily records...")

    existing_keys = set(zip(kept["hospital_name"], kept["date"]))
    new_df = to_string_frame(generate_for_dates(missing, existing_keys))
    combined = pd.concat([kept, new_df], ignore_index=True)[COLUMNS]
    # Rows inside the window must survive unchanged; only expired rows go.
    validate(combined, original=kept)
    final = save(combined)

    if missing:
        print(f"\n{len(new_df)} {'new ' if len(missing) == 1 else ''}records added.")
    if expired:
        span = expired[0] if len(expired) == 1 else f"{expired[0]} to {expired[-1]}"
        print(f"Rolling {HISTORY_DAYS}-day window: removed {len(expired)} expired "
              f"day(s) ({span}), {len(existing) - len(kept)} records.")
    print("Dataset updated successfully.")
    display(final, today_str)


if __name__ == "__main__":
    main()
