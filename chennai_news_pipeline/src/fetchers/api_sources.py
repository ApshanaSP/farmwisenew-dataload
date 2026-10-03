"""Optional NewsAPI / GNews connectors. Skipped silently when no key is configured."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Callable

import requests

from src.fetchers.base import SOURCE_TYPE_API, FetchReport, make_raw_item
from src.http_client import HttpClient
from src.logging_setup import get_logger

log = get_logger("api")


def _since(mode: str, run_cfg: dict[str, Any]) -> datetime:
    hours = int(run_cfg["backfill_days"]) * 24 if mode == "backfill" else int(run_cfg["daily_lookback_hours"])
    return datetime.now(timezone.utc) - timedelta(hours=hours)


def _newsapi_request(client: HttpClient, cfg: dict[str, Any], key: str, query: str, since: datetime) -> tuple[str, list[dict]]:
    params = {
        "q": query,
        "from": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "language": cfg["language"],
        "pageSize": cfg["page_size"],
        "sortBy": "publishedAt",
    }
    resp = client.get(cfg["endpoint"], params=params, headers={"X-Api-Key": key})
    return resp.url, resp.json().get("articles", [])


def _gnews_request(client: HttpClient, cfg: dict[str, Any], key: str, query: str, since: datetime) -> tuple[str, list[dict]]:
    params = {
        "q": query,
        "from": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lang": cfg["lang"],
        "country": cfg["country"],
        "max": cfg["max"],
        "apikey": key,
    }
    resp = client.get(cfg["endpoint"], params=params)
    # Never persist the key: record the request URL without it.
    return resp.url.replace(key, "***"), resp.json().get("articles", [])


_CONNECTORS: dict[str, Callable[..., tuple[str, list[dict]]]] = {
    "newsapi": _newsapi_request,
    "gnews": _gnews_request,
}


def fetch_api_sources(
    client: HttpClient,
    api_cfg: dict[str, Any],
    env: dict[str, str],
    mode: str,
    run_cfg: dict[str, Any],
    run_date: date,
    tz: tzinfo,
) -> FetchReport:
    """Run each connector whose API key is present in the environment."""
    report = FetchReport()
    since = _since(mode, run_cfg)
    for name, conn_cfg in (api_cfg or {}).items():
        key = (env.get(conn_cfg["env_key"]) or "").strip()
        request_fn = _CONNECTORS.get(name)
        if not key or request_fn is None:
            log.info("API connector %s disabled (no %s in .env)", name, conn_cfg["env_key"])
            continue
        label = f"API {name}"
        for query in conn_cfg.get("queries") or []:
            try:
                feed_url, articles = request_fn(client, conn_cfg, key, query, since)
            except (requests.RequestException, ValueError) as exc:
                log.warning("%s query %r failed: %s", label, query, str(exc).replace(key, "***"))
                report.failures.append(f"{label}: {query}")
                continue
            fetched_at = datetime.now(tz).replace(microsecond=0)
            report.requests_ok += 1
            for art in articles:
                source = art.get("source") or {}
                report.items.append(
                    make_raw_item(
                        run_date=run_date,
                        fetched_at=fetched_at,
                        source_type=SOURCE_TYPE_API,
                        source_name=label,
                        source_feed=feed_url,
                        query_used=query,
                        edition=conn_cfg.get("language") or conn_cfg.get("lang", ""),
                        title=art.get("title"),
                        link=art.get("url"),
                        published=art.get("publishedAt"),
                        summary=art.get("description"),
                        source_title=source.get("name"),
                        source_url=source.get("url", ""),
                        guid=art.get("url"),
                        entry=art,
                    )
                )
                report.per_feed[label] += 1
            log.info("%s %r: %d items", label, query, len(articles))
    return report
