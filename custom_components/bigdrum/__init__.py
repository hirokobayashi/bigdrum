"""Hitachi Big Drum (Washing Concierge) integration for Home Assistant."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)

from homeassistant.helpers import device_registry as devreg

from . import cloud, course
from .const import DOMAIN, PLATFORMS, SERVICE_START, SERVICE_STOP
from .coordinator import BigDrumCoordinator
from .udp import UdpExchangeError

_LOGGER = logging.getLogger(__name__)

# コース設定の 11 項目 (2桁 hex)。未知名のキーは拒否 (build_course_ctrld は
# 無視するだけなので、誤ったキーの自動実行を明確なエラーにする)。
OPTIONS_SCHEMA = vol.Schema(
    {vol.Optional(field): vol.Coerce(str) for field in course.COURSE_FIELDS + course.TAIL_FIELDS}
)
# marker (4桁 hex) と delay_minutes (10進・分) は同じ予約開始を表現するため
# 両立禁止 (async_start_course が検証する)。
START_SCHEMA = vol.Schema(
    {
        vol.Required("course_no"): str,
        vol.Optional("options"): OPTIONS_SCHEMA,
        vol.Optional("delay_minutes"): vol.Coerce(int),
        vol.Optional("marker"): str,
        vol.Optional("device_id"): str,
        vol.Optional("entry_id"): str,
    }
)
STOP_SCHEMA = vol.Schema(
    {
        vol.Optional("device_id"): str,
        vol.Optional("entry_id"): str,
    }
)


def _get_coordinator(hass: HomeAssistant, data: dict[str, Any]) -> BigDrumCoordinator:
    """Resolve the target entry's coordinator from the service call data.

    優先順: ``device_id`` (UI の device セレクタ・デバイスレジストリ経由)
    > ``entry_id`` > 1 台のみならその台。
    """
    if data.get("device_id"):
        entries = hass.data.get(DOMAIN, {})
        dev_reg = devreg.async_get(hass)
        device = dev_reg.async_get(data["device_id"])
        if device is None:
            raise ServiceValidationError(f"unknown device_id: {data['device_id']}")
        ours = {
            eid for eid in device.config_entries if eid in entries
        }
        if len(ours) == 1:
            return entries[next(iter(ours))]
        if not ours:
            raise ServiceValidationError(
                "the selected device has no Big Drum entry"
            )
        raise ServiceValidationError(
            "the selected device is shared by multiple Big Drum entries; use entry_id"
        )
    entries = hass.data.get(DOMAIN, {})
    eid = data.get("entry_id")
    if eid is not None:
        if eid not in entries:
            raise ServiceValidationError(f"unknown entry_id: {eid}")
        return entries[eid]
    if len(entries) == 1:
        return next(iter(entries.values()))
    raise ServiceValidationError(
        "multiple Big Drum devices are configured; specify entry_id"
    )


async def _service_start(call) -> None:
    coord = _get_coordinator(call.hass, call.data)
    try:
        res = await coord.async_start_course(
            call.data["course_no"],
            call.data.get("options") or {},
            marker=call.data.get("marker") or "0000",
            delay_minutes=call.data.get("delay_minutes"),
        )
    except ValueError as err:
        raise ServiceValidationError(str(err)) from err
    except (UdpExchangeError, OSError) as err:
        raise HomeAssistantError(f"LAN transport failed: {err}") from err
    except cloud.CloudError as err:
        raise HomeAssistantError(str(err)) from err
    if not res.get("accepted"):
        raise HomeAssistantError(
            f"start was not accepted via {res.get('transport')}: {res}"
        )


async def _service_stop(call) -> None:
    coord = _get_coordinator(call.hass, call.data)
    try:
        res = await coord.async_stop()
    except (UdpExchangeError, OSError) as err:
        raise HomeAssistantError(f"LAN transport failed: {err}") from err
    except cloud.CloudError as err:
        raise HomeAssistantError(str(err)) from err
    if not res.get("accepted"):
        raise HomeAssistantError(
            f"stop was not accepted via {res.get('transport')}: {res}"
        )


async def async_register_services(hass: HomeAssistant) -> None:
    """Register the bigdrum.start / bigdrum.stop services (once)."""
    if hass.services.has_service(DOMAIN, SERVICE_START):
        return
    hass.services.async_register(
        DOMAIN, SERVICE_START, _service_start, schema=START_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP, _service_stop, schema=STOP_SCHEMA
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = BigDrumCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(f"could not fetch initial state: {err}") from err
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await async_register_services(hass)
    # 「選択コース」の日本語名表示用に、コースカタログを背景で事前取得する。
    # 失敗しても非ブロッキング (失敗時は生番号表示にフォールバック)。
    hass.async_create_background_task(
        coordinator.async_prefetch_course_catalog(),
        f"bigdrum course catalog {entry.entry_id}",
        eager_start=True,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
