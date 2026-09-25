"""Sensors for the Hitachi Big Drum integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.util import dt as dt_util

from .const import CONF_DST, CONF_NICKNAME, DOMAIN, MANUFACTURER, MODEL, mac_from_dst

# GETINF 0001 フィールド -> コースカタログの disp フィールド (値ラベル解決用)。
# 名前の異なる項目 (autoOsouji→jido_osouji 等) をマッピング、同名はそのまま。
# 洗剤/柔軟剤 (Kijun) は disp 参照ではなく 16進投入量 (mL) なのでここには載せない。
_SETTING_FIELD_MAP = {
    "arai": "arai",
    "susugi": "susugi",
    "dassui": "dassui",
    "kanso": "kanso",
    "suiryo": "suiryo",
    "sensor": "sensor",
    "oyutori": "oyutori",
    "autoOsouji": "jido_osouji",
    "onsuiMisth": "onsui_mist",
}

# 16進投入量 (mL) として扱う GETINF 0001 フィールド (コース非依存)。
# アプリは getSenzaiKijun10() = hex->dec にして「N mL」と表示 (BD01070:311 等)。
_SETTING_ML_FIELDS = {"senzaiKijun", "nyunanzaiKijun"}

# 洗剤/柔軟剤は要約 (コース設定) から外し独立センサー化。key -> GETINF 0001 フィールド。
_ML_SENSOR_BY_KEY = {"detergent": "senzaiKijun", "softener": "nyunanzaiKijun"}
_ML_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="detergent", translation_key="detergent", icon="mdi:bucket"
    ),
    SensorEntityDescription(
        key="softener", translation_key="softener", icon="mdi:bucket-outline"
    ),
)

# GETINF 0001 フィールド -> 日本語表示名 (コース設定の属性キー用・PROTOCOL.md §8.4 準拠)。
_SETTING_FIELD_LABELS = {
    "arai": "洗い",
    "susugi": "すすぎ",
    "dassui": "脱水",
    "kanso": "乾燥",
    "suiryo": "水温",
    "sensor": "センサー",
    "oyutori": "お取り置き",
    "autoOsouji": "自動お掃除",
    "onsuiMisth": "お湯ミスト",
}

# 要約 (state) に表示する主要なコース設定の表示順。
_SETTING_SUMMARY_FIELDS = ("arai", "dassui", "kanso", "susugi")

# 通信経路 (coordinator の source / last_source) -> 表示名。LAN 直接 / クラウド。
_SOURCE_LABELS = {"lan": "LAN", "cloud": "Cloud"}


def _ml_label(raw: Any) -> str:
    """16進投入量 -> 'N mL' (アプリの getSenzaiKijun10() 相当)。非 hex は生値。"""
    try:
        return f"{int(str(raw), 16)} mL"
    except (TypeError, ValueError):
        return str(raw)


def _setting_value(
    st: dict[str, Any], field: str, maps: dict[str, dict[str, str]]
) -> Any:
    """コース設定 1 フィールドの表示値: disp で解れば名称、さもなくば生値。"""
    raw = st.get(field)
    if raw is None:
        return None
    name = maps.get(_SETTING_FIELD_MAP.get(field, ""), {}).get(str(raw).upper())
    return name if name is not None else raw


SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="status", translation_key="status", icon="mdi:washing-machine"
    ),
    SensorEntityDescription(
        key="phase", translation_key="phase", icon="mdi:progress-clock"
    ),
    SensorEntityDescription(key="selCourse", translation_key="course"),
    SensorEntityDescription(
        key="remainingTime",
        translation_key="remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    SensorEntityDescription(
        key="deviceTime",
        translation_key="state_change",
        device_class=SensorDeviceClass.TIMESTAMP,
        # 状態変化時刻は運用情報というより本体診断用なので Diagnostics セクションへ。
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(key="aiInfo", translation_key="ai_info"),
    SensorEntityDescription(key="info", translation_key="notice"),
    SensorEntityDescription(
        key="settings", translation_key="settings", icon="mdi:tune-variant"
    ),
    *_ML_SENSORS,
    # 通信経路 (LAN/クラウド)。DIAGNOSTIC = Diagnostics セクションに表示。
    # 最後に実際に取得に成功した経路を stale 期間も含め保持表示。
    SensorEntityDescription(
        key="transport",
        translation_key="transport",
        icon="mdi:lan-connect",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(BigDrumSensor(coordinator, d) for d in SENSORS)


class BigDrumSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_entity_category = description.entity_category
        dst = coordinator.config_entry.data[CONF_DST]
        self._attr_unique_id = f"{dst}_{description.key}"
        # MAC 相当 (dst[8:20]) を接続として登録。「デバイス 情報」に MAC を表示し、
        # 同じ MAC のネットワーク機器 (OPNsense 等のトラッカー) と Linked devices で紐づく。
        mac = mac_from_dst(dst)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, dst)},
            name=coordinator.config_entry.data.get(CONF_NICKNAME) or "Big Drum",
            manufacturer=MANUFACTURER,
            model=MODEL,
            connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
        )

    @property
    def device_info(self) -> DeviceInfo:
        # 本体のバージョン (FWVER) をデバイスカードの「ファームウェア」(sw_version) として
        # 動的反映。クラウド経由・スロットル取得のため __init__ 時未取得 → 取得後随時更新。
        info = dict(self._attr_device_info)
        info["sw_version"] = (self.coordinator.device_info() or {}).get("fw_ver")
        return DeviceInfo(**info)

    @property
    def available(self) -> bool:
        # 通信経路 (DIAGNOSTIC): 一度でも LAN/クラウドで取得成功してれば表示。
        # 状態更新の成否に依存しない (stale 期間も最後に成功した経路を保持)。
        if self.entity_description.key == "transport":
            return self.coordinator.last_source is not None
        data = self.coordinator.data or {}
        parsed = data.get("parsed")
        if not (self.coordinator.last_update_success and parsed):
            return False
        # 電源OFF・未報告 (クラウド GRP≠0/3/4・例 2): 運転状態の
        # 「本体の電源が入っていないか…」メッセージ (statusCode=00) のみ表示し、
        # それ以外の状態データは存在しない (公式アプリと同じ表示)。
        if parsed.get("noReport"):
            return self.entity_description.key == "status"
        # 残り時間は 0xFFFF sentinel(None) のときのみ「計測なし」= 非表示。真の 0 はそのまま表示。
        if self.entity_description.key == "remainingTime":
            rem = (data.get("parsed") or {}).get("remainingTime")
            return rem is not None
        # 運転フェーズ: フェーズ未定 (待機中など) は native_value が None になり「不明」表示。
        # デバイスに到達できているため「切断」(unavailable) にしない。
        # コース設定 (GETINF 0001) は未取得のときのみ非表示。
        if self.entity_description.key == "settings":
            return bool((data.get("parsed") or {}).get("settings"))
        # 洗剤/柔軟剤 (16進投入量・コース非依存): そのフィールド値が存在するときのみ。
        if self.entity_description.key in _ML_SENSOR_BY_KEY:
            st = (data.get("parsed") or {}).get("settings") or {}
            return st.get(_ML_SENSOR_BY_KEY[self.entity_description.key]) is not None
        return True

    @property
    def native_value(self) -> Any:
        parsed: dict[str, Any] = (self.coordinator.data or {}).get("parsed") or {}
        key = self.entity_description.key
        if key == "transport":
            src = self.coordinator.last_source
            return _SOURCE_LABELS.get(src, src) if src is not None else None
        if key == "remainingTime":
            return parsed.get("remainingTime")
        if key == "deviceTime":
            try:
                device_dt = datetime.strptime(
                    f"{parsed.get('date', '')} {parsed.get('time', '')}",
                    "%Y/%m/%d %H:%M",
                )
            except (TypeError, ValueError):
                return None
            return device_dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        if key == "aiInfo":
            infos: list[str] = parsed.get("aiInfos") or []
            return " / ".join(infos)
        if key == "selCourse":
            return self._course_display()
        if key in _ML_SENSOR_BY_KEY:
            raw = (parsed.get("settings") or {}).get(_ML_SENSOR_BY_KEY[key])
            return _ml_label(raw) if raw is not None else None
        if key == "settings":
            st = parsed.get("settings") or {}
            maps = self.coordinator.setting_label_maps_for(parsed.get("selCourse") or "")
            parts = []
            for field in _SETTING_SUMMARY_FIELDS:
                val = _setting_value(st, field, maps)
                if val is not None:
                    parts.append(f"{_SETTING_FIELD_LABELS.get(field, field)} {val}")
            return " / ".join(parts) if parts else None
        return parsed.get(key)

    def _course_display(self) -> Any:
        """選択コースをコースカタログの日本語名で表示 (未取得・未登録は生番号)。"""
        parsed: dict[str, Any] = (self.coordinator.data or {}).get("parsed") or {}
        raw = str(parsed.get("selCourse") or "")
        res = self.coordinator.course_name(raw) if raw else None
        if res and res[0]:
            return res[0]
        return raw

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        parsed: dict[str, Any] = data.get("parsed") or {}
        if self.entity_description.key == "transport":
            health = self.coordinator.health()
            return {
                "source": data.get("source"),
                "mode": health.get("mode"),
                "device_addr": health.get("device_addr"),
                "last_lan": health.get("last_lan"),
            }
        attrs: dict[str, Any] = {"source": data.get("source")}
        if self.entity_description.key == "selCourse":
            raw = str(parsed.get("selCourse") or "")
            attrs["course_no"] = raw or None
            res = self.coordinator.course_name(raw) if raw else None
            if res and res[1]:
                attrs["course_tab"] = res[1]
        elif self.entity_description.key == "settings":
            st = parsed.get("settings") or {}
            maps = self.coordinator.setting_label_maps_for(parsed.get("selCourse") or "")
            for field in st:
                if field in _SETTING_FIELD_LABELS:
                    attrs[_SETTING_FIELD_LABELS[field]] = _setting_value(
                        st, field, maps
                    )
                elif field not in _SETTING_ML_FIELDS:
                    attrs[field] = st[field]
        elif self.entity_description.key in _ML_SENSOR_BY_KEY:
            raw = (parsed.get("settings") or {}).get(
                _ML_SENSOR_BY_KEY[self.entity_description.key]
            )
            if raw is not None:
                attrs["value"] = raw
        return attrs
