"""COURSEINF カタログから CTRLC=0003 の CTRLD (コース文字列) を組み立てる。

実機捕捉 (BD-STX110G / コース1101・標準コース) で確定した形式 (42 桁):

    0000             開始マーカー (即時開始="0000" / 予約は先頭4桁を予約時刻 %04X に置換)
    course_no(4)
    moto_course_no(4)
    suiryo(2) arai(2) susugi(2) dassui(2) kanso(2)
    tokusyu(8)
    onsui_mist(2) sensor(2) jido_osouji(2) oyutori(2) senzai(2) softener(2)

CATALOG の initial が "Z" (未選択プレースホルダ) のフィールドは既定値を使う。
既定値は実機捕捉の値から確定 (標準コース: sensor=02, jido_osouji=01,
senzai=01, softener=01)。
"""

from __future__ import annotations

# コース本体の option フィールド順 (2 桁ずつ)
COURSE_FIELDS = ("suiryo", "arai", "susugi", "dassui", "kanso")
# tokusyu の次の option フィールド順 (2 桁ずつ)
TAIL_FIELDS = ("onsui_mist", "sensor", "jido_osouji", "oyutori", "senzai", "softener")

# initial が "Z" のフィールドの既定値 (実機捕捉・コース1101 より)
Z_DEFAULTS = {
    "sensor": "02",
    "jido_osouji": "01",
    "senzai": "01",
    "softener": "01",
}

_HEX = "0123456789ABCDEF"


def _norm_hex(value, label: str, width: int) -> str:
    """width 桁の hex 値を正規化して返す。

    1 桁 (例 "c") はゼロパディング ("0c"→"0C")。それ以外は
    "0123456789ABCDEF" 限定の width 桁でなければ ValueError (壊れた
    CTRLD を本体に送らないためのガード)。
    """
    s = str(value).strip().upper()
    if len(s) == 1 and s in _HEX:
        s = "0" * (width - 1) + s
    if len(s) != width or any(c not in _HEX for c in s):
        raise ValueError(f"{label}={value!r} is not a {width}-digit hex value")
    return s


def _val(course: dict, key: str, overrides: dict) -> str:
    """1 フィールドの 2 桁値を求める (override > initial > Z 既定)"""
    if key in overrides and overrides[key] is not None:
        return _norm_hex(overrides[key], f"option {key}", 2)
    node = course.get(key)
    ini = node.get("initial") if isinstance(node, dict) else None
    s = str(ini).upper() if ini is not None else ""
    if s in ("", "Z"):
        return Z_DEFAULTS.get(key, "00")
    return s


def build_course_ctrld(course: dict, overrides: dict | None = None, marker: str = "0000") -> str:
    """course1/2/3 構造の dict から 42 桁の CTRLD を返す。

    marker (先頭4桁) は 4 桁 hex を要求 (即時開始="0000" / 予約=開始までの分数 %04X)。
    overrides の値は 2 桁 hex (1 桁はゼロパディング) でなければ ValueError。
    """
    ov = {k: v for k, v in (overrides or {}).items() if v is not None}
    marker = _norm_hex(marker, "marker", 4)
    course_no = str(course.get("course_no") or course.get("courseNo") or "")[:4]
    moto = str(course.get("moto_course_no") or course.get("motoCourseNo") or course_no)[:4]
    tokusyu = str(course.get("tokusyu") or "00000000")[:8].ljust(8, "0")

    out = [marker, course_no.ljust(4, "0"), moto.ljust(4, "0")]
    for k in COURSE_FIELDS:
        out.append(_val(course, k, ov)[:2].ljust(2, "0"))
    out.append(tokusyu)
    for k in TAIL_FIELDS:
        out.append(_val(course, k, ov)[:2].ljust(2, "0"))
    return "".join(out)


def find_course(cat: dict, course_no) -> dict | None:
    """COURSEINF キャタログから course_no 一致の course dict を返す。"""
    target = str(course_no)
    for cs in cat.get("course_set", []) or []:
        cl = cs.get("course_List", {}) or {}
        for ci in ("course1", "course2", "course3"):
            c = cl.get(ci)
            if isinstance(c, dict) and str(c.get("course_no") or c.get("courseNo")) == target:
                return c
    return None


def build_course_label_maps(cat: dict) -> dict[str, dict[str, dict[str, str]]]:
    """course_no ごとに {field: {value: name}} を構築する。

    値 (2桁16進) → 名称の対応は **コースごとに異なる** (同一 value がコース間で
    別名称: 例 arai=0C は 12分/60分/105分)。ゆえに単一のグローバル表に正規化できず、
    現在選択コース (selCourse) の disp で解決する必要がある。
    """
    out: dict[str, dict[str, dict[str, str]]] = {}
    for cs in cat.get("course_set", []) or []:
        cl = cs.get("course_List", {}) or {}
        for ci in ("course1", "course2", "course3"):
            c = cl.get(ci)
            if not isinstance(c, dict):
                continue
            cno = str(c.get("course_no") or c.get("courseNo") or "")
            if not cno:
                continue
            m: dict[str, dict[str, str]] = {}
            for field, node in c.items():
                if not isinstance(node, dict):
                    continue
                disp = node.get("disp")
                if not isinstance(disp, list):
                    continue
                fm = m.setdefault(field, {})
                for it in disp:
                    if not isinstance(it, dict):
                        continue
                    v = it.get("value")
                    n = it.get("name")
                    if v is None or n is None:
                        continue
                    fm.setdefault(str(v).upper(), str(n))
            if m:
                out.setdefault(cno, m)
    return out


def find_course_set_name(cat: dict, course_no) -> tuple[str, str] | None:
    """course_no に対応する (course_set_name, course_tab) を返す。見つからなければ None。

    course_no の先頭2桁は操作 (洗濯/洗乾/乾燥…)、末尾2桁はコース種別に対応し、
    同じ course_set_name を複数の操作が共有する。表示は (日本語名, タブ) を返す。
    """
    target = str(course_no)
    for cs in cat.get("course_set", []) or []:
        cl = cs.get("course_List", {}) or {}
        for ci in ("course1", "course2", "course3"):
            c = cl.get(ci)
            if isinstance(c, dict) and str(c.get("course_no") or c.get("courseNo")) == target:
                return (
                    str(cs.get("course_set_name") or ""),
                    str(c.get("course_tab") or c.get("courseTab") or ""),
                )
    return None
