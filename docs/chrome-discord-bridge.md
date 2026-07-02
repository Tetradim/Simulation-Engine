# Chrome Discord Bridge

Sentinel Archive accepts local Chrome bridge traffic at:

```text
POST /api/discord/chrome-bridge/message
POST /api/discord/chrome-bridge/heartbeat
GET  /api/discord/chrome-bridge/health
```

`message` payloads are recorded through the Discord recorder and published to the Cross Bot Event Bus as `signal.observed` with `contract_version: chrome.discord.message.v1`.

`heartbeat` payloads are published as `bridge.health`. The endpoints are local-only unless `CHROME_BRIDGE_ALLOW_REMOTE=1` is set.
