"""メンバーズクラブ / GLS クラウドAPI クライアント (スマホ・アプリ不要)。

洗濯コンシェルジュアプリ (jp.co.hitachi_gls.concierge) の APK 逆アセンブルから
抽出した通信手順の実装:

1. メンバーズクラブ ログイン (login2)
   POST https://kadenfan.hitachi.co.jp/my/api/login2
   要求: "email=..&password=..&app_id=8" を AES-256-CBC (MEMBERS_KEY, MEMBERS_IV,
         ゼロパディング) で暗号化したバイト列を body として送信
   応答: 同一の鍵/IV で復号 →
         "status=..&agree_cd=..&cspf_id=..&token=..&ckdn_key=<hex>&ckdn_iv=<hex>
          &ckdn_token=..&ckdn_device_key=<dst,key,iv|...>&login_datetime=.."
    - ckdn_key / ckdn_iv : GLS API 通信に使う AES-256 鍵/IV (hex文字列)
    - ckdn_device_key    : 登録デバイスの UDP 50000 鍵 (dst,keyhex,ivhex の
                           コマ区切り。複数デバイスは "|" 区切り。末尾に
                           バイナリのフレームバイトが付加されるため先頭 hex のみ採用)

2. GLS クラウドAPI
   POST https://tkg.capsys21.hitachi-gls.co.jp/cgi-bin/ap_outer_ctrl_filter2.cgi
   要求: "CMD=..&HID=..&APPTYPE=8[&SRC=..&DST=..&DATA=..]" を
         AES-256-CBC (ckdn_key, ckdn_iv, ゼロパディング) で暗号化
   応答: 同一の鍵/IV で復号 → "GRP=..&CMD=..&DATA=.." の k=v 形式 (またはJSON)
   - CMD=GETDEVIDHID : デバイス一覧 (DEVINFO=src,userName,dst,nickName|...)
   - CMD=MOVEINF     : 運転状態 (LAN 経由の UDP と同じ DATA 形式)
   - CMD=GETINF      : 設定/状態 (DATA=0001/0002/0003/0005/0006)
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import protocol

KADEN_LOGIN_URL = "https://kadenfan.hitachi.co.jp/my/api/login2"
GLS_API_URL = (
    "https://tkg.capsys21.hitachi-gls.co.jp/cgi-bin/ap_outer_ctrl_filter2.cgi"
)
APPTYPE = "8"

# アプリ内ハードコード: jp.co.hitachi_gls.concierge.library.constant.ConstantCo
# (メンバーズクラブ API 専用。デバイスごとに変わるものではない)
MEMBERS_KEY: bytes = b""
MEMBERS_IV: bytes = b""


class CloudError(Exception):
    pass


def _http_post(
    url: str, body: bytes, timeout: float, user_agent: str = "okhttp/3.14.9"
) -> bytes:
    # アプリ (okhttp 3.14.9) と同一のヘッダ: Content-Type なし (MediaType=null),
    # Accept-Encoding: gzip (okhttp デフォルト)。
    # GLS API (tkg.capsys21) は User-Agent = ckdn_token で認証する (Interceptor)。
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip" or raw[:2] == b"\x1f\x8b":
            import gzip as _gzip

            raw = _gzip.decompress(raw)
        return raw


def parse_kv(plain: str) -> dict[str, str]:
    """k=v&k=v 形式の平文を dict にする (アプリのパーサーと同様、値内の '=' を切る)。"""
    out: dict[str, str] = {}
    for part in plain.strip().split("&"):
        if not part:
            continue
        pieces = part.split("=")
        k = pieces[0]
        v = pieces[1] if len(pieces) > 1 else ""
        out[k] = v
    return out


# login2 の失敗応答は k=v でなく 16 バイトのバイナリ構造
# "status" + 3桁16進コード + NULパッド (+ 0x10) で返る (アプリも素通しで
# 汎用エラー表示になる)。観測済みコード:
#   9E2 = 要求の形式不正 (フィールド欠落など)
#   9E3 = メールアドレスまたはパスワードが不正
LOGIN_ERR_CODES = {
    "9E2": "要求形式が不正です (内部エラー)",
    "9E3": "メールアドレスまたはパスワードが正しくありません",
}


def _login_error_code(plain: str) -> str | None:
    """バイナリエラー応答 ("status9E3\0...") の場合、コードを返す。"""
    core = "".join(ch for ch in plain if ch.isprintable())
    if core.startswith("status") and "=" not in core:
        return core[6:].strip()
    return None


def login(
    email: str,
    password: str,
    timeout: float = 15.0,
    members_key: bytes | None = None,
    members_iv: bytes | None = None,
) -> dict[str, str]:
    """login2 を呼び、復号した k=v dict を返す (status/cspf_id/ckdn_key/...)。"""
    key = members_key if members_key is not None else MEMBERS_KEY
    iv = members_iv if members_iv is not None else MEMBERS_IV
    if not key or not iv:
        raise CloudError(
            "MEMBERS_KEY / MEMBERS_IV が未設定です。"
            "洗濯コンシェルジュアプリの APK から ConstantCo.MEMBERS_ENCKEY / "
            "MEMBERS_IV を抽出して設定してください (README 参照)。"
        )
    msg = f"email={email}&password={password}&app_id={APPTYPE}"
    body = protocol.encrypt(key, iv, msg.encode("utf-8"))
    try:
        raw = _http_post(KADEN_LOGIN_URL, body, timeout)
    except Exception as e:
        raise CloudError(f"login2 通信に失敗しました: {e}") from e
    try:
        plain = protocol.decrypt(key, iv, raw).decode("utf-8")
    except Exception as e:
        raise CloudError(
            "login2 応答の復号に失敗しました (鍵が古いか、応答形式が変化しています)"
        ) from e
    code = _login_error_code(plain)
    if code is not None:
        detail = LOGIN_ERR_CODES.get(code, "不明なエラー")
        raise CloudError(
            f"ログイン失敗 (code={code}): {detail}\n"
            f"スマホアプリでログインできると同じメールアドレス/パスワードを"
            f"確認してください。"
        )
    res = parse_kv(plain)
    if not res:
        raise CloudError(f"login2 応答が空でした (plain={plain!r})")
    return res


_HEX64_RE = re.compile(r"^[0-9a-fA-F]{64}")
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}")


def parse_ckdn_device_key(s: str) -> list[tuple[str, str, str]]:
    """ckdn_device_key ("dst,keyhex,ivhex|dst,keyhex,ivhex") を分解する。

    実レスポンスでは末尾の IV フィールドの後にバイナリのフレームバイト
    (NUL / 0x10 など) が付加されるため、key は先頭 64 桁 hex、IV は先頭
    32 桁 hex のみを採用する (アプリの UdpKey.hexStr2Bytes と同一の语义)。
    """
    out: list[tuple[str, str, str]] = []
    if not s:
        return out
    for chunk in s.split("|"):
        parts = chunk.split(",")
        if len(parts) < 3 or not parts[0]:
            continue
        key_m = _HEX64_RE.match(parts[1])
        iv_m = _HEX32_RE.match(parts[2])
        if key_m is None or iv_m is None:
            continue
        out.append((parts[0].strip(), key_m.group(0), iv_m.group(0)))
    return out


def gls_call(
    ckdn_key: str,
    ckdn_iv: str,
    params: dict[str, str],
    ckdn_token: str = "",
    timeout: float = 15.0,
) -> dict[str, str] | str:
    """GLS API を呼び、復号した k=v dict (JSON 応答の場合は生の文字列) を返す。

    ckdn_token は User-Agent ヘッダとして送信する (アプリの Interceptor と同一)。
    """
    key = bytes.fromhex(ckdn_key)
    iv = bytes.fromhex(ckdn_iv)
    msg = "&".join(f"{k}={v}" for k, v in params.items())
    body = protocol.encrypt(key, iv, msg.encode("utf-8"))
    ua = ckdn_token or "okhttp/3.14.9"
    try:
        raw = _http_post(GLS_API_URL, body, timeout, user_agent=ua)
    except Exception as e:
        raise CloudError(f"GLS API 通信に失敗しました: {e}") from e
    try:
        plain = protocol.decrypt(key, iv, raw).decode("utf-8")
    except Exception as e:
        raise CloudError(
            "GLS API 応答の復号に失敗しました (ckdn_key/ckdn_iv が古い可能性があります。"
            "再ログインしてください)"
        ) from e
    stripped = plain.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            return plain
        except json.JSONDecodeError:
            pass
    return parse_kv(stripped)


@dataclass
class DevInfo:
    src: str  # mobileId (UDP 通信での SRC)
    user_name: str
    dst: str  # 製品ID (UDP 通信での DST)
    nick_name: str = ""


def get_devices(
    ckdn_key: str,
    ckdn_iv: str,
    cspf_id: str,
    ckdn_token: str = "",
    timeout: float = 15.0,
) -> list[DevInfo]:
    """CMD=GETDEVIDHID で登録デバイス一覧を取得する。"""
    res = gls_call(
        ckdn_key,
        ckdn_iv,
        {"CMD": "GETDEVIDHID", "HID": cspf_id, "APPTYPE": APPTYPE},
        ckdn_token=ckdn_token,
        timeout=timeout,
    )
    if isinstance(res, str):
        raise CloudError(f"GETDEVIDHID 応答が予期しない形式でした: {res[:200]!r}")
    devinfo = res.get("DEVINFO", "")
    if not devinfo:
        grp = res.get("GRP", "")
        if grp and grp != "0":
            raise CloudError(f"GETDEVIDHID 失敗 (GRP={grp})")
        return []
    out: list[DevInfo] = []
    for chunk in devinfo.split("|"):
        parts = chunk.split(",")
        if len(parts) == 1:
            continue  # 削除済みの目印
        if len(parts) >= 4:
            out.append(DevInfo(parts[0], parts[1], parts[2], parts[3]))
        elif len(parts) == 3:
            out.append(DevInfo(parts[0], "", parts[1], parts[2]))
    return out


def _moveinf_blob(data: str) -> str:
    """MOVEINF の DATA から状態 blob を取る (クラウドは4桁目、UDPは2桁目)。"""
    parts = data.split(",")
    return parts[3] if len(parts) >= 4 else parts[1]


def cloud_moveinf(
    ckdn_key: str,
    ckdn_iv: str,
    src: str,
    dst: str,
    ckdn_token: str = "",
    timeout: float = 15.0,
) -> tuple[str, str, dict[str, Any]]:
    """クラウド経由で運転状態 (MOVEINF) を取得する。

    (GRP, DATA, パース済み dict) を返す。LAN 不要 (家電の Wi-Fi が外出先でも
    インターネットにつながっていれば動作)。

    ``GRP`` は {0,3,4} のみ成功 (§3.3)。それ以外 (例 2) は本体電源OFF / 未報告の
    **確定信号** (ネットワークエラーではない)。この場合 ``parsed`` は空 dict なので、
    呼び出し側は ``protocol.no_report_parsed()`` (電源OFFメッセージ) に差し替えて
    表示する (§6.2)。
    """
    res = gls_call(
        ckdn_key,
        ckdn_iv,
        {"CMD": "MOVEINF", "SRC": src, "DST": dst},
        ckdn_token=ckdn_token,
        timeout=timeout,
    )
    if isinstance(res, str):
        raise CloudError(f"MOVEINF 応答が予期しない形式でした: {res[:200]!r}")
    grp = res.get("GRP", "")
    data = res.get("DATA", "")
    parsed: dict[str, Any] = {}
    if data:
        try:
            parsed = protocol._parse_moveinf_blob(_moveinf_blob(data))
        except Exception:
            pass
    return grp, data, parsed


def cloud_fwinfo(
    ckdn_key: str,
    ckdn_iv: str,
    src: str,
    dst: str,
    ckdn_token: str = "",
    timeout: float = 15.0,
) -> dict[str, str]:
    """クラウド経由で本体のファームウェア情報 (FWINFO) を取得する。

    アプリの「本体のバージョン」は `FWVER` (例 "04051122")。FWMODE / FWSTS も返す。
    (UDP ではなくクラウド API のみで取得できる。)
    """
    res = gls_call(
        ckdn_key,
        ckdn_iv,
        {"CMD": "FWINFO", "SRC": src, "DST": dst},
        ckdn_token=ckdn_token,
        timeout=timeout,
    )
    if isinstance(res, str):
        raise CloudError(f"FWINFO 応答が予期しない形式でした: {res[:200]!r}")
    return {
        "FWVER": res.get("FWVER", ""),
        "FWMODE": res.get("FWMODE", ""),
        "FWSTS": res.get("FWSTS", ""),
    }


def cloud_getinf(
    ckdn_key: str,
    ckdn_iv: str,
    src: str,
    dst: str,
    ckdn_token: str = "",
    sub: str = "0005",
    timeout: float = 15.0,
) -> tuple[str, str, dict[str, Any] | None]:
    """クラウド経由で設定/状態 (GETINF) を取得する。"""
    res = gls_call(
        ckdn_key,
        ckdn_iv,
        {"CMD": "GETINF", "SRC": src, "DST": dst, "DATA": sub},
        ckdn_token=ckdn_token,
        timeout=timeout,
    )
    if isinstance(res, str):
        raise CloudError(f"GETINF 応答が予期しない形式でした: {res[:200]!r}")
    grp = res.get("GRP", "")
    data = res.get("DATA", "")
    parsed = protocol._parse_getinf(data) if data else None
    return grp, data, parsed


def cloud_ctrl(
    ckdn_key: str,
    ckdn_iv: str,
    src: str,
    dst: str,
    ctrlc: str,
    ctrld: str,
    time_hex: str = "",
    ckdn_token: str = "",
    timeout: float = 15.0,
) -> tuple[str, str]:
    """クラウド経由で操作指令 (CTRLINS) を送信する。 (GRP, DATA) を返す。

    クラウドの ReqCtrlIns は TIME/CTRLC/CTRLD が明示的なパラメータのため、
    UDP と異なり DATA 結合の曖昧さが無い。
    """
    params: dict[str, str] = {
        "CMD": "CTRLINS",
        "SRC": src,
        "DST": dst,
        "CTRLC": ctrlc,
        "CTRLD": ctrld,
    }
    if time_hex:
        params["TIME"] = time_hex
    res = gls_call(
        ckdn_key, ckdn_iv, params, ckdn_token=ckdn_token, timeout=timeout
    )
    if isinstance(res, str):
        raise CloudError(f"CTRLINS 応答が予期しない形式でした: {res[:200]!r}")
    return {
        "grp": res.get("GRP", ""),
        "success": res.get("SUCCESS", ""),
        "data": res.get("DATA", ""),
    }


def cloud_courseinf(
    ckdn_key: str,
    ckdn_iv: str,
    model: str,
    version: str = "",
    ckdn_token: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    """COURSEINF でモデル別のコースカタログ (JSON) を取得する。

    応答は JsonRootCourse (version / newest / course_set[]) の JSON。
    newest が "newest" の場合は更新なし (アプリはそのまま破棄)。
    model は dst の先頭8桁 (アプリの appDst.substring(0,8) と同一)。
    """
    res = gls_call(
        ckdn_key,
        ckdn_iv,
        {"CMD": "COURSEINF", "MODEL": model, "VERSION": version},
        ckdn_token=ckdn_token,
        timeout=timeout,
    )
    if isinstance(res, str):
        try:
            return json.loads(res)
        except json.JSONDecodeError as e:
            raise CloudError(
                f"COURSEINF 応答が JSON ではありませんでした: {res[:300]!r}"
            ) from e
    if isinstance(res, dict):
        data = res.get("DATA", "")
        if isinstance(data, str) and data:
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                pass
        return res
    raise CloudError(f"COURSEINF 応答が予期しない形式でした: {res!r}")
