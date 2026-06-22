#!/usr/bin/env python3
"""Auto-canonicalize: fold the CONFIDENT 'name alias' rows from the sheet's "Needs Review"
tab into registry/manual_aliases.json, so they become deterministic AND propagate to the
draft + auction (which read the committed registry, not the sheet).

Only 'name alias' rows are folded (the bot fuzzy-matched them with confidence and gave a
suggestion). 'not in squad' rows are LEFT for human judgment. After running, re-run
build_registry.py and commit.

Run:  python3 registry/fold_review_aliases.py        (reads the live "Needs Review" tab)
"""
import os, re, json, csv, io, urllib.request, unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
SHEET_ID = os.environ.get("GSHEET_ID", "1um6Scv2MbFzRxTVUJsWxMxX4oxTDojJmujcFBJGDlyg")
REVIEW_TAB = os.environ.get("REVIEW_TAB", "Needs Review")
MANUAL = os.path.join(HERE, "manual_aliases.json")

def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()

url = (f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
       f"?tqx=out:csv&sheet={urllib.parse.quote(REVIEW_TAB)}&headers=1")
rows = list(csv.reader(io.StringIO(urllib.request.urlopen(url, timeout=30).read().decode())))
hdr = [c.strip() for c in rows[0]]; ti = {c: i for i, c in enumerate(hdr)}
def g(r, k): i = ti.get(k); return (r[i].strip() if i is not None and i < len(r) else "")

doc = json.load(open(MANUAL))
# index existing entries by their normalized 'match' so we extend rather than duplicate
by_match = {norm(e["match"]): e for e in doc["entries"]}
added = 0
for r in rows[1:]:
    if g(r, "Type") != "name alias":
        continue
    feed, correct = g(r, "Feed Name"), g(r, "Suggested Player")
    if not feed or not correct:
        continue
    ent = by_match.get(norm(correct))
    if ent is None:
        ent = {"match": correct, "add": []}
        doc["entries"].append(ent); by_match[norm(correct)] = ent
    if norm(feed) not in {norm(a) for a in ent["add"]} and norm(feed) != norm(correct):
        ent["add"].append(feed); added += 1

json.dump(doc, open(MANUAL, "w"), indent=2, ensure_ascii=False)
print(f"folded {added} confident alias(es) into manual_aliases.json "
      f"({len(doc['entries'])} entries total). Now run build_registry.py and commit.")
