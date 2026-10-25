"""Constants for the Hitachi Big Drum integration."""

from datetime import timedelta

DOMAIN = "bigdrum"

CONF_MODE = "mode"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_MEMBERS_KEY = "members_key"
CONF_MEMBERS_IV = "members_iv"
CONF_CSPF_ID = "cspf_id"
CONF_CKDN_KEY = "ckdn_key"
CONF_CKDN_IV = "ckdn_iv"
CONF_CKDN_TOKEN = "ckdn_token"
CONF_SRC = "src"
CONF_DST = "dst"
CONF_NICKNAME = "nickname"
CONF_UDP_KEY = "udp_key"
CONF_UDP_IV = "udp_iv"
CONF_DEVICE_ADDR = "device_addr"
CONF_BROADCAST_ADDR = "broadcast_addr"

MODE_CLOUD = "cloud"
MODE_LAN = "lan"

MANUFACTURER = "Hitachi"
# 実機は BD-STX120H (モデルコード 01003C14 = dst[:8] で確認済み)。
# 上流既定の BD-STX110G (01003517) は作者の機種。表示のみの修正で、
# 動作 (COURSEINF の MODEL) は dst[:8] を使うため影響しない。
MODEL = "BD-STX120H"

DEFAULT_BROADCAST_ADDR = "255.255.255.255"
UDP_TIMEOUT = 2.5
LAN_POLL = timedelta(seconds=3)
CLOUD_POLL_EVERY = timedelta(seconds=55)
LAN_FAIL_COOLDOWN = timedelta(seconds=60)
# 未特定時の SEARCH (ブロードキャスト) 再試行間隔。初回は即試行、失敗後は
# この間隔で再探索する (サイクル番号の割込みに依存しないため失敗クールダウン
# と干渉して発見不能になる不具合を回避)。
SEARCH_RETRY = timedelta(seconds=10)
RELOGIN_THROTTLE = timedelta(minutes=10)
# 本体のバージョン (FWINFO) はファーム更新時のみ変わるため長めにスロットル。
FWINFO_REFRESH = timedelta(hours=6)

PLATFORMS = ["sensor", "binary_sensor"]

# 操作系 (Phase 2) サービス名
SERVICE_START = "start"
SERVICE_STOP = "stop"


def mac_from_dst(dst: str) -> str:
    """製品ID dst の 8〜19 桁 (12 hex) を MAC 形式 (xx:xx:xx:xx:xx:xx・小文字) で返す。

    アプリ (CO00352Activity.dataUI: ``str.substring(8, 20)`` + コロン挿入) と同一。
    デバイスレジストリの接続 (CONNECTION_NETWORK_MAC) で他インテグレーション
    (OPNsense 等のトラッカー) と Linked devices で紐付けるため小文字に正規化する。
    先頭 8 桁はモデルコード。dst が 12 桁未満 (不正) な場合は空文字列を返す。
    """
    mac = str(dst or "")[8:20].lower()
    if len(mac) < 12:
        return ""
    return ":".join(mac[i : i + 2] for i in range(0, 12, 2))
