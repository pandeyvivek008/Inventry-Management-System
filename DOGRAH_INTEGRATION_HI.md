# Dograh ko inventory app se connect karein

Dograh apni alag service hai (Dograh Cloud ya self-hosted). Yeh app Dograh ke website widgets embed karti hai aur live inventory data protected MCP tools se deti hai. Dograh ke official [getting started](https://docs.dograh.com/getting-started), [website widget](https://docs.dograh.com/voice-agent/add-to-website), [transcriber](https://docs.dograh.com/configurations/transcriber), aur [voice configuration](https://docs.dograh.com/configurations/voice) guides follow karein.

## 1. Dograh workflow aur widgets

1. Inventory MCP tools aur prompts ke saath ek `Dograh` workflow banayein aur us par **Chat → Headless** widget configure karein.
2. Chat widget mein sirf production hostname `inventry-management-system-production.up.railway.app` allow karein. Generated script URL ko Railway variable `DOGRAH_WIDGET_SCRIPT_URL` mein rakhein.
3. Isi workflow ko duplicate karke `Dograh Voice` banayein. Dograh ek workflow par ek widget type configure karta hai; voice workflow par alag se **Voice → Headless** set karein. Wahi hostname allow karke generated URL `DOGRAH_VOICE_WIDGET_SCRIPT_URL` mein rakhein.
4. Dono workflows mein same inventory MCP server, tools, safety instructions aur language prompts rakhein. Voice workflow mein multilingual auto-detect transcriber aur Hindi/English supported TTS voice select karein. Preview mein Devanagari Hindi, Roman Hindi/Hinglish, English aur language switching test karein.
5. Dono workflows save karein. Hindi reply ke liye Devanagari, natural Hindi aur clear unhurried pace ki instruction dein; har naye turn par user ki language match ho.
6. Prompt mein `{{initial_context.speech_guidance}}` ko explicitly use karein. Widget isi context ko dono chat aur voice workflows ko deta hai; context tabhi model prompt ka hissa banta hai jab workflow us variable ko reference karta hai.

## 2. Railway variables

App service ke Railway Variables mein:

```text
DOGRAH_WIDGET_MODE=headless-chat
DOGRAH_WIDGET_SCRIPT_URL=<Dograh Chat Headless URL>
DOGRAH_VOICE_WIDGET_SCRIPT_URL=<Dograh Voice Headless URL>
DOGRAH_MCP_API_KEY=<same random secret stored in Dograh credential vault>
```

MCP key kam-se-kam 32 characters ka secret hona chahiye. Dograh MCP credential mein ise `X-API-Key` header ke roop mein bhejein. Key ko widget URL, frontend JavaScript, Git, screenshot, ya chat mein na rakhein. `DOGRAH_MCP_ALLOWED_HOSTS` aur `DOGRAH_MCP_ALLOWED_ORIGINS` optional allow-lists hain.

Deploy ke baad `GET /api/dograh/runtime-config` se check karein ki `dograh_chat_enabled` aur `dograh_voice_enabled` true hain. Yeh endpoint sirf public widget configuration deta hai—secret nahi.

## 3. Inventory MCP source

Dograh workflow ke agent node par **MCP → Streamable HTTP** server add karein:

- URL: `https://inventry-management-system-production.up.railway.app/api/mcp/`
- Header: `X-API-Key`
- Credential: Dograh credential vault mein rakhi inventory MCP key

Tools source dono workflows ke Main Agenda agent node se attach karein. Dograh ke control-plane MCP aur is app ke inventory MCP alag cheezein hain; inventory ke liye upar diya `/api/mcp/` URL use karein. Publish se pehle read-only inventory query aur Hindi/English chat test karein.

### Available 12 tools

Read tools: `search_inventory`, `get_product`, `get_inventory_summary`, `get_inventory_alerts`, `get_reorder_shortages`, `get_stock_history`, `get_daily_dispatch_summary`, `get_dispatch_analytics`, `list_pending_order_batches`, `get_order_batch`.

Changes ke liye `prepare_inventory_action` aur `confirm_inventory_action` hain. Inse product/size/stock change, order import aur partial/full dispatch prepare karke exact effect review hota hai; alag explicit user approval ke baad hi action confirm karein. Confirmation token 10 minute baad expire hota hai. Badle hue stock/order state par stale action reject hota hai. Current WhatsApp feature sirf message draft/link deta hai; woh message send nahi karta.

## 4. Agent prompt

```text
You are Dograh, the friendly multilingual assistant for this inventory app.
Detect the language of every new user turn independently. Reply in the user's
language and script. Use Hindi in natural Devanagari, Roman Hindi/Hinglish as
the user wrote it, and English when the user speaks English. Support other
languages only when this workflow's configured transcriber, model and voice
support them. Use the configured Dograh voice; do not switch to browser speech.
Keep spoken replies clear, conversational and unhurried. Follow
{{initial_context.speech_guidance}}: read complete words and full product/design
names naturally, never spell ordinary words letter by letter. Read quantities
as whole quantities. Say SKU/code characters only when the user asks.

Use the live inventory MCP tools for every stock, product, order, dispatch,
alert, reorder, and analytics fact. Preserve exact product, SKU, colour and size;
do not guess. Before every write or dispatch, call prepare_inventory_action and
show the exact item, size, quantity, and effect. Wait for explicit user approval
before confirm_inventory_action. Never say a change succeeded until the tool
confirms it. Re-prepare if its token expires or data changes.

Also converse naturally about everyday topics. WhatsApp is draft-only: show the
recipient and message for review. Do not claim to read personal notifications,
contacts, or send a WhatsApp message. Do not claim to control the user's PC or
other desktop apps; use only the inventory tools connected to this workflow.
```

## 5. Capability boundary

Dograh can control the inventory features and data exposed by the 12 MCP tools. It does not get unrestricted computer/OS control, desktop-app access, personal WhatsApp notifications, or message-sending rights through this widget. The app's ordinary REST endpoints are not automatically granted to Dograh; new capabilities must be implemented as validated MCP tools first.

For language setup, microphone troubleshooting and user steps, see [Dograh user manual](DOGRAH_USER_MANUAL_HI.md) and [voice guide](VOICE_AUTOMATION_HI.md).
