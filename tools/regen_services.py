"""Regenerate services.yaml course options from a COURSEINF catalog JSON.

Usage: python3 regen_services.py <courseinf.json> [services.yaml]

Rewrites the options list under start.fields.course_no with every course in
the catalog (course_no "0000" placeholder excluded), grouped by course_tab
(洗濯/洗乾/乾燥/なし) and sorted by course_no. Duplicate course names get a
tab suffix so all labels stay unique in the HA UI; the two 槽洗いコース
entries (1142/210A) share both name and tab, so they are disambiguated by
course code instead.
"""
import json
import sys
from collections import Counter

cat_path = sys.argv[1]
svc_path = sys.argv[2] if len(sys.argv) > 2 else "custom_components/bigdrum/services.yaml"

cat = json.load(open(cat_path, encoding="utf-8"))

ORDER = {"洗濯": 0, "洗乾": 1, "乾燥": 2, "なし": 3}
entries: list[tuple[str, str, str]] = []  # (tab, no, name)
seen = set()
for cs in cat["course_set"]:
    name = cs.get("course_set_name", "?")
    for _k, c in (cs.get("course_List") or {}).items():
        if not isinstance(c, dict):
            continue
        no = c.get("course_no", "")
        if not no or no == "0000":
            continue
        if (no, name) in seen:
            continue
        seen.add((no, name))
        entries.append((c.get("course_tab") or "その他", no, name))

name_counts = Counter(name for _, _, name in entries)
name_tab_counts = Counter((name, tab) for tab, _, name in entries)

lines = []
for tab in sorted({e[0] for e in entries}, key=lambda t: ORDER.get(t, 9)):
    lines.append(f"            # {tab}")
    for t2, no, name in sorted(
        (e for e in entries if e[0] == tab), key=lambda r: r[1]
    ):
        if name_tab_counts[(name, tab)] > 1:
            label = f"{name} ({no})"
        elif name_counts[name] > 1:
            label = f"{name} ({tab})"
        else:
            label = name
        lines.append(f'            - value: "{no}"')
        lines.append(f'              label: "{label}"')

src = open(svc_path, encoding="utf-8").read()
head, rest = src.split("          options:\n", 1)
_, tail = rest.split("\n    delay_minutes:\n", 1)
new = head + "          options:\n" + "\n".join(lines) + "\n    delay_minutes:\n" + tail
open(svc_path, "w", encoding="utf-8").write(new)

labels = [l for l in lines if "label:" in l]
dups = [k for k, v in Counter(labels).items() if v > 1]
print(f"options: {len([l for l in lines if '- value:' in l])} | duplicate labels: {dups if dups else 'none'}")
