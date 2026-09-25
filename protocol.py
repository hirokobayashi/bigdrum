"""50000/udp プロトコル層: AES-256-CBC 暗号化 / リクエスト構築 / レスポンスパース。

- リクエスト・レスポンス本体は AES-256-CBC (ゼロパディング) で暗号化
- 末尾にリクエストバイト列の SHA256 の先頭 16 バイトがチェックサムとして付く
- 平文は "SEQ=..&SRC=..&DST=..&GRP=REQ|ACK&CMD=..&[DATA=..]" 形式
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

BLOCK = 16  # AES block size (固定 16 バイト)


def encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    from Crypto.Cipher import AES

    pad = BLOCK - len(data) % BLOCK
    return AES.new(key, AES.MODE_CBC, iv).encrypt(data + b"\x00" * pad)


def decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    from Crypto.Cipher import AES

    return AES.new(key, AES.MODE_CBC, iv).decrypt(data).rstrip(b"\x00")


def now_string_hex(dt: datetime | None = None) -> str:
    """アプリの TimeUtils.getNowStringHex() と同一形式の時刻 hex (16桁)。

    %04X(年) %02X(月) %02X(日) %02X(時) %02X(分) %02X(秒) %02X(曜日)
    曜日は日曜=0..土曜=6 (日本のカレンダー既定: 週初め=日曜のため)。
    UDP (50000) の CTRLINS 等の TIME に使用。
    """
    if dt is None:
        dt = datetime.now()
    weekday = (dt.weekday() + 1) % 7
    return (
        f"{dt.year:04X}{dt.month:02X}{dt.day:02X}"
        f"{dt.hour:02X}{dt.minute:02X}{dt.second:02X}{weekday:02X}"
    )


def now_string_dex(dt: datetime | None = None) -> str:
    """アプリの TimeUtils.getNowStringDex() と同一形式の時刻 (10進・15桁)。

    %04d(年) %02d(月) %02d(日) %02d(時) %02d(分) %02d(秒) %1d(曜日)
    曜日は日曜=0..土曜=6。クラウド (GLS) の CTRLINS 等に使用。
    """
    if dt is None:
        dt = datetime.now()
    weekday = (dt.weekday() + 1) % 7
    return (
        f"{dt.year:04d}{dt.month:02d}{dt.day:02d}"
        f"{dt.hour:02d}{dt.minute:02d}{dt.second:02d}{weekday:d}"
    )


def build_request(
    key: bytes,
    iv: bytes,
    seq: int,
    src: str,
    dst: str,
    cmd: str,
    data: str | None = None,
) -> bytes:
    req = f"SEQ={seq % 256:02X}&SRC={src}&DST={dst}&GRP=REQ&CMD={cmd}&"
    if data is not None:
        req += f"DATA={data}"
    req_bytes = req.encode("utf-8")
    return encrypt(key, iv, req_bytes) + hashlib.sha256(req_bytes).digest()[:16]


def _get_data(res: str) -> str | None:
    for kv in res.split("&"):
        if "=" not in kv:
            continue
        k, v = kv.split("=", 2)
        if k == "DATA":
            return v
    return None


STATUS_MAP = {
    "00": "本体の電源が入っていないか、通信ができない状態です。",
    "05": "予約中",
    "06": "運転中",
    "07": "運転中",
    "40": "槽内冷却中",
    "41": "槽内冷却中",
    "42": "槽内冷却中",
    "43": "槽内冷却中",
    "44": "槽内冷却中",
    "45": "槽内冷却中",
    "46": "槽内冷却中",
    "47": "槽内冷却中",
    "86": "一時停止中",
    "87": "一時停止中",
}

PROCESS_MAP = {
    "FF": "運転終了",
    "FA": "乾燥終了・ふんわりガード運転中",
    "FB": "自動投入部お手入れ運転中",
}

INFO_CODE_I_MAP = {
    "01": "ドア開閉確認",
    "02": "糸くずフィルター",
    "03": "乾燥フィルター",
    "04": "乾燥容量オーバー",
    "05": "槽洗浄おすすめサイン",
    "06": "槽洗いおすすめサイン",
}

AI_INFO_BITS = [
    (0b100000, "衣類から出る水分量が少ないので、脱水時間を短縮します。"),
    (0b10000, "衣類がすすげているので、すすぎ時間を短縮します。"),
    (0b1000, "化繊衣類等が多いので、すすぎの水量を減らします。"),
    (0b100, "汚れの量が多いので、洗い時間を延長します。"),
    (0b10, "水の硬度が低く水温が高いので、洗い時間を短縮します。"),
    (0b1, "水の硬度が低く水温が高いので、洗剤量を減らします。"),
]

REMAIN_REASON_BITS = [
    (0b10000, "衣類の片寄りを直しました。"),
    (0b1000, "運転内容を見直しました。"),
    (0b100, "運転内容を見直しています。"),
    (0b10, "衣類の片寄りを直しています。"),
    (0b1, "泡消し運転を実施しています。"),
]


def no_report_parsed() -> dict[str, Any]:
    """本体電源OFF / 未報告 (クラウド GRP∉{0,3,4}・例 2) の状態 dict (§3.3 / §6.2)。

    公式アプリが状態を取得できないときに表示するメッセージ (statusCode=00) と
    同一の ``status`` を返す。呼び出し側はこれを「状態データ」として保持し、
    運転状態 (status) だけを表示する (PROTOCOL.md §6.2)。
    ``noReport=True`` がフラグで、状態データなし (他フィールドは全て欠落)。
    """
    return {
        "statusCode": "00",
        "status": STATUS_MAP["00"],
        "noReport": True,
    }


def _parse_remaining_time(v: str) -> int | None:
    """残り時間 (16進) をパース。0xFFFF は「時間なし」の sentinel として None。"""
    n = int(v, 16)
    return None if n >= 0xFFFF else n


# 運転フェーズ (洗い/すすぎ/脱水/乾燥) の判定テーブル。
# MOVEINF blob の processAll (s[18:20]) × process (s[20:22]) で現在フェーズを特定する。
# 対応は apk の ImagePattern2B (画像名 status_<フェーズ数>_<種別>_<ステップ>) を基に作成。
# processAll=1E (course 1201 洗乾) は実機ライブ取得で確認済み。
# process=FF は運転終了 (status センサーが「運転終了」表示) するためここでは対象外。
# 単一フェーズの特殊運転 (02/04/08/10/22) もフェーズの対象外 (status が表示する)。
PROCESS_ALL_PHASES: dict[str, dict[str, str]] = {
    # 4 フェーズ (洗濯〜乾燥)
    "1E": {"02": "洗い", "03": "すすぎ", "04": "脱水", "05": "乾燥"},
    "0F": {"01": "洗い", "02": "すすぎ", "03": "脱水", "04": "乾燥"},
    "1B": {"01": "洗い", "02": "すすぎ", "04": "脱水", "05": "乾燥"},
    "3E": {"02": "洗い", "03": "すすぎ", "04": "脱水", "05": "乾燥"},
    # 3 フェーズ (洗濯)
    "07": {"01": "洗い", "02": "すすぎ", "03": "脱水"},
    "0B": {"01": "洗い", "02": "すすぎ", "04": "脱水"},
    "0E": {"02": "洗い", "03": "すすぎ", "04": "脱水"},
    "2E": {"02": "洗い", "03": "すすぎ", "04": "脱水"},
    "3A": {"02": "洗い", "04": "すすぎ", "05": "脱水"},
    "1A": {"02": "洗い", "04": "すすぎ", "05": "脱水"},
    # 2 フェーズ
    "03": {"01": "洗い", "02": "脱水"},
    "06": {"02": "洗い", "03": "脱水"},
    "0A": {"02": "洗い", "04": "脱水"},
    "0C": {"03": "洗い", "04": "脱水"},
    "26": {"02": "洗い", "03": "脱水"},
    "2A": {"02": "洗い", "04": "脱水"},
    # 5 フェーズ
    "1F": {"01": "洗い", "02": "すすぎ", "03": "脱水", "04": "乾燥", "05": "乾燥"},
}


def current_phase(status_code: str, process_all: str, process: str) -> str | None:
    """運転中の現在フェーズ (洗い/すすぎ/脱水/乾燥) を返す。運転外・終了(FF)・未知は None。"""
    if status_code not in ("06", "07"):
        return None
    return PROCESS_ALL_PHASES.get(process_all, {}).get(process)


def _parse_moveinf_blob(s: str) -> dict[str, Any]:
    """MOVEINF の状態 blob (hex文字列) をパースする。"""
    date = f"{int(s[0:4], 16):04}/{int(s[4:6], 16):02}/{int(s[6:8], 16):02}"
    time = f"{int(s[8:10], 16):02}:{int(s[10:12], 16):02}"
    status_code = s[16:18]
    process = s[20:22]
    info_code_c = s[26:28]
    info_code_f = s[28:30]
    info_code_i = s[30:32]
    ai_info_flag = int(s[34:36], 16)
    reason_flag = int(s[76:78], 16)

    status = STATUS_MAP.get(status_code, "待機中")
    if process in PROCESS_MAP:
        status = PROCESS_MAP[process]

    info = ""
    if info_code_c != "00" or info_code_f != "00" or info_code_i != "00":
        info = "お知らせがあります"
        if info_code_f != "00":
            info += f"「コードF{'00' if info_code_f == 'FF' else info_code_f}」"
        elif info_code_c != "00":
            info += f"「コードC{info_code_c}」"
        elif info_code_i in INFO_CODE_I_MAP:
            info += f"「コードI{info_code_i}:{INFO_CODE_I_MAP[info_code_i]}」"

    ai_infos = [msg for bit, msg in AI_INFO_BITS if ai_info_flag & bit]
    reasons = [msg for bit, msg in REMAIN_REASON_BITS if reason_flag & bit]

    return {
        "date": date,
        "time": time,
        "statusCode": status_code,
        "status": status,
        "processAll": s[18:20],
        "process": process,
        "phase": current_phase(status_code, s[18:20], process),
        "selCourse": s[22:26],
        "infoCodeC": info_code_c,
        "infoCodeF": info_code_f,
        "infoCodeI": info_code_i,
        "info": info,
        "aiInfoFlag": ai_info_flag,
        "aiInfos": ai_infos,
        "downloadCourse1": s[36:48],
        "downloadCourse2": s[48:60],
        "downloadCourse3": s[60:72],
        "remainingTime": _parse_remaining_time(s[72:76]),
        "reasonRemainingTimeFlag": reason_flag,
        "reasonRemainingTime": reasons,
        "yoyakuRemainingCounter": f"{int(s[78:82], 16):04}",
        "remoteOperationStatus": s[82:84],
        "uniqueId": s[84:88],
    }


def _parse_moveinf(data: str) -> dict[str, Any]:
    """UDP 経由の MOVEINF DATA ("prefix,blob") をパースする。"""
    return _parse_moveinf_blob(data.split(",")[1])


# GETINF のサブコマンド (0004 は存在しない)
GETINF_SUBS = ("0001", "0002", "0003", "0005", "0006")


def _parse_getinf(data: str) -> dict[str, Any] | None:
    k, v = data.split(",", 2)
    if k == "0001":
        jido_tonyu_zanryo_flag = int(v[54:56], 16)
        return {
            "yoyaku": f"{int(v[0:4], 16):04}",
            "arai": v[4:6],
            "susugi": v[6:8],
            "dassui": v[8:10],
            "kanso": v[10:12],
            "onsuiMisth": v[12:14],
            "sensor": v[14:16],
            "autoOsouji": v[16:18],
            "oyutori": v[18:20],
            "untenStart": v[20:22],
            "shuryoYokoku": v[22:24],
            "dassuiGuai": v[24:26],
            "kawakiGuai": v[26:28],
            "onpuHogushi": v[28:30],
            "funwari": v[30:32],
            "atatameAuto": v[32:34],
            "dassuikido": v[34:36],
            "onpuDassui": v[36:38],
            "joshitsu": v[38:40],
            "oyutoriMemory": v[40:42],
            "shimizuSusugi": v[42:44],
            "suiryo": v[44:46],
            "sosaMukoOn": v[46:48],
            "suiryoOme": v[48:50],
            "kaitenShawa": v[50:52],
            "toridashi": v[52:54],
            "jidoTonyuZanryoFlag": jido_tonyu_zanryo_flag,
            "senzaiZanryoSho": jido_tonyu_zanryo_flag & 0b1,
            "nyunanzaiZanryoSho": jido_tonyu_zanryo_flag & 0b10,
            "senzaiMeigara": v[56:58],
            "senzaiKijun": v[58:60],
            "senzaiAiSusugi": v[60:62],
            "senzaiJidoTonyu": v[62:64],
            "nyunanzaiMeigara": v[64:66],
            "nyunanzaiKijun": v[66:68],
            "nyunanzaiJidoTonyu": v[68:70],
            "suion": v[70:72],
        }
    if k == "0002":
        return {
            "sentakuArai": v[0:2],
            "sentakuSusugi": v[2:4],
            "sentakuDassui": v[4:6],
            "sentakuKanso": v[6:8],
            "araiInuiArai": v[8:10],
            "araiInuiSusugi": v[10:12],
            "araiInuiDassui": v[12:14],
            "araiInuiKanso": v[14:16],
            "sentakuSuiryo": v[16:18],
            "araiInuiSuiryo": v[18:20],
        }
    if k == "0003":
        return {
            "yoyaku": f"{int(v[0:4], 16):04}",
            "arai": v[4:6],
            "susugi": v[6:8],
            "dassui": v[8:10],
            "kanso": v[10:12],
            "onsuiMist": v[12:14],
            "sensor": v[14:16],
            "autoOsouji": v[16:18],
            "oyutori": v[18:20],
            "suiryo": v[20:22],
            "senzai": v[22:24],
            "nyunanzai": v[24:26],
            "suion": v[26:28],
        }
    if k == "0005":
        return {"remainingTime": _parse_remaining_time(v)}
    if k == "0006":
        return {"reserveRelated": int(v, 16)}
    return None


@dataclass
class DecodedResponse:
    """受信パケットの復号結果。"""

    seq: str
    src: str
    dst: str
    grp: str
    cmd: str
    data: str | None
    plain: str
    parsed: dict[str, Any] = field(default_factory=dict)


def parse_packet(key: bytes, iv: bytes, packet: bytes) -> DecodedResponse:
    """受信 UDP パケット (AES ciphertext + SHA256[0:16]) を復号・パースする。"""
    plain = decrypt(key, iv, packet[:-16]).decode("utf-8")
    fields: dict[str, str] = {}
    for kv in plain.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 2)
            fields[k] = v

    data = fields.get("DATA")
    parsed: dict[str, Any] = {}
    if data is not None and fields.get("GRP") == "ACK":
        if fields.get("CMD") == "MOVEINF":
            parsed = _parse_moveinf(data)
        elif fields.get("CMD") == "GETINF":
            p = _parse_getinf(data)
            if p is not None:
                parsed = p

    return DecodedResponse(
        seq=fields.get("SEQ", ""),
        src=fields.get("SRC", ""),
        dst=fields.get("DST", ""),
        grp=fields.get("GRP", ""),
        cmd=fields.get("CMD", ""),
        data=data,
        plain=plain,
        parsed=parsed,
    )


def extract_ids(plain: str) -> tuple[str, str] | None:
    """平文から (SRC, DST) を抽出する。"""
    import re

    m = re.search(r"SRC=([0-9A-Fa-f]+)&DST=([0-9A-Fa-f]+)", plain)
    if not m:
        return None
    return m.group(1), m.group(2)
