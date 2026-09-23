"""Diagnostics support for the Hitachi Big Drum integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CKDN_IV,
    CONF_CKDN_KEY,
    CONF_CKDN_TOKEN,
    CONF_DST,
    CONF_PASSWORD,
    CONF_UDP_IV,
    CONF_UDP_KEY,
    DOMAIN,
    mac_from_dst,
)

REDACTED = "**********"
SECRET_KEYS = (
    CONF_PASSWORD,
    CONF_CKDN_KEY,
    CONF_CKDN_IV,
    CONF_CKDN_TOKEN,
    CONF_UDP_KEY,
    CONF_UDP_IV,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    data = dict(entry.data)
    for key in SECRET_KEYS:
        if key in data:
            data[key] = REDACTED
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    return {
        "entry": data,
        # 製品の MAC 相当 (dst の [8:20]・アプリの「確認」画面と同一)。
        "mac": mac_from_dst(data.get(CONF_DST, "")),
        "coordinator": {
            "last_update_success": (
                coordinator.last_update_success if coordinator else None
            ),
            "health": coordinator.health() if coordinator else None,
            "data": coordinator.data if coordinator else None,
            # 本体のバージョン (FWINFO / FWVER) 等。クラウド経由・スロットル付き取得。
            "device": coordinator.device_info() if coordinator else {},
        },
    }
