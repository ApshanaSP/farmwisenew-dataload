# Chennai District News Dataset Pipeline

Collects, cleans, and structures news about **Chennai district, Tamil Nadu** into a dataset with many descriptive columns. It is built for the Collector's District Intelligence Platform (Next.js 14 + MySQL).

**Scope.** This code fetches, cleans, filters by Chennai relevance, extracts neutral features, and tags each article with one of 15 **departments** and a **complaint flag** (section 5). It does **not** do any of the following:

- deduplication
- scoring or ranking
- clustering
- sentiment analysis
- alerts
- summarization

Duplicate articles are kept on purpose. The same story fetched from two feeds becomes two rows with two different `article_id`s.

---

## 1. Setup

```bash
cd chennai_news_pipeline
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate        # Linux / macOS
pip install -r requirements.txt
copy .env.example .env             # optional - only for API keys / MySQL
python -m pytest -q                # 88 tests
python run_pipeline.py
```

No API keys are needed. Google News RSS and the publisher feeds work without any key.

**HTTPS behind antivirus or a proxy.** Some antivirus products inspect HTTPS traffic and re-sign it with their own root certificate; Avast is one example. Windows trusts that root, but Python's default CA bundle does not, so every request fails with `CERTIFICATE_VERIFY_FAILED`.

The setting `http.use_system_trust_store: true` (the default) fixes this. It uses the `truststore` package to verify certificates against the operating-system certificate store.

## 2. Running

| Command | What it does |
|---|---|
| `python run_pipeline.py` | The first run does a **90-day backfill** (`run.backfill_days`). Later runs fetch the **last 48 hours** (`when:2d`). |
| `python run_pipeline.py --backfill` | Forces a 90-day backfill (about 1 hour). Replaces that day's rows. |
| `python run_pipeline.py --no-extract` | Skips article-page downloads, so only RSS summaries are used. |
| `python run_pipeline.py --no-db` | Skips MySQL even when it is configured. |
| `python run_pipeline.py --reprocess [YYYY-MM-DD]` | Rebuilds processed, rejected, and master rows from a saved raw JSON (today's by default, or the given run date), without re-fetching. Use it after editing the gazetteer, exclusions, or classification keywords. |

The pipeline records whether the backfill has finished in `data/state/pipeline_state.json`. Delete that file to backfill again automatically.

**Rolling 90-day window.** `master_news.csv` keeps only the last 90 days (`run.master_retention_days`). On every run, rows whose article date is more than 90 days before the run date are dropped. The article date is `published_date`, or the fetch date when there is none. The dated per-run files in `data/raw` and `data/processed` are never trimmed, so older data can still be found there. Set the value to `0` to keep everything.

**Re-running on the same day** overwrites that day's files. It also replaces that day's rows in `master_news.csv` and in MySQL. Rows are matched by the `YYYYMMDD-` prefix of `article_id`. This is file handling only; rows inside a run are never de-duplicated.

### Daily schedule - Windows Task Scheduler

`scripts\run_daily.bat` uses `.venv` when it exists and falls back to `python` on PATH otherwise.

Run this from an **Administrator** command prompt. It creates a task that runs every day at 06:30:

```bat
schtasks /Create /TN "ChennaiNewsPipeline" /SC DAILY /ST 06:30 ^
  /TR "\"D:\farmwisenew\chennai_news_pipeline\scripts\run_daily.bat\"" /RL LIMITED /F
```

To set it up in the GUI instead: open Task Scheduler, choose **Create Basic Task**, set the trigger to **Daily**, choose the action **Start a program**, and set **Program** to the full path of `run_daily.bat`. Also turn on **Run whether user is logged on or not** and **Run task as soon as possible after a scheduled start is missed**.

Run once now to test:

```bat
schtasks /Run /TN "ChennaiNewsPipeline"
```

### Daily schedule - cron (Linux / macOS)

```bash
chmod +x scripts/run_daily.sh
crontab -e
# every day at 06:30 IST (set CRON_TZ if the server is not on IST)
CRON_TZ=Asia/Kolkata
30 6 * * * /path/to/chennai_news_pipeline/scripts/run_daily.sh >> /path/to/chennai_news_pipeline/logs/cron.log 2>&1
```

## 3. Sources

Every source is configured in `config.yaml`. If a source fails, the failure is logged and the run continues.

| Source | Type | Notes |
|---|---|---|
| Google News RSS search, English (`en-IN`) and Tamil (`ta`) editions | `google_news` | Covers the core queries, 19 topic queries, and one query per taluk, GCC zone, and locality: about 150 queries. Daily runs use `when:2d`. The backfill uses `after:/before:` windows of `backfill_window_days`. |
| The Hindu - Chennai | `rss` | `thehindu.com/news/cities/chennai/feeder/default.rss` |
| Times of India - Chennai | `rss` | `rssfeeds/2950623.cms` |
| DT Next - Chennai | `rss` | `api/v1/collections/newschennai-news.rss` |
| New Indian Express - Chennai | `rss` | `cities/chennai/rssfeed/?id=182` |
| Dinamani (Tamil) | `rss` | Dinamani has no Chennai-only feed, so the pipeline reads its all-stories feed and the Chennai filter narrows it. |
| Dinamalar (Tamil) | `sitemap` | Dinamalar has no RSS feed, so the pipeline reads its "latest news" sitemap. Only `/news/` URLs count as articles. |
| NewsAPI and GNews | `api` | Enabled only when `NEWSAPI_KEY` or `GNEWS_API_KEY` is set in `.env`. |

All feed URLs were checked on 2026-09-24. Publishers change feed URLs from time to time. When a feed breaks, the run summary lists it under *Source failures*.

### Full article text and robots.txt

For every item that has a publisher URL, the pipeline downloads the article page and extracts the text with **trafilatura**. The download happens only when that site's `robots.txt` allows it for the token `ChennaiDistrictNewsDataset`. Requests are rate-limited per domain (1 request per second by default, or the site's `Crawl-delay` if it sets one) and are retried with backoff. If extraction fails, the RSS summary is kept and `body_extracted = False`.

### Google News links

Google News RSS items link to `news.google.com/rss/articles/<id>`. Older IDs contain the publisher URL and are **decoded offline**, without any request. Current IDs (`AU_yqL…`) are opaque, so resolving them needs requests to `news.google.com/articles/…` and Google's internal API. **`news.google.com/robots.txt` disallows those paths**, so the pipeline does not resolve them.

For those rows:

- `canonical_url` is the Google link with tracking parameters removed.
- `source_domain` is the publisher's domain, taken from the RSS `<source>` element.
- `body_extracted = False`.

The RSS feeds themselves are fetched because they are a public feed endpoint that you configured on purpose, even though the same `robots.txt` also lists `/rss`. Set `google_news.enabled: false` if your policy needs strict robots compliance for Google.

## 4. Chennai relevance filter

This filter is the only filtering step. An item is **kept** when its title, summary, or body mentions Chennai or a gazetteer place (`config.yaml → gazetteer`):

- **District names:** Chennai, Madras, Greater Chennai Corporation, CMWSSB, and the Tamil forms சென்னை and மெட்ராஸ்.
- **17 revenue taluks:** the list was checked against the official Chennai district website ([chennai.nic.in, Revenue Administration](https://chennai.nic.in/revenue-administration/)). The official list has **Kolathur**, which the original brief left out.
- **The 15 Greater Chennai Corporation zones.** Zones named like a taluk are covered by the taluk entry.
- **About 50 major localities**, with English and Tamil spellings.

Matching rules:

- **English** matches whole words and ignores case. Dots, spaces, and hyphens are interchangeable, so `T.Nagar` matches `T. Nagar` and `T Nagar`.
- **Tamil** matches must start a word, but case suffixes are allowed: `சென்னையில்` matches. A name ending in pulli (`்`) also matches its inflected forms, so `மயிலாப்பூரில்` matches Mylapore.
- **`requires_context: true`** is set on places whose names often refer to somewhere else: Ashok Nagar (Delhi), K.K. Nagar (Madurai and Trichy), Ramapuram, Padi, Nandanam, and George Town (Penang). These places are listed in the location columns, but on their own they do not make an article relevant. Kolathur, Anna Nagar, Adyar, Perungudi, and Manali were checked against the first 30-day run. They turned out to be overwhelmingly Chennai stories, so they are not flagged. Himachal's Manali is handled through exclusion phrases (`Kullu-Manali` and similar).
- **`exclusion_phrases`** are removed from the text before matching. Examples are *Chennai Super Kings* and *Madurai Bench of Madras High Court*.

Rejected items go to `rejected_YYYY-MM-DD.csv` with one of these reasons:

- `no_gazetteer_mention`
- `only_excluded_phrases`
- `only_context_dependent_places`
- `processing_error`

Many statewide stories carry a "CHENNAI:" dateline, and Madras High Court cases often concern other districts. Both are kept, because they do mention Chennai. Separating them out is a job for your later relevance or classification algorithms.

## 5. Department and complaint classification

Every Chennai-relevant article gets exactly one department from this list (`config.yaml → classification.departments`):

Revenue Department · Engineering Department (Town Planning & Building Permissions) · Electrical Department · Solid Waste Management Department · Storm Water Drain Department · Bridges Department · Health Department · Family Welfare Department · Education Department · Parks & Play Fields Department · Buildings Department · Mechanical Engineering Department · Land & Estate Department · General Administration · Financial Management Unit

**How the department is chosen** (the first layer that gives an answer wins):

1. **LLM, optional.** When `OPENAI_API_KEY` is set in `.env`, `gpt-4o-mini` classifies the title, summary, and body in batches of 20. This is the same provider as the dashboard's `ai-classifier.ts`. The prompt lists the 15 departments with descriptions. Any answer that is not one of those names is discarded, and that row falls through to the next layer. Answers are cached in `data/state/classification_cache.json`, so re-runs are not billed twice.
2. **Keyword model (always available, offline).** Each department has weighted English and Tamil terms. A department is eligible only when the text contains **one strong term, or at least two different weak terms**, so one generic word ("road", "hospital") never decides it. Terms in the title count double. The highest score wins; ties go to the department listed first. Matching rules:
   - English terms also match plural and past forms (`pothole` matches `potholes`).
   - Short acronyms match case-sensitively, so `ROB` does not match "rob".
   - Tamil terms allow case endings and sandhi consonants (`காலை உணவுத் திட்டம்` matches `காலை உணவு திட்டம்`).
   - `ignore_phrases` masks names that only contain a department word, such as பள்ளிக்கரணை, which starts with பள்ளி ("school").
3. **Embedding model (off by default).** Zero-shot similarity with a locally cached `all-MiniLM-L6-v2` model, for English rows with no keyword hit. It was tested on the 90-day data and then disabled: it forced crime, price, and politics headlines into departments at low similarity, for example "Electrician hacked to death" became Electrical Department.
4. **Fallback.** Anything left, such as crime, politics, cinema, or markets, gets `General Administration` with `department_method = fallback`. Filter on that column to separate real General Administration news from unplaced news.

**Complaint flag** (`is_complaint`) marks news that reports a civic problem or grievance affecting the public. The score is:

- strong grievance cues: +2 (for example "residents complain", "woes", "apathy", "Citizen Connect", அவதி, அலட்சியம்)
- weak grievance cues: +1 (for example "protest", "complaint", புகார், கோரிக்கை)
- problem cues: +1 (for example "overflowing", "potholed", "power cut", தேங்கி, துர்நாற்றம்)
- negative cues: −2 (for example "inaugurated", "arrested", "police", "to face … outage", "full list", அறிவிப்பு, திறந்து வைத்தார்)

A row is a complaint when the score is at least 2 and it has at least one grievance or problem cue. The matched cues are saved in `complaint_cues` for review. With an OpenAI key, the LLM decides instead (`complaint_method = llm`).

**Water supply.** Water supply and sewerage belong to Metrowater (CMWSSB), which is not in the list. Sewage overflow and drainage go to Storm Water Drain. Pure drinking-water supply stories without other cues fall back to General Administration.

To tune the classifier, edit the terms in `config.yaml`, then run `python run_pipeline.py --reprocess <run date>`.

## 6. Outputs per run

```
data/raw/raw_news_YYYY-MM-DD.csv / .json       every fetched item, unfiltered
data/processed/processed_news_YYYY-MM-DD.csv   Chennai-relevant items, duplicates kept
data/processed/rejected_YYYY-MM-DD.csv         items dropped by the filter + reason
data/processed/master_news.csv                 all processed rows across days
logs/pipeline_YYYY-MM-DD.log                   full log (appended per run)
```

All CSVs are written as **UTF-8 with BOM** (`utf-8-sig`), so Excel shows Tamil text correctly. List columns are stored as JSON strings such as `["Velachery", "Guindy"]`. Tamil text in these strings is kept as-is, not escaped.

### MySQL (optional)

Set `DB_HOST`, `DB_USER`, `DB_PASSWORD`, and `DB_NAME` in `.env`. These are the same variable names the dashboard uses. Each run then:

1. Applies `schema.sql`, which only uses `CREATE TABLE IF NOT EXISTS`.
2. Replaces the day's rows in `news_raw` and `news_processed` inside one transaction.

`news_processed` has indexes on `published_at_ist`, `source_domain`, `first_mentioned_place`, `fetched_at_ist`, and `canonical_url(255)`. Datetimes are stored as IST wall-clock time.

## 7. Column reference

### Raw file (`raw_news_*.csv` / `.json`) - as received

| Column | Description |
|---|---|
| article_id | Unique ID per fetched row (`YYYYMMDD-<16 hex>`). Same ID as in the processed and rejected files. |
| fetched_at | IST timestamp of the request that returned the item. |
| source_type | `google_news`, `rss`, `sitemap`, or `api`. |
| source_name | The feed label (for example `Google News [ta]` or `The Hindu`). |
| source_feed | The exact URL requested. API keys are masked. |
| query_used | Google or API query string. Empty for RSS and sitemap rows. |
| edition | Language edition of the feed or query (`en` / `ta`). |
| title, link, published, summary, guid | Fields exactly as the feed delivered them. |
| source_title, source_url | Publisher name and URL (Google `<source>`, API `source`, or the feed's own). |
| entry_json | The complete feed entry or API record as JSON. In the `.json` file it is expanded into an object. |

### Processed file (`processed_news_*.csv`, `master_news.csv`)

| Column | Type | Description |
|---|---|---|
| article_id | str | Unique per fetched row, **not** a content hash. Identical articles from different sources get different IDs. |
| fetched_at_ist | ISO datetime | When the item was fetched (Asia/Kolkata). |
| published_at_ist | ISO datetime | Publication time converted to IST. Empty if the source gave none. |
| published_date | date | `YYYY-MM-DD` of `published_at_ist`. |
| published_hour | int 0-23 | Hour of `published_at_ist`. |
| day_of_week | str | `Monday` … `Sunday`. |
| age_hours | float | `fetched_at_ist − published_at_ist` in hours (2 decimals). |
| title | str | Title exactly as received. |
| title_clean | str | HTML and entities removed, unicode (NFKC) and whitespace normalized. For Google rows the " - Publisher" suffix is removed. |
| title_normalized | str | `title_clean` lower-cased, with punctuation and symbols removed. Tamil vowel signs are kept. Meant for later matching. |
| summary_clean | str | Cleaned RSS or API summary. For Google rows the trailing publisher name is removed. |
| body_clean | str | Cleaned article text from trafilatura. When extraction failed, this is the same as `summary_clean`. |
| full_text | str | `title_clean` + newline + `body_clean`. |
| body_extracted | bool | `True` when `body_clean` came from the article page. |
| word_count | int | Whitespace-separated tokens in `full_text`. |
| char_count | int | Characters in `full_text`. |
| language | str | `ta` if at least 30% of letters are Tamil script. Otherwise `other` for non-Latin scripts. Latin-script text is `en` unless langdetect is at least 95% sure it is another language on a text of 120+ characters. |
| has_tamil_text | bool | `full_text` contains any Tamil-script character. |
| url | str | Link as received (may be a Google News link). |
| canonical_url | str | Tracking parameters (`utm_*`, `fbclid`, `oc`, …) and fragment removed, host lower-cased, query sorted. Old-style Google links are decoded. |
| source_name | str | The publisher: Google/API `<source>` name, or the feed name for publisher RSS. |
| source_domain | str | Publisher domain without `www.`. |
| source_type | str | `google_news`, `rss`, `sitemap`, or `api`. |
| query_used | str | Query that returned the row. Empty for RSS and sitemap rows. |
| mentioned_taluks | JSON list | Every taluk mentioned, in order of first appearance. |
| mentioned_localities | JSON list | Every GCC zone or locality mentioned, in order of first appearance. |
| first_mentioned_place | str | First gazetteer place in title → summary → body. This can be `Chennai` itself (`relevance.first_place_includes_district`). |
| latitude, longitude | float | Approximate centroid of `first_mentioned_place`, from config. |
| department | str | One of the 15 departments (section 5). |
| department_method | str | `llm`, `keyword`, `embedding`, or `fallback` (no department matched; set to General Administration). |
| department_confidence | float 0-1 | Keyword: the winning department's share of all department scores. Embedding: cosine similarity. LLM: 1.0. Fallback: 0. |
| department_evidence | JSON list | Keyword terms that matched for the chosen department. |
| is_complaint | bool | The article reports a civic problem or grievance. |
| complaint_score | float | Rule score (grievance + problem − negative cues). 0 when the LLM decided. |
| complaint_cues | JSON list | Cues that matched. Negative cues are prefixed with `-`. |
| complaint_method | str | `rules` or `llm`. |

Places are searched in the title, the summary, and the body, after exclusion phrases are removed. Nothing is chosen, ranked, or scored.

### Rejected file (`rejected_*.csv`)

`article_id, fetched_at_ist, published_at_ist, title, url, canonical_url, source_name, source_domain, source_type, query_used, language, rejection_reason, excluded_phrases_found, context_only_places_found`

## 8. Project layout

```
config.yaml              queries, feeds, gazetteer, exclusions, all tunables
.env.example             optional API keys + MySQL
schema.sql               news_raw / news_processed
run_pipeline.py          entry point + run summary
scripts/run_daily.bat|sh schedulers
src/
  config.py              config + .env loading
  http_client.py         session, retries/backoff, per-domain rate limit, robots.txt
  fetchers/
    google_news.py       Google News RSS (en + ta, daily/backfill windows)
    rss_feeds.py         publisher RSS + news sitemaps
    api_sources.py       NewsAPI / GNews (optional)
    article_extractor.py trafilatura full text
    base.py              raw row structure
  cleaning.py            HTML/unicode/whitespace, dates → IST, URL canonicalisation
  relevance_filter.py    gazetteer matching + Chennai filter + location features
  features.py            language, text, time features; column order
  classification.py      department (LLM / keyword / embedding) + complaint flag
  processing.py          raw row → processed/rejected row
  storage.py             CSV/JSON/master/MySQL writers, state file
  logging_setup.py       logs/pipeline_YYYY-MM-DD.log
tests/                   pytest: cleaning, URL canonicalisation, Chennai filter, classification
```

## 9. Customising

- **Add a locality:** add an entry under `gazetteer.localities` with `aliases_en`, `aliases_ta`, `latitude`, and `longitude`. It becomes a Google News query automatically.
- **Add a feed:** append an entry to `rss_feeds`.
- **Change the daily window:** edit `run.daily_lookback` (Google) and `run.daily_lookback_hours` (APIs).
- **Wrong matches:** add phrases to `relevance.exclusion_phrases`, or set `requires_context: true` on the place. Check `rejected_*.csv` after changes.
- **Coordinates:** these are approximate centroids for map placement. Refine them in `config.yaml`.
