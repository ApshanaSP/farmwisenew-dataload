"""
Shared settings for the AWS setup scripts: team prefix, region, and a boto3 session built from the keys that
`npm run ai:key` saved in chennai-grievance-portal-main/.env (temporary Builder-role keys from the AWS access portal).

This PC's antivirus / proxy re-signs HTTPS, so the Windows certificate store is used through truststore
(verification stays on).
"""
from __future__ import annotations

from pathlib import Path

try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover
    pass

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
TEAM = "fai-tce-team37"
REGION = "ap-south-1"
LAMBDA_ROLE = "FAI-TCE-LambdaExecutionRole"

BUCKETS = {"raw": f"{TEAM}-raw", "uploads": f"{TEAM}-uploads", "exports": f"{TEAM}-exports"}
TABLES = {"intel": f"{TEAM}-intel", "portal": f"{TEAM}-portal", "summary": f"{TEAM}-summary", "user": f"{TEAM}-user"}
STREAM_TABLES = {"intel", "portal"}  # new rows here will trigger the AI-tagging Lambda later
API_URL = "https://i6q6oi20lb.execute-api.ap-south-1.amazonaws.com"  # fai-tce-team37-api

# source -> (collector script, folder holding its CSVs on the PC, what it collects)
COLLECTORS = {
    "imd": (ROOT / "imd_weather_collector" / "imd_weather_collector.py", ROOT / "imd_weather_collector" / "data",
            "IMD weather observations, forecasts and warnings"),
    "cpcb": (ROOT / "cpcb_air_quality_collector" / "cpcb_air_quality_collector.py", ROOT / "cpcb_air_quality_collector" / "data",
             "CPCB air quality (AQI, pollutants)"),
    "cfm": (ROOT / "cfm_dss_collector" / "cfm_dss_collector.py", ROOT / "cfm_dss_collector" / "data",
            "CFM-DSS flood gauges, gate operations and alerts"),
    "pwd": (ROOT / "pwd_dataset_generator" / "generate_pwd_data.py", ROOT / "pwd_dataset_generator" / "data" / "pwd",
            "PWD lakes, incidents, tasks and works (synthetic)"),
    "hospital": (ROOT / "chennai_hospital_data" / "chennai_hospital_data_generator.py", ROOT / "chennai_hospital_data",
                 "Hospital beds, cases and alerts (synthetic)"),
    "news": (ROOT / "chennai_news_pipeline" / "run_pipeline.py", ROOT / "chennai_news_pipeline" / "data" / "processed",
             "Chennai news (RSS + article text, department and complaint tags)"),
    # Node.js: generated on the PC (npm.cmd run generate -- --refresh --mode=csv), sent with aws/push.py police
    "police": (None, ROOT / "police_dataset_generator" / "output", "Police incident reports and stations (synthetic)"),
}


def session():
    """boto3 is imported here, so push.py and export_intel.py run on a PC without it."""
    import boto3

    load_dotenv(ROOT / "chennai-grievance-portal-main" / ".env", override=True)
    return boto3.Session(region_name=REGION)
