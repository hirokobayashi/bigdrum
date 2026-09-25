"""Binary sensors for the Hitachi Big Drum integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import CONF_DST, CONF_NICKNAME, DOMAIN, MANUFACTURER, MODEL, mac_from_dst

# statusCode: 06/07 = running. process: FA = fluff-guard drying, FB = dispenser care
RUNNING_STATUS_CODES = ("06", "07")
RUNNING_PROCESS_CODES = ("FA", "FB")

# GETINF 0001 の jidoTonyuZanryoFlag ビット (自動投入部の残量「少」フラグ)
BIT_DETERGENT_LOW = 0b1
BIT_SOFTENER_LOW = 0b10

# リモート操作: 日立アプリは remoteOperationStatus=="00" のとき
# M0065「操作できない」を出して開始/停止をブロックする (SUCCESS コード "6" と同一)。
REMOTE_OP_BLOCKED = "00"

BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="running",
        translation_key="running",
        device_class=BinarySensorDeviceClass.RUNNING,
    ),
    BinarySensorEntityDescription(
        key="remote_possible", translation_key="remote_possible"
    ),
    BinarySensorEntityDescription(
        key="detergent_low",
        translation_key="detergent_low",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    BinarySensorEntityDescription(
        key="softener_low",
        translation_key="softener_low",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    # 電源ONかつLAN/クラウド経由で状態を正常取得中 (到達可能) を示す。
    # 電源OFF・未報告 (クラウド GRP≠0/3/4) は確定の off (常時 available)。
    BinarySensorEntityDescription(
        key="online", translation_key="online"
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: DataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        BigDrumBinarySensor(coordinator, d) for d in BINARY_SENSORS
    )


class BigDrumBinarySensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DataUpdateCoordinator,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        dst = coordinator.config_entry.data[CONF_DST]
        self._attr_unique_id = f"{dst}_{description.key}"
        # MAC 相当 (dst[8:20]) を接続として登録 (センサー側と同じデバイス接続)。
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
        # 動的反映 (センサー側と同じデバイス・同一の値)。
        info = dict(self._attr_device_info)
        info["sw_version"] = (self.coordinator.device_info() or {}).get("fw_ver")
        return DeviceInfo(**info)

    @property
    def available(self) -> bool:
        # online は電源/到達可否そのものを示すので常時有効 (電源OFF・無応答 = off)
        if self.entity_description.key == "online":
            return True
        data = self.coordinator.data or {}
        parsed = data.get("parsed")
        if not (self.coordinator.last_update_success and parsed):
            return False
        # 電源OFF・未報告 (クラウド GRP≠0/3/4): 状態データなし
        # (表示するのは status センサーの「本体の電源が入っていないか…」のみ)。
        if parsed.get("noReport"):
            return False
        # 洗剤/柔軟剤残量は GETINF 0001 に依存 (取得失敗時は判定不能 → 非利用可)。
        if self.entity_description.key in ("detergent_low", "softener_low"):
            return (data.get("parsed") or {}).get("jidoTonyuZanryoFlag") is not None
        return True

    @property
    def is_on(self) -> bool:
        key = self.entity_description.key
        if key == "online":
            # 直近の更新が成功 (電源ONで状態取得に到達) しているか。
            # 電源OFF・未報告 (クラウドの確定応答・noReport) は off。
            data = self.coordinator.data
            parsed = (data or {}).get("parsed")
            return (
                self.coordinator.last_update_success is True
                and bool(data)
                and parsed is not None
                and not parsed.get("noReport")
            )
        parsed: dict[str, Any] = (self.coordinator.data or {}).get("parsed") or {}
        if key == "running":
            return (
                parsed.get("statusCode") in RUNNING_STATUS_CODES
                or parsed.get("process") in RUNNING_PROCESS_CODES
            )
        if key == "remote_possible":
            val = parsed.get("remoteOperationStatus")
            return val is not None and val != REMOTE_OP_BLOCKED
        if key == "detergent_low":
            return bool((parsed.get("jidoTonyuZanryoFlag") or 0) & BIT_DETERGENT_LOW)
        if key == "softener_low":
            return bool((parsed.get("jidoTonyuZanryoFlag") or 0) & BIT_SOFTENER_LOW)
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        return {"source": data.get("source")}
