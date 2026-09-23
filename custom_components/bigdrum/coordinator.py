"""Data coordinator: LAN UDP (3s) / cloud (60s) auto-switching.

The same design the official app uses:
- Same Wi-Fi (LAN reachable): UDP MOVEINF every 3s
- Otherwise: cloud MOVEINF every 60s
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from . import cloud, course
from .const import (
    CLOUD_POLL_EVERY,
    CONF_BROADCAST_ADDR,
    CONF_CKDN_IV,
    CONF_CKDN_KEY,
    CONF_CKDN_TOKEN,
    CONF_DEVICE_ADDR,
    CONF_DST,
    CONF_EMAIL,
    CONF_MEMBERS_IV,
    CONF_MEMBERS_KEY,
    CONF_MODE,
    CONF_NICKNAME,
    CONF_PASSWORD,
    CONF_SRC,
    CONF_UDP_IV,
    CONF_UDP_KEY,
    DEFAULT_BROADCAST_ADDR,
    FWINFO_REFRESH,
    LAN_FAIL_COOLDOWN,
    LAN_POLL,
    MODE_CLOUD,
    MODE_LAN,
    RELOGIN_THROTTLE,
    SEARCH_RETRY,
    UDP_TIMEOUT,
)
from .protocol import now_string_dex, now_string_hex
from .udp import UdpExchangeError, udp_exchange

_LOGGER = logging.getLogger(__name__)

class BigDrumCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches washer state over LAN UDP or the GLS cloud."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"bigdrum {entry.data.get(CONF_NICKNAME) or entry.data.get(CONF_DST, '')}",
            update_interval=LAN_POLL,
        )
        self._seq = 0x20
        self._device_addr: str | None = entry.data.get(CONF_DEVICE_ADDR) or None
        self._last_cloud: datetime | None = None
        self._lan_cooldown_until: datetime | None = None
        self._last_search: datetime | None = None
        self._last_relogin: datetime | None = None
        self._last_parsed: dict[str, Any] | None = None
        self._course_cache: dict[str, Any] | None = None
        self._course_label_maps: dict[str, dict[str, dict[str, str]]] | None = None
        # 健全性 (diagnostics / online binary_sensor 用)
        self._last_fresh_at: datetime | None = None
        self._last_error: str | None = None
        self._stale: bool = False
        self._consecutive_failures: int = 0
        self._total_updates: int = 0
        self._total_failures: int = 0
        self._last_lan_result: str | None = None
        self._last_cloud_result: str | None = None
        # 本体のバージョン (FWINFO / FWVER)。ファーム更新時のみ変わるためキャッシュ。
        self._fw_info: dict[str, str] | None = None
        self._fw_info_at: datetime | None = None
        # 最後に実際に取得に成功した経路 ("lan"/"cloud")。stale 期間でも保持 (診断用)。
        self._last_source: str | None = None

    # ---------------------------------------------------------------- LAN

    def _next_seq(self) -> int:
        self._seq = (self._seq + 1) % 256
        return self._seq

    def _lan_discover(self, data: dict[str, Any], key: bytes, iv: bytes) -> str:
        """UDP SEARCH (ブロードキャスト) でデバイスの IP を発見して返す。"""
        result = udp_exchange(
            key,
            iv,
            data[CONF_SRC],
            data[CONF_DST],
            self._next_seq(),
            "SEARCH",
            None,
            data.get(CONF_BROADCAST_ADDR) or DEFAULT_BROADCAST_ADDR,
            timeout=UDP_TIMEOUT,
        )
        return result.from_host

    def _lan(self) -> dict[str, Any] | None:
        """UDP MOVEINF. Returns the parsed dict, or None when the unit is off."""
        data = self.config_entry.data
        key = bytes.fromhex(data[CONF_UDP_KEY])
        iv = bytes.fromhex(data[CONF_UDP_IV])
        if self._device_addr is None:
            # 未特定: SEARCH (ブロードキャスト) で IP を探索。初回は即試行、
            # 以降は SEARCH_RETRY 間隔で再試行する (サイクル番号の割込みに依存
            # しないため、_async_update_data 側の失敗クールダウンと干渉して
            # 発見が永遠に起きない不具合を回避する)。
            now = datetime.now(timezone.utc)
            if self._last_search is None or now - self._last_search >= SEARCH_RETRY:
                self._last_search = now
                self._device_addr = self._lan_discover(data, key, iv)
                _LOGGER.info("Discovered Big Drum at %s", self._device_addr)
        if self._device_addr is None:
            raise UdpExchangeError("device address unknown (waiting for SEARCH)")
        result = udp_exchange(
            key,
            iv,
            data[CONF_SRC],
            data[CONF_DST],
            self._next_seq(),
            "MOVEINF",
            None,
            self._device_addr,
            timeout=UDP_TIMEOUT,
        )
        if not result.resp.parsed:
            return None  # NAK / off / no report
        parsed = result.resp.parsed
        # 残り時間は MOVEINF の blob だけでは取れないコースがあり、
        # CLI monitor と同様に GETINF 0005 で補完する (authoritative 源)。
        try:
            gres = udp_exchange(
                key,
                iv,
                data[CONF_SRC],
                data[CONF_DST],
                self._next_seq(),
                "GETINF",
                "0005",
                self._device_addr,
                timeout=UDP_TIMEOUT,
            )
            if gres.resp.parsed:
                rem = gres.resp.parsed.get("remainingTime")
                if rem is not None:
                    parsed["remainingTime"] = rem
        except UdpExchangeError:
            pass  # MOVEINF が取れていれば十分 (残り時間の補完失敗のみ)
        # 洗剤/柔軟剤残量 (GETINF 0001 jidoTonyuZanryoFlag: bit0=洗剤少 bit1=柔軟剤少)。
        try:
            g1 = udp_exchange(
                key,
                iv,
                data[CONF_SRC],
                data[CONF_DST],
                self._next_seq(),
                "GETINF",
                "0001",
                self._device_addr,
                timeout=UDP_TIMEOUT,
            )
            if g1.resp.parsed:
                parsed["settings"] = g1.resp.parsed
                flag = g1.resp.parsed.get("jidoTonyuZanryoFlag")
                if flag is not None:
                    parsed["jidoTonyuZanryoFlag"] = flag
        except UdpExchangeError:
            pass
        return parsed

    # -------------------------------------------------------------- cloud

    def _cloud(self) -> dict[str, Any] | None:
        data = self.config_entry.data
        grp, _, parsed = cloud.cloud_moveinf(
            data[CONF_CKDN_KEY],
            data[CONF_CKDN_IV],
            data[CONF_SRC],
            data[CONF_DST],
            ckdn_token=data.get(CONF_CKDN_TOKEN, ""),
        )
        if grp not in ("0", "3", "4"):
            return None  # e.g. 2 = power off / no report
        if not parsed:
            return None
        # 残り時間は GETINF 0005 で補完 (CLI monitor と同じ authoritative 源)。
        try:
            _ggrp, _gdata, gparsed = cloud.cloud_getinf(
                data[CONF_CKDN_KEY],
                data[CONF_CKDN_IV],
                data[CONF_SRC],
                data[CONF_DST],
                ckdn_token=data.get(CONF_CKDN_TOKEN, ""),
                sub="0005",
            )
            if gparsed:
                rem = gparsed.get("remainingTime")
                if rem is not None:
                    parsed["remainingTime"] = rem
        except cloud.CloudError:
            pass
        # 洗剤/柔軟剤残量 (GETINF 0001 jidoTonyuZanryoFlag)。
        try:
            _g1grp, _g1data, g1parsed = cloud.cloud_getinf(
                data[CONF_CKDN_KEY],
                data[CONF_CKDN_IV],
                data[CONF_SRC],
                data[CONF_DST],
                ckdn_token=data.get(CONF_CKDN_TOKEN, ""),
                sub="0001",
            )
            if g1parsed:
                parsed["settings"] = g1parsed
                flag = g1parsed.get("jidoTonyuZanryoFlag")
                if flag is not None:
                    parsed["jidoTonyuZanryoFlag"] = flag
        except cloud.CloudError:
            pass
        return parsed

    def _fw_refresh_due(self, now: datetime) -> bool:
        """FWVER の取得が必要な場合 (未取得 or スロットル超過) に True を返す。"""
        if (
            self._fw_info
            and self._fw_info.get("FWVER")
            and self._fw_info_at is not None
            and now - self._fw_info_at < FWINFO_REFRESH
        ):
            return False
        return True

    def _refresh_fw_info_if_stale(self) -> None:
        """FWINFO (本体のバージョン) をスロットル付きで取得・キャッシュする (クラウド専用)。"""
        data = self.config_entry.data
        if not data.get(CONF_CKDN_KEY):
            return
        now = datetime.now(timezone.utc)
        if not self._fw_refresh_due(now):
            return  # 有効な値がある (スロットル内)
        try:
            info = cloud.cloud_fwinfo(
                data[CONF_CKDN_KEY],
                data[CONF_CKDN_IV],
                data[CONF_SRC],
                data[CONF_DST],
                ckdn_token=data.get(CONF_CKDN_TOKEN, ""),
            )
        except cloud.CloudError as err:
            _LOGGER.debug("FWINFO failed: %s", err)
            return
        if info.get("FWVER"):
            self._fw_info = info
            self._fw_info_at = datetime.now(timezone.utc)

    def device_info(self) -> dict[str, Any]:
        """本体のファームウェア情報 (FWINFO / 本体のバージョン)。未取得時は空 dict。"""
        if not self._fw_info:
            return {}
        out: dict[str, Any] = {
            "fw_ver": self._fw_info.get("FWVER"),
            "fw_mode": self._fw_info.get("FWMODE"),
            "fw_sts": self._fw_info.get("FWSTS"),
        }
        if self._fw_info_at is not None:
            out["fetched_at"] = self._fw_info_at.isoformat()
        return out

    @property
    def last_source(self) -> str | None:
        """最後に実際に取得に成功した経路 ("lan" / "cloud")。stale 期間も保持する。"""
        return self._last_source

    def _relogin(self) -> None:
        """Re-login to refresh ckdn_* and the UDP keys in the config entry."""
        data = self.config_entry.data
        mk_hex = data.get(CONF_MEMBERS_KEY, "")
        miv_hex = data.get(CONF_MEMBERS_IV, "")
        res = cloud.login(
            data[CONF_EMAIL],
            data[CONF_PASSWORD],
            15.0,
            bytes.fromhex(mk_hex) if mk_hex else None,
            bytes.fromhex(miv_hex) if miv_hex else None,
        )
        devices = cloud.get_devices(
            res["ckdn_key"],
            res["ckdn_iv"],
            res["cspf_id"],
            res.get("ckdn_token", ""),
        )
        target = next((d for d in devices if d.dst == data[CONF_DST]), None)
        if target is None:
            raise ConfigEntryAuthFailed(
                f"dst={data[CONF_DST]} is no longer registered in the cloud"
            )
        udp = next(
            (
                p
                for p in cloud.parse_ckdn_device_key(
                    res.get("ckdn_device_key", "")
                )
                if p[0] == data[CONF_DST]
            ),
            None,
        )
        new_data = dict(data)
        new_data.update(
            {
                CONF_SRC: target.src,
                CONF_CSPF_ID: res["cspf_id"],
                CONF_CKDN_KEY: res["ckdn_key"],
                CONF_CKDN_IV: res["ckdn_iv"],
                CONF_CKDN_TOKEN: res.get("ckdn_token", ""),
            }
        )
        if udp is not None:
            new_data[CONF_UDP_KEY], new_data[CONF_UDP_IV] = udp[1], udp[2]
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
        _LOGGER.info("Re-logged in to the cloud; credentials refreshed")

    def _cloud_with_relogin(self) -> dict[str, Any] | None:
        """Cloud MOVEINF; re-logs in once on credential errors (10 min throttle)."""
        now = datetime.now(timezone.utc)
        try:
            return self._cloud()
        except cloud.CloudError as first_err:
            if (
                self._last_relogin is not None
                and now - self._last_relogin < RELOGIN_THROTTLE
            ):
                raise
            self._last_relogin = now
            try:
                self._relogin()
            except cloud.CloudError:
                raise first_err
            return self._cloud()

    # ---------------------------------------------------------------- main

    async def _async_update_data(self) -> dict[str, Any]:
        self._total_updates += 1
        data = self.config_entry.data
        mode = data.get(CONF_MODE, MODE_CLOUD)
        parsed: dict[str, Any] | None = None
        source: str | None = None
        failures: list[str] = []
        now = datetime.now(timezone.utc)
        self._last_lan_result = None
        self._last_cloud_result = None

        if data.get(CONF_UDP_KEY):
            if self._lan_cooldown_until is None or now >= self._lan_cooldown_until:
                try:
                    parsed = await asyncio.to_thread(self._lan)
                    self._last_lan_result = "ok" if parsed else "no-data"
                    if parsed:
                        source = "lan"
                        self._lan_cooldown_until = None
                except (UdpExchangeError, OSError) as err:
                    if self._device_addr is None:
                        # 宛先未特定 (SEARCH 待ち): これを失敗クールダウンにすると
                        # 次回の SEARCH 試行が常に先送りされ、発見が永遠に起きない。
                        # 再試行は _lan 内の SEARCH_RETRY 間隔で制御されるため、
                        # ここではクールダウン・失敗計数をセットしない。
                        self._last_lan_result = f"search-pending: {err}"
                        _LOGGER.debug("LAN discovery pending: %s", err)
                    else:
                        self._lan_cooldown_until = now + LAN_FAIL_COOLDOWN
                        self._last_lan_result = f"error: {err}"
                        failures.append(f"lan: {err}")
                        _LOGGER.debug("LAN update failed: %s", err)
            else:
                self._last_lan_result = "cooldown"
        else:
            self._last_lan_result = "no-key"

        if parsed is None and mode == MODE_CLOUD:
            due = self._last_cloud is None or now - self._last_cloud >= CLOUD_POLL_EVERY
            if due:
                try:
                    parsed = await asyncio.to_thread(self._cloud_with_relogin)
                    self._last_cloud_result = "ok" if parsed else "no-data"
                    if parsed:
                        source = "cloud"
                    else:
                        # クラウドが応答して「データなし」(grp≠0/3/4、例: 2=電源OFF)。
                        # ネットワークエラー (CloudError) ではなく本体OFFの確定信号なので、
                        # 電源ON時の stale 旧データを破棄する。破棄しないと同周期内で
                        # 旧データが stale として復活し、online/各センサーがオフ↔オン往復する。
                        self._last_parsed = None
                    self._last_cloud = now
                except cloud.CloudError as err:
                    self._last_cloud_result = f"error: {err}"
                    failures.append(f"cloud: {err}")
            else:
                parsed = self._last_parsed  # stale but known
                if parsed is not None:
                    source = "stale"

        # 健全性の記録 (電源/到達/取得成否の判定に供する)
        if parsed is not None and source in ("lan", "cloud"):
            self._last_parsed = parsed
            self._last_fresh_at = now
            self._last_source = source
            self._last_error = None
            self._consecutive_failures = 0
            self._stale = False
        elif parsed is not None:  # stale
            self._last_parsed = parsed
            self._stale = True
        else:
            self._stale = False
            if failures:
                self._total_failures += 1
                self._consecutive_failures += 1
                self._last_error = "; ".join(failures)

        # 本体のバージョン (FWINFO/FWVER) はクラウド専用・ファーム更新時のみ変わるため
        # スロットル付きで取得。LAN 直結でもクラウド認証情報があれば取得する (スレッドで実行し
        # イベントループをブロックしない)。
        if (
            source in ("lan", "cloud")
            and data.get(CONF_CKDN_KEY)
            and self._fw_refresh_due(now)
        ):
            await asyncio.to_thread(self._refresh_fw_info_if_stale)

        if parsed is None and source is None and failures:
            if mode == MODE_LAN or any(
                f.startswith("cloud:") for f in failures
            ):
                raise UpdateFailed("; ".join(failures))

        return {"parsed": parsed, "source": source, "ts": now}

    def health(self) -> dict[str, Any]:
        """接続・取得の健全性。diagnostics へ出力する (秘密情報は含まない)。"""
        now = datetime.now(timezone.utc)
        age = (
            (now - self._last_fresh_at).total_seconds()
            if self._last_fresh_at is not None
            else None
        )
        return {
            "mode": self.config_entry.data.get(CONF_MODE, MODE_CLOUD),
            "last_fresh_at": (
                self._last_fresh_at.isoformat() if self._last_fresh_at else None
            ),
            "seconds_since_fresh": round(age, 1) if age is not None else None,
            "stale": self._stale,
            "last_error": self._last_error,
            "consecutive_failures": self._consecutive_failures,
            "total_updates": self._total_updates,
            "total_failures": self._total_failures,
            "last_lan": self._last_lan_result,
            "last_cloud": self._last_cloud_result,
            "last_source": self._last_source,
            "device_addr": self._device_addr,
            "lan_cooldown_active": (
                self._lan_cooldown_until is not None
                and now < self._lan_cooldown_until
            ),
        }

    # ----------------------------------------------------------- operation

    async def async_get_course_catalog(self, force: bool = False) -> dict[str, Any]:
        """COURSEINF でコースカタログを取得しキャッシュする (クラウド専用)。"""
        data = self.config_entry.data
        if not data.get(CONF_CKDN_KEY):
            raise cloud.CloudError(
                "course catalog requires cloud credentials (COURSEINF is cloud-only)"
            )
        if self._course_cache is None or force:
            cat = await asyncio.to_thread(
                cloud.cloud_courseinf,
                data[CONF_CKDN_KEY],
                data[CONF_CKDN_IV],
                data[CONF_DST][:8],
                "",
                ckdn_token=data.get(CONF_CKDN_TOKEN, ""),
            )
            if not isinstance(cat, dict) or not cat.get("course_set"):
                raise cloud.CloudError(
                    f"COURSEINF returned no course_set: {str(cat)[:200]!r}"
                )
            self._course_cache = cat
            self._course_label_maps = course.build_course_label_maps(cat)
        return self._course_cache

    def course_name(self, course_no) -> tuple[str, str] | None:
        """キャッシュ済みカタログから course_no の (日本語名, タブ) を返す。

        カタログ未取得・未登録コースは None (呼び出し側が生番号にフォールバック)。
        """
        if self._course_cache is None:
            return None
        return course.find_course_set_name(self._course_cache, course_no)

    def setting_label_maps_for(self, course_no) -> dict[str, dict[str, str]]:
        """指定コースの {field: {value: name}} を返す (未取得・未登録は空 dict)。

        値→名称の対応はコースごとに異なるため、現在選択コース (selCourse) で解決する。
        """
        if not self._course_label_maps:
            return {}
        return self._course_label_maps.get(str(course_no)) or {}

    async def async_prefetch_course_catalog(self) -> None:
        """背景でコースカタログを事前取得する。

        「選択コース」を日本語名で表示するためのみ。失敗しても状態表示は壊さず、
        生番号 (4桁) にフォールバックする (ログのみ)。
        """
        try:
            await self.async_get_course_catalog()
        except Exception as err:  # noqa: BLE001 - ベストエフォートの背景タスク
            _LOGGER.warning(
                "course catalog prefetch failed (raw course no will be shown): %s", err
            )

    def _lan_ctrl(self, ctrlc: str, ctrld: str) -> dict[str, Any]:
        """UDP 経由の CTRLINS (TIME=hex)。成功=GRP=ACK。"""
        data = self.config_entry.data
        key = bytes.fromhex(data[CONF_UDP_KEY])
        iv = bytes.fromhex(data[CONF_UDP_IV])
        if self._device_addr is None:
            # 宛先未特定ならまず SEARCH で発見 (ユーザー操作なので再試行間隔は
            # 設けない)。_last_search を同期して直後のポーリング側と二重ブロード
            # カストを避ける。失敗時は例外で async_send_ctrl のクラウドフォール
            # バックへ。
            self._last_search = datetime.now(timezone.utc)
            self._device_addr = self._lan_discover(data, key, iv)
            _LOGGER.info("Discovered Big Drum at %s", self._device_addr)
        result = udp_exchange(
            key,
            iv,
            data[CONF_SRC],
            data[CONF_DST],
            self._next_seq(),
            "CTRLINS",
            f"{now_string_hex()},{ctrlc},{ctrld}",
            self._device_addr,
            timeout=UDP_TIMEOUT,
        )
        return {
            "transport": "lan",
            "grp": result.resp.grp,
            "success": "",
            "data": result.resp.data,
            "accepted": result.resp.grp == "ACK",
        }

    def _cloud_ctrl(self, ctrlc: str, ctrld: str) -> dict[str, Any]:
        """クラウド経由の CTRLINS (TIME=dex)。成功=SUCCESS=0 + DATA=FF。"""
        data = self.config_entry.data
        res = cloud.cloud_ctrl(
            data[CONF_CKDN_KEY],
            data[CONF_CKDN_IV],
            data[CONF_SRC],
            data[CONF_DST],
            ctrlc,
            ctrld,
            now_string_dex(),
            ckdn_token=data.get(CONF_CKDN_TOKEN, ""),
        )
        res["transport"] = "cloud"
        res["accepted"] = res.get("success") == "0" and res.get("data") == "FF"
        return res

    async def async_send_ctrl(self, ctrlc: str, ctrld: str) -> dict[str, Any]:
        """CTRLINS を送信する。LAN UDP を優先、不可ならクラウドへフォールバック。"""
        data = self.config_entry.data
        if data.get(CONF_UDP_KEY):
            try:
                return await asyncio.to_thread(self._lan_ctrl, ctrlc, ctrld)
            except (UdpExchangeError, OSError) as err:
                if not data.get(CONF_CKDN_KEY):
                    raise
                _LOGGER.warning("LAN CTRLINS failed (%s); falling back to cloud", err)
        if not data.get(CONF_CKDN_KEY):
            raise cloud.CloudError(
                "no transport available (no UDP key and no cloud credentials)"
            )
        return await asyncio.to_thread(self._cloud_ctrl, ctrlc, ctrld)

    async def async_start_course(
        self,
        course_no: str,
        options: dict[str, str] | None = None,
        marker: str = "0000",
        delay_minutes: int | None = None,
    ) -> dict[str, Any]:
        """コース選択 + 開始 (CTRLC=0003)。カタログから 42桁 CTRLD を組み立て送信する。

        予約開始は CTRLD 先頭4桁 (marker) に「開始までの分数」の %04X を置く
        (アプリ BD00313AFragment と同一。即時開始="0000")。``delay_minutes``
        (10進・分) を渡すとこちらを優先して marker に変換し、コースの予約最低
        リードタイム (yoyaku.initial) と上限 (1440分=24時間) を検証する。
        """
        cat = await self.async_get_course_catalog()
        found = course.find_course(cat, course_no)
        if found is None:
            raise ValueError(
                f"course_no={course_no} is not in the course catalog"
            )
        if delay_minutes is not None:
            if marker not in (None, "", "0000"):
                raise ValueError(
                    "specify either delay_minutes or marker, not both"
                )
            m = int(delay_minutes)
            if not 1 <= m <= 1440:
                raise ValueError(
                    f"delay_minutes={delay_minutes} out of range (1..1440)"
                )
            yoyaku = found.get("yoyaku") or {}
            minimum = int(str(yoyaku.get("initial") or "0"))
            if m <= minimum:
                raise ValueError(
                    f"course {course_no} needs more than {minimum} minutes "
                    f"until start (got {m})"
                )
            marker = f"{m:04X}"
        ctrld = course.build_course_ctrld(found, options, marker=marker)
        return await self.async_send_ctrl("0003", ctrld)

    async def async_stop(self) -> dict[str, Any]:
        """停止 (CTRLC=0001 / CTRLD=03)。"""
        return await self.async_send_ctrl("0001", "03")
