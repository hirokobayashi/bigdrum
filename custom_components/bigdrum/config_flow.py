"""Config flow for the Hitachi Big Drum integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.data_entry_flow import FlowResult

from . import cloud
from .const import (
    CONF_BROADCAST_ADDR,
    CONF_CKDN_IV,
    CONF_CKDN_KEY,
    CONF_CKDN_TOKEN,
    CONF_CSPF_ID,
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
    DOMAIN,
    MODE_CLOUD,
    MODE_LAN,
)

_LOGGER = logging.getLogger(__name__)


class BigDrumConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._login_res: dict[str, str] = {}
        self._devices: list[cloud.DevInfo] = []
        self._cloud_email = ""
        self._cloud_password = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            # 選択 UI は表示名 (Cloud/LAN) を渡すが、内部の mode 値 (cloud/lan) へ
            # 小文字化して対応する (既存エントリの mode 値は小文字のまま維持)。
            mode = str(user_input["mode"]).lower()
            if mode == MODE_CLOUD:
                return await self.async_step_cloud()
            return await self.async_step_lan()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    # 表示は Cloud/LAN。内部値は handler が小文字化 (cloud/lan) する。
                    vol.Required("mode", default="Cloud"): vol.In(["Cloud", "LAN"])
                }
            ),
        )

    async def async_step_cloud(
        self, user_input: dict[str, Any] | None = None, errors: dict[str, str] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._cloud_email = user_input[CONF_EMAIL]
            self._cloud_password = user_input[CONF_PASSWORD]
            members_key_hex = (user_input.get(CONF_MEMBERS_KEY) or "").strip()
            members_iv_hex = (user_input.get(CONF_MEMBERS_IV) or "").strip()
            # 指定があれば hex 検証、未指定ならモジュール既定値を使用
            if members_key_hex or members_iv_hex:
                valid = len(members_key_hex) == 64 and len(members_iv_hex) == 32
                if valid:
                    try:
                        bytes.fromhex(members_key_hex)
                        bytes.fromhex(members_iv_hex)
                    except ValueError:
                        valid = False
                if not valid:
                    errors = {"base": "invalid_hex"}
                else:
                    mk = bytes.fromhex(members_key_hex)
                    miv = bytes.fromhex(members_iv_hex)
                    try:
                        self._login_res = await asyncio.to_thread(
                            cloud.login,
                            self._cloud_email,
                            self._cloud_password,
                            15.0,
                            mk,
                            miv,
                        )
                        self._devices = await asyncio.to_thread(
                            cloud.get_devices,
                            self._login_res["ckdn_key"],
                            self._login_res["ckdn_iv"],
                            self._login_res["cspf_id"],
                            self._login_res.get("ckdn_token", ""),
                        )
                    except cloud.CloudError as err:
                        errors = (
                            {"base": "invalid_auth"}
                            if "9E3" in str(err)
                            else {"base": "cannot_connect"}
                        )
                    else:
                        self._members_key_hex = members_key_hex
                        self._members_iv_hex = members_iv_hex
                        if not self._devices:
                            errors = {"base": "no_devices"}
                        else:
                            return await self.async_step_cloud_device()
            else:
                if not cloud.MEMBERS_KEY or not cloud.MEMBERS_IV:
                    errors = {"base": "missing_members_key"}
                else:
                    try:
                        self._login_res = await asyncio.to_thread(
                            cloud.login, self._cloud_email, self._cloud_password
                        )
                        self._devices = await asyncio.to_thread(
                            cloud.get_devices,
                            self._login_res["ckdn_key"],
                            self._login_res["ckdn_iv"],
                            self._login_res["cspf_id"],
                            self._login_res.get("ckdn_token", ""),
                        )
                    except cloud.CloudError as err:
                        errors = (
                            {"base": "invalid_auth"}
                            if "9E3" in str(err)
                            else {"base": "cannot_connect"}
                        )
                    else:
                        self._members_key_hex = ""
                        self._members_iv_hex = ""
                        if not self._devices:
                            errors = {"base": "no_devices"}
                        else:
                            return await self.async_step_cloud_device()
        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_MEMBERS_KEY): str,
                vol.Optional(CONF_MEMBERS_IV): str,
            }
        )
        return self.async_show_form(
            step_id="cloud", data_schema=schema, errors=errors or {}
        )

    async def async_step_cloud_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            dst = user_input[CONF_DST]
            dev = next(d for d in self._devices if d.dst == dst)
            udp = next(
                (
                    p
                    for p in cloud.parse_ckdn_device_key(
                        self._login_res.get("ckdn_device_key", "")
                    )
                    if p[0] == dst
                ),
                None,
            )
            await self.async_set_unique_id(dst)
            self._abort_if_unique_id_configured()
            data = {
                CONF_MODE: MODE_CLOUD,
                CONF_EMAIL: self._cloud_email,
                CONF_PASSWORD: self._cloud_password,
                CONF_MEMBERS_KEY: getattr(self, "_members_key_hex", ""),
                CONF_MEMBERS_IV: getattr(self, "_members_iv_hex", ""),
                CONF_CSPF_ID: self._login_res["cspf_id"],
                CONF_CKDN_KEY: self._login_res["ckdn_key"],
                CONF_CKDN_IV: self._login_res["ckdn_iv"],
                CONF_CKDN_TOKEN: self._login_res.get("ckdn_token", ""),
                CONF_SRC: dev.src,
                CONF_DST: dst,
                CONF_NICKNAME: dev.nick_name or dev.user_name or dst,
                CONF_UDP_KEY: udp[1] if udp else "",
                CONF_UDP_IV: udp[2] if udp else "",
                CONF_DEVICE_ADDR: (user_input.get(CONF_DEVICE_ADDR) or "").strip(),
                CONF_BROADCAST_ADDR: (
                    user_input.get(CONF_BROADCAST_ADDR) or DEFAULT_BROADCAST_ADDR
                ).strip(),
            }
            return self.async_create_entry(
                title=dev.nick_name or dev.user_name or dst, data=data
            )
        return self.async_show_form(
            step_id="cloud_device",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DST): vol.In([d.dst for d in self._devices]),
                    vol.Optional(CONF_DEVICE_ADDR): str,
                    vol.Optional(
                        CONF_BROADCAST_ADDR, default=DEFAULT_BROADCAST_ADDR
                    ): str,
                }
            ),
            description_placeholders={"count": len(self._devices)},
        )

    async def async_step_lan(
        self, user_input: dict[str, Any] | None = None, errors: dict[str, str] | None = None
    ) -> FlowResult:
        if user_input is not None:
            key = user_input[CONF_UDP_KEY].strip()
            iv = user_input[CONF_UDP_IV].strip()
            valid = len(key) == 64 and len(iv) == 32
            if valid:
                try:
                    bytes.fromhex(key)
                    bytes.fromhex(iv)
                except ValueError:
                    valid = False
            if not valid:
                errors = {"base": "invalid_hex"}
            else:
                dst = user_input[CONF_DST].strip()
                await self.async_set_unique_id(dst)
                self._abort_if_unique_id_configured()
                data = {
                    CONF_MODE: MODE_LAN,
                    CONF_SRC: user_input[CONF_SRC].strip(),
                    CONF_DST: dst,
                    CONF_NICKNAME: (user_input.get(CONF_NICKNAME) or "").strip(),
                    CONF_UDP_KEY: key,
                    CONF_UDP_IV: iv,
                    CONF_DEVICE_ADDR: (user_input.get(CONF_DEVICE_ADDR) or "").strip(),
                    CONF_BROADCAST_ADDR: (
                        user_input.get(CONF_BROADCAST_ADDR)
                        or DEFAULT_BROADCAST_ADDR
                    ).strip(),
                }
                return self.async_create_entry(
                    title=data[CONF_NICKNAME] or f"...{dst[-6:]}", data=data
                )
        return self.async_show_form(
            step_id="lan",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DST): str,
                    vol.Required(CONF_SRC): str,
                    vol.Required(CONF_UDP_KEY): str,
                    vol.Required(CONF_UDP_IV): str,
                    vol.Optional(CONF_NICKNAME): str,
                    vol.Optional(CONF_DEVICE_ADDR): str,
                    vol.Optional(
                        CONF_BROADCAST_ADDR, default=DEFAULT_BROADCAST_ADDR
                    ): str,
                }
            ),
            errors=errors or {},
        )
