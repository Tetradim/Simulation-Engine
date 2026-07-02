from __future__ import annotations

import asyncio
from typing import Any

from .recorder_models import normalize_channel_ids


DISCORD_API_BASE = "https://discord.com/api/v10"


class DiscordConnectionDiagnostics:
    async def run(
        self,
        *,
        token: str,
        channel_ids: list[str],
        record_all_channels: bool,
        timeout_seconds: float = 12.0,
    ) -> dict[str, Any]:
        clean_token = str(token or "").strip()
        channel_ids = normalize_channel_ids(channel_ids)
        result: dict[str, Any] = {
            "ok": False,
            "status": "token_missing",
            "token_configured": bool(clean_token),
            "record_all_channels": record_all_channels,
            "channel_ids": channel_ids,
            "channels": [],
            "bot_user": None,
            "diagnostic": "discord_rest",
        }
        if not clean_token:
            return result
        if not record_all_channels and not channel_ids:
            result["status"] = "channel_ids_missing"
            return result

        try:
            return await asyncio.wait_for(
                self._run_rest_probe(
                    token=clean_token,
                    channel_ids=channel_ids,
                    record_all_channels=record_all_channels,
                    result=result,
                    timeout_seconds=timeout_seconds,
                ),
                timeout=timeout_seconds + 1,
            )
        except TimeoutError:
            result["status"] = "timeout"
            return result
        except Exception as exc:
            result["status"] = "diagnostic_failed"
            result["error"] = _safe_error(exc)
            return result

    async def _run_rest_probe(
        self,
        *,
        token: str,
        channel_ids: list[str],
        record_all_channels: bool,
        result: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        import aiohttp

        headers = {"Authorization": f"Bot {token}", "User-Agent": "Sentinel-Archive"}
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(f"{DISCORD_API_BASE}/users/@me") as response:
                if response.status in {401, 403}:
                    result["status"] = "invalid_token"
                    return result
                if response.status < 200 or response.status >= 300:
                    result["status"] = "discord_api_error"
                    result["error"] = f"users/@me returned HTTP {response.status}"
                    return result
                user = await response.json()
                result["bot_user"] = {
                    "id": str(user.get("id", "")),
                    "username": str(user.get("username", "")),
                }

            if record_all_channels:
                result["ok"] = True
                result["status"] = "authenticated"
                return result

            channel_checks = []
            for channel_id in channel_ids:
                channel_checks.append(await self._check_channel(session, str(channel_id)))
            result["channels"] = channel_checks
            result["ok"] = bool(channel_checks) and all(item.get("accessible") for item in channel_checks)
            result["status"] = "ok" if result["ok"] else "channel_access_failed"
            return result

    async def _check_channel(self, session: Any, channel_id: str) -> dict[str, Any]:
        async with session.get(f"{DISCORD_API_BASE}/channels/{channel_id}") as response:
            if response.status == 200:
                payload = await response.json()
                return {
                    "channel_id": channel_id,
                    "accessible": True,
                    "name": str(payload.get("name", "")),
                    "guild_id": str(payload.get("guild_id", "")),
                    "type": payload.get("type"),
                }
            if response.status == 403:
                return {"channel_id": channel_id, "accessible": False, "error": "forbidden"}
            if response.status == 404:
                return {"channel_id": channel_id, "accessible": False, "error": "not_found"}
            return {"channel_id": channel_id, "accessible": False, "error": f"http_{response.status}"}


def _safe_error(exc: Exception) -> str:
    text = str(exc)
    return text[:240] if text else exc.__class__.__name__
