"""News pipeline output -> one document per article, story clusters, report type,
incident gate, casualties, place resolution; incident articles also become events.

Reuses the news pipeline's own Tamil-aware gazetteer matcher (loaded from its file),
extended with police and PWD localities. Nothing in the news pipeline is modified.
"""
from __future__ import annotations

import importlib.util
import json
import re
from collections import Counter

import numpy as np
import pandas as pd
import yaml
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from scipy.sparse import csr_matrix, hstack
from sklearn.model_selection import StratifiedKFold

from .. import embed
from .. import severity as sev
from .. import textproc as tp
from ..refdata import Reference
from ..schema import DOC_COLUMNS, EVENT_COLUMNS, conform
from ..util import REF_DIR, Settings, UnionFind, log, mixed_to_ist, sha256_file

NON_INCIDENT_TYPES = ["service_notice", "announcement", "court", "politics", "entertainment_sport", "business"]

PRECISION = {"locality": 1500.0, "zone": 4000.0, "taluk": 4000.0, "district": 15000.0}
GEO_CONF = {"locality": 0.7, "zone": 0.55, "taluk": 0.5, "district": 0.3}
OTHER_DISTRICTS = re.compile(
    r"\b(?:Madurai|Coimbatore|Tiruchi|Trichy|Salem|Tirunelveli|Vellore|Erode|Thoothukudi|Tuticorin|Thanjavur|Dindigul|"
    r"Kanyakumari|Nagapattinam|Cuddalore|Villupuram|Kancheepuram|Tiruvallur|Chengalpattu|Krishnagiri|Dharmapuri|Namakkal|"
    r"Karur|Pudukottai|Ramanathapuram|Sivaganga|Virudhunagar|Theni|Nilgiris|Ooty|Tiruppur|Tiruvannamalai|Ranipet|"
    r"Tirupattur|Kallakurichi|Perambalur|Ariyalur|Mayiladuthurai|Tenkasi|Puducherry|Bengaluru|Hyderabad|Delhi|Mumbai|Kerala|Andhra)\b")

REPORT_TYPE_RULES = [
    # scheduled maintenance outages ("power cut tomorrow: full list of areas") are notices, not incidents
    ("service_notice", re.compile(r"power ?cut (today|tomorrow|on )|power shutdown|outage (list|on )|affected areas|check (timings|full list|affected)|"
                                  r"(நாளை|இன்று).{0,30}மின்(சார)?\s?தடை|மின்\s?தடை ஏற்படும்|மின்தடை.{0,20}(பட்டியல்|பகுதிகள்|இடங்கள்)|பவர் கட்", re.I)),
    ("court", re.compile(r"high court|supreme court|\bbench\b|petition|\bPIL\b|\bHC\b|writ|உயர் நீதிமன்ற|நீதிமன்ற", re.I)),
    ("announcement", re.compile(r"inaugurat|launch|unveil|to be held|will be held|scheme|tender|announced|flagged off|"
                                r"திறந்து வைத்தார்|தொடங்கி வைத்தார்|அறிவிப்பு|அறிவித்த|திட்டம்", re.I)),
    ("politics", re.compile(r"\bDMK\b|AIADMK|\bBJP\b|Congress|\bPMK\b|\bTVK\b|election|candidate|minister said|MLA|"
                            r"தேர்தல்|திமுக|அதிமுக|பாஜக", re.I)),
    ("entertainment_sport", re.compile(r"\bfilm\b|movie|actor|actress|cricket|\bIPL\b|match|tournament|box office|"
                                       r"திரைப்பட|நடிகர்|கிரிக்கெட்", re.I)),
    ("business", re.compile(r"gold rate|gold price|sensex|shares|stock|startup|funding|real estate|தங்கம் விலை", re.I)),
]
CIVIC_COMPLAINT = re.compile(r"residents (complain|allege|demand|say)|woes|apathy|no action|plight|suffer|ordeal|"
                             r"அவதி|அலட்சியம்|புகார்|கோரிக்கை|குற்றச்சாட்டு", re.I)
CRIME = re.compile(r"arrested|police said|FIR|booked|murder|theft|robbery|snatch|கைது|கொலை|திருட்டு", re.I)
STRONG_INCIDENT = re.compile(
    r"waterlog|inundat|flooded|knee-deep|submerged|stagnat|overflow|sewage|cave[ -]?in|caved|sinkhole|collaps|"
    r"fire broke|blaze|gutted|accident|collid|run over|killed|died|injured|electrocut|live wire|power cut|outage|"
    r"garbage|stench|dengue|fever cases|cholera|diarrh|dog bite|stray dog|tree fell|uprooted|road roko|blockade|"
    r"traffic snarl|gridlock|drinking water|water supply|contaminated|pothole|drowned|"
    r"தேங்கி|வெள்ள|மழைநீர்|பள்ளம்|விபத்து|பலி|உயிரிழ|காயம்|தீ விபத்து|இடிந்து|குப்பை|டெங்கு|காய்ச்சல்|மின்தடை|"
    r"மின்சாரம் தாக்கி|சாலை மறியல்|கழிவுநீர்|துர்நாற்றம்|நாய்|மரம் சாய்ந்து|மரம் முறிந்து|குடிநீர்|கொலை|திருட்டு|தீப்பிடித்", re.I)


def _load_gazetteer_module(settings: Settings):
    path = settings.path(settings.raw["sources"]["news"]["package_dir"]) / "src" / "relevance_filter.py"
    spec = importlib.util.spec_from_file_location("news_relevance_filter", path)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod        # dataclasses look their module up here
    spec.loader.exec_module(mod)
    return mod


def build_gazetteer(settings: Settings, extra_places: pd.DataFrame):
    """The news pipeline's gazetteer plus police and PWD localities (English aliases)."""
    rf = _load_gazetteer_module(settings)
    cfg = yaml.safe_load(settings.src("news", "config").read_text(encoding="utf-8"))["gazetteer"]
    entries = []

    def add(item, category):
        p = rf.Place(name=item["name"], category=category, latitude=item.get("latitude"), longitude=item.get("longitude"),
                     requires_context=bool(item.get("requires_context", False)), gcc_zone=item.get("gcc_zone"))
        aliases = list(item.get("aliases_en") or []) + list(item.get("aliases_ta") or [])
        entries.append((p, aliases, list(item.get("ta_block_suffixes") or [])))
    add(cfg["district"], rf.CATEGORY_DISTRICT)
    known = set()
    for key, cat in (("taluks", rf.CATEGORY_TALUK), ("zones", rf.CATEGORY_ZONE), ("localities", rf.CATEGORY_LOCALITY)):
        for item in cfg.get(key) or []:
            add(item, cat)
            known.add(tp.translit_key(item["name"]))
    ambiguous = {"padi", "ramapuram", "nandanam", "george town", "ashok nagar", "k.k. nagar", "kk nagar", "gandhi nagar", "anna nagar east"}
    for r in extra_places.itertuples():
        key = tp.translit_key(r.name)
        if key in known or len(r.name) < 5:
            continue
        known.add(key)
        add({"name": r.name, "latitude": r.lat, "longitude": r.lon, "aliases_en": [r.name],
             "requires_context": r.name.lower() in ambiguous}, rf.CATEGORY_LOCALITY)
    return rf, rf.Gazetteer(entries)


def _publisher_names(n: pd.DataFrame, ref: Reference) -> dict[str, tuple[str, str, float]]:
    out = {}
    for dom, grp in n.groupby("source_domain"):
        tier, rel, name = ref.publisher_tier(dom)
        if not name:
            names = [s for s in grp["source_name"] if isinstance(s, str) and "." not in s and not s.lower().startswith("google news")]
            name = Counter(names).most_common(1)[0][0] if names else dom
        out[dom] = (name, tier, rel)
    return out


def _stories(docs: pd.DataFrame, threshold: float = 0.55, hours: float = 48) -> np.ndarray:
    """Near-duplicate headlines within 48 h -> one story (char n-gram TF-IDF cosine, chunked)."""
    titles = docs["title"].fillna("").map(tp.normalize)
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)
    X = vec.fit_transform(titles)
    t = docs["published_at"].astype("int64").to_numpy() / 3.6e12
    uf = UnionFind(range(len(docs)))
    order = np.argsort(t)
    Xo = X[order]
    to = t[order]
    for start in range(0, len(order), 400):
        stop = min(start + 400, len(order))
        lo = np.searchsorted(to, to[start] - hours)
        hi = np.searchsorted(to, to[stop - 1] + hours, side="right")
        S = (Xo[start:stop] @ Xo[lo:hi].T).toarray()
        ii, jj = np.where(S >= threshold)
        for i, j in zip(ii, jj):
            a, b = start + i, lo + j
            if a < b and abs(to[a] - to[b]) <= hours:
                uf.union(order[a], order[b])
    roots = np.array([uf.find(i) for i in range(len(docs))])
    return roots


def load(settings: Settings, ref: Reference, extra_places: pd.DataFrame) -> dict[str, pd.DataFrame]:
    path = settings.src("news", "master")
    n = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    snap = sha256_file(path)
    n["published_at"] = mixed_to_ist(n["published_at_ist"])
    n["fetched_at"] = mixed_to_ist(n["fetched_at_ist"])
    n["body_extracted"] = n["body_extracted"].astype(str).str.lower().eq("true")
    raw_rows = len(n)

    # ---- 1. one document per canonical URL; keep every feed sighting
    n["_rank"] = (~n["body_extracted"]).astype(int) * 2 + (n["source_type"] == "google_news").astype(int)
    n = n.sort_values(["canonical_url", "_rank", "fetched_at"])
    sightings = n.groupby("canonical_url").apply(
        lambda g: json.dumps(sorted({f"{a}|{b}" for a, b in zip(g["source_type"], g["query_used"].fillna(""))}), ensure_ascii=False),
        include_groups=False)
    counts = n.groupby("canonical_url").size()
    first_fetch = n.groupby("canonical_url")["fetched_at"].min()
    d = n.drop_duplicates("canonical_url").copy()
    d["feed_sightings"] = d["canonical_url"].map(sightings)
    d["sightings_count"] = d["canonical_url"].map(counts)
    d["first_fetched_at"] = d["canonical_url"].map(first_fetch)
    unique_urls = len(d)

    # ---- 2. a Google News copy of an article the publisher's own feed also gave us -> merge into the publisher copy
    d["tkey"] = d["title_normalized"].fillna("") + "|" + d["source_domain"].fillna("")
    pub = d[d["source_type"] != "google_news"].drop_duplicates("tkey").set_index("tkey")
    is_g = d["source_type"] == "google_news"
    has_pub = is_g & d["tkey"].isin(pub.index)
    merged_into = d.loc[has_pub, "tkey"].map(pub["canonical_url"])
    extra = d[has_pub].groupby(merged_into)["sightings_count"].sum()
    d = d[~has_pub].copy()
    d["sightings_count"] = d["sightings_count"] + d["canonical_url"].map(extra).fillna(0).astype(int)
    d["publisher_url"] = np.where(d["source_type"] == "google_news", None, d["canonical_url"])
    d["body_status"] = np.where(d["body_extracted"], "full", np.where(d["source_type"] == "google_news", "snippet_google_news", "snippet"))

    # ---- 3. publishers
    pubs = _publisher_names(d, ref)
    d["publisher"] = d["source_domain"].map(lambda x: pubs.get(x, (x, "regional", 0.6))[0])
    d["publisher_tier"] = d["source_domain"].map(lambda x: pubs.get(x, (x, "regional", 0.6))[1])
    d["reliability"] = d["source_domain"].map(lambda x: pubs.get(x, (x, "regional", 0.6))[2])

    d = d.reset_index(drop=True)
    d["doc_id"] = d["article_id"]
    d["title"] = d["title_clean"].fillna(d["title"])
    d["summary"] = d["summary_clean"].fillna("")
    d["body"] = np.where(d["body_extracted"], d["body_clean"], "")
    text = (d["title"].fillna("") + ". " + d["summary"].fillna("") + ". " + d["body"].fillna("")).str.slice(0, 6000)
    d["lang"] = d["language"]
    d["simhash"] = [tp.simhash64(t) for t in d["title"]]

    # ---- 4. story clusters
    roots = _stories(d)
    d["story_id"] = ["STY-" + d.loc[r, "doc_id"][-10:] for r in roots]
    d = d.sort_values("published_at")
    d["story_role"] = np.where(d.groupby("story_id").cumcount() == 0, "first_report", "follow_up")
    d["outlet_count"] = d.groupby("story_id")["publisher"].transform("nunique")
    d = d.sort_index()

    # ---- 5. report type, statewide datelines
    def rtype(t: str) -> str:
        for name, rx in REPORT_TYPE_RULES:
            if rx.search(t):
                return name
        if CRIME.search(t):
            return "crime"
        if CIVIC_COMPLAINT.search(t):
            return "civic_complaint"
        if STRONG_INCIDENT.search(t):
            return "incident"
        return "other"
    d["report_type"] = [rtype(t) for t in text]
    no_local = d["mentioned_taluks"].fillna("[]").eq("[]") & d["mentioned_localities"].fillna("[]").eq("[]")
    d["statewide_dateline"] = (no_local & text.str.contains(OTHER_DISTRICTS)).astype(int)
    d["is_district"] = (1 - d["statewide_dateline"]).astype(int)

    # ---- 6. incident gate: weak labels + hand labels -> logistic regression on char n-grams + multilingual embeddings
    strong = np.array([len(STRONG_INCIDENT.findall(t)) for t in text])
    neg_type = d["report_type"].isin(NON_INCIDENT_TYPES).to_numpy()
    weak = np.full(len(d), -1)
    weak[(strong >= 2) & ~neg_type & (d["is_district"] == 1).to_numpy()] = 1
    weak[(strong == 0) & neg_type] = 0
    weak[(strong == 0) & (d["report_type"] == "other").to_numpy()] = 0
    weak[(d["report_type"] == "service_notice").to_numpy()] = 0
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=3, max_features=150_000, sublinear_tf=True)
    X = vec.fit_transform(text.map(tp.normalize))
    E = embed.encode((d["title"].fillna("") + ". " + d["summary"].fillna("").str.slice(0, 300)).tolist())
    if E is not None:
        X = hstack([X, csr_matrix(E * 2.0)]).tocsr()
    gold, gate_eval = _gold(d), {}
    gidx = d.index[d["doc_id"].isin(gold.index)].to_numpy()
    gy = d.loc[gidx, "doc_id"].map(gold["label_incident"]).to_numpy().astype(int)
    gw = d.loc[gidx, "doc_id"].map(gold["w"]).to_numpy()
    weak_idx = np.where((weak >= 0) & ~np.isin(np.arange(len(d)), gidx))[0]

    def fit(extra_idx, extra_y, gold_weight=3.0):
        idx = np.concatenate([weak_idx, extra_idx]).astype(int)
        y = np.concatenate([weak[weak_idx], extra_y]).astype(int)
        sw = np.concatenate([np.ones(len(weak_idx)), np.full(len(extra_idx), gold_weight)])
        return LogisticRegression(max_iter=3000, C=2.0, class_weight="balanced").fit(X[idx], y, sample_weight=sw)

    thr = 0.5
    if len(gidx) >= 50:
        cv_weak, cv_mix = np.zeros(len(gidx)), np.zeros(len(gidx))
        base = fit(np.array([], int), np.array([], int))
        for tr, te in StratifiedKFold(5, shuffle=True, random_state=3).split(gidx, gy):
            cv_weak[te] = base.predict_proba(X[gidx[te]])[:, 1]
            cv_mix[te] = fit(gidx[tr], gy[tr]).predict_proba(X[gidx[te]])[:, 1]
        blocked = d.loc[gidx, "report_type"].isin(NON_INCIDENT_TYPES).to_numpy()
        best = max(((t, _wmetrics(cv_mix >= t, gy, gw, blocked)) for t in np.arange(0.2, 0.9, 0.025)), key=lambda x: x[1]["f1"])
        best_w = max(((t, _wmetrics(cv_weak >= t, gy, gw, blocked)) for t in np.arange(0.2, 0.9, 0.025)), key=lambda x: x[1]["f1"])
        thr = float(round(best[0], 3))
        gate_eval = {"gold_labels": int(len(gidx)), "gold_positive": int(gy.sum()), "threshold": thr,
                     "weak_labels_only": {**best_w[1], "threshold": float(round(best_w[0], 3))},
                     "weak_plus_hand_labels_cv": best[1],
                     "features": "char n-grams" + (" + multilingual-e5 embeddings" if E is not None else "")}
        model = fit(gidx, gy)
    else:
        model = fit(np.array([], int), np.array([], int))
    prob = model.predict_proba(X)[:, 1]
    d["incident_conf"] = prob.round(3)
    gate = (prob >= thr) & (d["is_district"] == 1).to_numpy() & ~d["report_type"].isin(NON_INCIDENT_TYPES).to_numpy()
    d["is_incident"] = gate.astype(int)
    d["incident_method"] = f"logreg weak+hand labels (thr={thr:.2f})" if len(gidx) >= 50 else f"logreg weak labels (thr={thr:.2f})"
    log.info("news: %d rows -> %d URLs -> %d documents; incident gate keeps %d (threshold %.2f); eval %s",
             raw_rows, unique_urls, len(d), int(gate.sum()), thr, gate_eval)

    # ---- 7. category by keywords (the classifier fills the rest later)
    kw = []
    for c in ref.cat.values():
        for k in (c.get("keywords_en") or []):
            kw.append((c["code"], re.compile(r"(?<![A-Za-z])" + re.escape(k), re.I), 2 if " " in k else 1))
        for k in (c.get("keywords_ta") or []):
            kw.append((c["code"], re.compile(re.escape(k)), 2))
    cats, confs = [], []
    for t in text:
        score: Counter = Counter()
        head = t[:300]
        for code, rx, w in kw:
            if rx.search(t):
                score[code] += w * (2 if rx.search(head) else 1)
        if score:
            (best, s1), *rest = score.most_common(2) + [(None, 0)]
            s2 = rest[0][1] if rest else 0
            if s1 >= 2 and s1 > s2:
                cats.append(best); confs.append(round(min(0.95, 0.5 + 0.1 * s1 - 0.05 * s2), 2)); continue
        cats.append(None); confs.append(None)
    d["category_code"] = cats
    d["category_conf"] = confs
    d["category_method"] = np.where(pd.Series(cats).notna(), "keywords", None)
    d["dept_src"] = d["department"].map(ref.cmap["news_department"])

    # ---- 8. places (most specific first-mentioned), casualties
    rf, gaz = build_gazetteer(settings, extra_places)
    order = {"locality": 0, "zone": 1, "taluk": 2, "district": 3}
    places, ptxt, lat, lon, level, conf = [], [], [], [], [], []
    for t in text:
        ms = gaz.find_mentions(t)
        named = [m for m in ms if m.place.category != "district"]
        has_anchor = any(not m.place.requires_context for m in ms)
        named = [m for m in named if not m.place.requires_context or has_anchor]
        places.append("|".join(dict.fromkeys(m.place.name for m in named)))
        if named:
            best = sorted(named, key=lambda m: (order[m.place.category], m.start))[0]
            ptxt.append(best.place.name); lat.append(best.place.latitude); lon.append(best.place.longitude)
            level.append(best.place.category); conf.append(GEO_CONF[best.place.category])
        else:
            ptxt.append("Chennai"); lat.append(13.0827); lon.append(80.2707); level.append("district"); conf.append(GEO_CONF["district"])
    d["places"], d["place_text"], d["lat"], d["lon"], d["geo_level"], d["geo_conf"] = places, ptxt, lat, lon, level, conf
    cas = [tp.casualties(t) for t in text]
    d["dead"] = [c[0] for c in cas]
    d["injured"] = [c[1] for c in cas]

    # ---- 7b. stories across languages: embeddings + same category (character n-grams cannot match Tamil to English)
    if E is not None:
        merged = _embed_stories(d, E)
        log.info("news: embedding story merges %s", merged)
        gate_eval["story_merges"] = merged
        d = d.sort_values("published_at")
        d["story_role"] = np.where(d.groupby("story_id").cumcount() == 0, "first_report", "follow_up")
        d["outlet_count"] = d.groupby("story_id")["publisher"].transform("nunique")
        d = d.sort_index()

    d["source_kind"] = "news"
    d["_text"] = text
    return {"documents": d, "raw_rows": raw_rows, "unique_urls": unique_urls, "snapshot": snap, "gate_eval": gate_eval}


def _gold(d: pd.DataFrame) -> pd.DataFrame:
    """Hand labels (reference/labels/news_incident_labels.csv) with stratum weights for unbiased estimates."""
    p = REF_DIR / "labels" / "news_incident_labels.csv"
    if not p.exists():
        return pd.DataFrame(columns=["label_incident", "w"])
    g = pd.read_csv(p, encoding="utf-8-sig")
    n = g.groupby("stratum")["doc_id"].transform("size")
    g["w"] = g["stratum_size"] / n
    return g.drop_duplicates("doc_id").set_index("doc_id")[["label_incident", "w"]]


def _wmetrics(pred: np.ndarray, y: np.ndarray, w: np.ndarray, blocked: np.ndarray) -> dict:
    """Population-weighted precision, recall and F1 (the sample was stratified, so each label carries its stratum weight)."""
    pred = pred & ~blocked
    tp = float((w * (pred & (y == 1))).sum())
    P = tp / max(float((w * pred).sum()), 1e-9)
    R = tp / max(float((w * (y == 1)).sum()), 1e-9)
    return {"precision": round(P, 3), "recall": round(R, 3), "f1": round(2 * P * R / (P + R), 3) if P + R else 0.0}


def _embed_stories(d: pd.DataFrame, E: np.ndarray, hours: float = 24) -> dict:
    """Conservative merges (a wrong merge hides an incident, a missed one only double-counts it):
    same language: cosine >= 0.95, same category, within 24 h;
    English-Tamil: cosine >= 0.90, same non-OTHER category, both incidents, same specific place (not just "Chennai"),
    and casualty counts that do not disagree. Edges are applied strongest first and never grow a story past 6 articles."""
    t = d["published_at"].astype("int64").to_numpy() / 3.6e12
    order = np.argsort(t)
    ts = t[order]
    lang, cat, inc, sid = d["lang"].to_numpy(), d["category_code"].to_numpy(), d["is_incident"].to_numpy(), d["story_id"].to_numpy()
    place, level, dead = d["place_text"].to_numpy(), d["geo_level"].to_numpy(), d["dead"].to_numpy()
    edges = []
    for s0 in range(0, len(order), 500):
        blk = order[s0:s0 + 500]
        lo = np.searchsorted(ts, t[blk].min() - hours)
        hi = np.searchsorted(ts, t[blk].max() + hours, side="right")
        win = order[lo:hi]
        S = E[blk] @ E[win].T
        for i, j in zip(*np.where(S >= 0.90)):
            a, b = blk[i], win[j]
            if a >= b or abs(t[a] - t[b]) > hours or sid[a] == sid[b]:
                continue
            ca, cb = cat[a], cat[b]
            if lang[a] == lang[b]:
                if S[i, j] >= 0.95 and ca == cb:
                    edges.append((float(S[i, j]), a, b, "same"))
            elif ({lang[a], lang[b]} == {"en", "ta"} and inc[a] and inc[b] and isinstance(ca, str) and ca == cb and ca != "OTHER"
                  and level[a] != "district" and place[a] == place[b] and not (dead[a] and dead[b] and dead[a] != dead[b])):
                edges.append((float(S[i, j]), a, b, "cross"))
    uf = UnionFind(d["story_id"].tolist())
    size = d.groupby("story_id").size().to_dict()
    n = {"same": 0, "cross": 0}
    for _, a, b, kind in sorted(edges, reverse=True):
        ra, rb = uf.find(sid[a]), uf.find(sid[b])
        if ra == rb or size.get(ra, 1) + size.get(rb, 1) > 6:
            continue
        r = uf.union(ra, rb)
        size[r] = size.get(ra, 1) + size.get(rb, 1)
        n[kind] += 1
    d["story_id"] = d["story_id"].map(uf.find)
    return {"same_language_merges": n["same"], "english_tamil_merges": n["cross"], "stories_after": int(d["story_id"].nunique())}


def to_events(d: pd.DataFrame, ref: Reference, snap: str | None) -> pd.DataFrame:
    """Incident articles become events (severity capped at High until confirmed)."""
    x = d[(d["is_incident"] == 1) & d["category_code"].notna() & (d["category_code"] != "OTHER")].copy()
    ev = pd.DataFrame({
        "event_id": "NEWS-" + x["doc_id"], "source": "news", "source_record_id": x["doc_id"],
        "deep_link": x["publisher_url"].fillna(x["canonical_url"]), "snapshot_sha256": snap, "is_synthetic": 0, "is_overlay": 0,
        "occurred_at": x["published_at"], "reported_at": x["published_at"], "time_precision": "published",
        "title": x["title"], "text": x["_text"].str.slice(0, 1200), "lang": x["lang"], "junk_flag": 0, "simhash": x["simhash"],
        "category_src": x["report_type"], "category_code": x["category_code"], "category_conf": x["category_conf"],
        "category_method": x["category_method"], "dept_src": x["department"], "is_actionable": 1,
        "lat": x["lat"], "lon": x["lon"], "loc_precision_m": x["geo_level"].map(PRECISION), "place_text": x["place_text"],
        "geo_level": x["geo_level"], "geo_method": "gazetteer", "geo_conf": x["geo_conf"],
        "dead": x["dead"], "injured": x["injured"],
        "vulnerable_flags": ["|".join(tp.vulnerable_flags(t)) for t in x["_text"]],
        "hazard_flag": [int(tp.hazard(t)) for t in x["_text"]],
        "status_std": "Open", "status_src": "reported", "status_at": x["published_at"], "response_applicable": 0,
        "channel": "media", "source_reliability": x["reliability"], "dup_group_id": "NEWS-" + x["story_id"],
        "ext_ref": x["story_id"], "ext_ref_status": "story",
    })
    ev["lead_dept"] = ev["category_code"].map(lambda c: ref.cat.get(c, ref.cat["OTHER"])["lead"])
    ev = sev.apply(ev, lambda r: sev.from_category(r, ref, cap_unconfirmed=True))
    return conform(ev, EVENT_COLUMNS)
