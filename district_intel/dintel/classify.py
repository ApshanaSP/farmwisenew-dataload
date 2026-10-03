"""Category classifier trained on labelled departmental text.

Grievance details (English, Tamil, Tanglish), police descriptions and PWD descriptions
all carry a category from the crosswalk, so together they form a free multilingual
training set. Character n-grams work across scripts without a translation step.
Used for grievances typed "Other" and for news articles the keyword rules could not place.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split

from . import textproc as tp
from .util import log


class CategoryModel:
    def __init__(self) -> None:
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, max_features=200_000, sublinear_tf=True)
        self.clf = LogisticRegression(max_iter=3000, C=6.0, class_weight="balanced")
        self.metrics: dict = {}

    def fit(self, events: pd.DataFrame) -> "CategoryModel":
        m = events["source"].isin(["grievance", "police", "pwd"]) & (events["category_code"] != "OTHER") & \
            events["category_method"].astype(str).str.startswith(("crosswalk", "keyword")) & (events["junk_flag"] != 1)
        df = events[m]
        # keep the police share modest so civic categories are not drowned out
        pol = df[df["source"] == "police"]
        df = pd.concat([df[df["source"] != "police"], pol.sample(min(len(pol), 3000), random_state=1)])
        cnt = df["category_code"].value_counts()
        df = df[df["category_code"].isin(cnt[cnt >= 8].index)]
        text = (df["title"].fillna("") + " " + df["text"].fillna("")).map(tp.normalize)
        y = df["category_code"].to_numpy()
        lang = df["lang"].fillna("en").to_numpy()
        src = df["source"].to_numpy()
        Xtr, Xte, ytr, yte, ltr, lte, str_, ste = train_test_split(text, y, lang, src, test_size=0.2, random_state=7, stratify=y)
        self.clf.fit(self.vec.fit_transform(Xtr), ytr)
        pred = self.clf.predict(self.vec.transform(Xte))
        g = ste == "grievance"
        self.metrics = {"macro_f1_all": round(f1_score(yte, pred, average="macro"), 3),
                        "n_train": int(len(ytr)), "n_test": int(len(yte)), "classes": int(len(set(y)))}
        for L in ("en", "ta", "tanglish"):
            sel = g & (lte == L)
            if sel.sum() >= 20:
                self.metrics[f"grievance_macro_f1_{L}"] = round(f1_score(yte[sel], pred[sel], average="macro"), 3)
                self.metrics[f"grievance_n_{L}"] = int(sel.sum())
        # the model evaluated above (80% of the data) is the one used; refitting on 100% doubled the runtime for no measurable gain
        log.info("category model: %s", self.metrics)
        return self

    def predict(self, texts: pd.Series) -> tuple[np.ndarray, np.ndarray]:
        P = self.clf.predict_proba(self.vec.transform(texts.fillna("").map(tp.normalize)))
        best = P.argmax(axis=1)
        return self.clf.classes_[best], P[np.arange(len(best)), best]


def apply(events: pd.DataFrame, docs: pd.DataFrame, model: CategoryModel, min_conf: float = 0.45) -> None:
    """Fill categories in place: grievances typed Other, and incident articles without a keyword category."""
    gm = events["category_method"] == "pending_classifier"
    if gm.any():
        labels, conf = model.predict(events.loc[gm, "title"].fillna("") + " " + events.loc[gm, "text"].fillna(""))
        events.loc[gm, "category_code"] = np.where(conf >= min_conf, labels, "OTHER")
        events.loc[gm, "category_conf"] = conf.round(3)
        events.loc[gm, "category_method"] = "classifier"
    dm = (docs["is_incident"] == 1) & docs["category_code"].isna()
    if dm.any():
        labels, conf = model.predict(docs.loc[dm, "_text"])
        docs.loc[dm, "category_code"] = np.where(conf >= min_conf, labels, "OTHER")
        docs.loc[dm, "category_conf"] = conf.round(3)
        docs.loc[dm, "category_method"] = "classifier"
