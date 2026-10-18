# Disha + Dograh setup

Disha remains the assistant's name. Dograh supplies the optional chat agent; the app keeps its existing Disha screen and voice controls. If the Dograh widget is not configured, Disha continues to use the configured app AI provider.

## 1. Create the Dograh agent and chat widget

1. Install or open Dograh using its [Getting started](https://docs.dograh.com/getting-started) instructions. Dograh is a separate service; this inventory app does not run Dograh's Docker stack inside the Railway web process.
2. Create a workflow and name the agent **Disha**. Configure its Hindi persona and choose your Hindi voice/LLM providers in Dograh.
3. In the agent settings, open **Add to Website**. Enable embedding, allow your inventory app's domain, select **Chat** and **Headless** mode, then save.
4. Copy the generated embed script URL (the value assigned to `js.src`). Do not copy the Dograh workspace API key here.

## 2. Set Railway variables

Create a random secret locally, for example:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

In Railway → inventory app service → Variables, set:

- `DISHA_MCP_API_KEY`: the generated secret (at least 32 characters)
- `DOGRAH_WIDGET_MODE`: `headless-chat`
- `DOGRAH_WIDGET_SCRIPT_URL`: the generated Dograh widget script URL
- `DISHA_MCP_ALLOWED_HOSTS`: only if the request host is not Railway's `RAILWAY_PUBLIC_DOMAIN`; comma-separated hostnames
- `DISHA_MCP_ALLOWED_ORIGINS`: only if an MCP browser client needs an Origin header; comma-separated exact origins

Keep the secret out of source control, the browser, and chat messages. The app's runtime config exposes only the public widget URL. The MCP endpoint stays unavailable until the secret is set.

## 3. Connect the inventory tools to Dograh

Use Dograh v1.31.0 or newer, which includes generic MCP tool sources and per-node tool filtering ([changelog](https://github.com/dograh-hq/dograh/blob/main/CHANGELOG.md)). Add an MCP tool source to the workflow's main agent node:

- Transport: Streamable HTTP
- Server URL: `https://<your-inventory-domain>/api/mcp/`
- Header: `X-API-Key: <the-same-DISHA_MCP_API_KEY>`

Attach the tools you want to that agent node. If a custom domain is used, add its hostname to `DISHA_MCP_ALLOWED_HOSTS`. Railway's public hostname is included automatically when `RAILWAY_PUBLIC_DOMAIN` is set.

The Dograh workspace MCP URL documented by Dograh (typically `/api/v1/mcp/`) controls Dograh agents from coding assistants. Disha's inventory MCP URL above exposes this app's inventory to the Dograh workflow; they serve different purposes.

## 4. Put these rules in Disha's Dograh instructions

```text
You are Disha, a friendly Hindi-first assistant. Understand Hindi, Devanagari,
Romanized Hindi, Hinglish, and English. Reply in the user's language. Use live
inventory MCP tools whenever a question depends on app data. Preserve the exact
product SKU and size when selecting a stock variant; never infer size or stock.

For every stock change, product create/edit/archive/restore, size change, order
import, or order dispatch: first call prepare_inventory_action. Read the exact
product, size, quantity, and affected order lines to the user. Wait for an
explicit yes from the user. Only then call confirm_inventory_action with the
returned token.
If inventory changed, the token expired, or the tool reports an error, explain
that the action did not happen and prepare again. Never say an action succeeded
before the confirmation tool reports completed.

Order dispatch only ships currently counted and available stock; the rest
remains pending. WhatsApp is a draft only: show the number and text, open the
returned URL, and let the user review and press Send themselves. Never claim
you can read contacts, messages, or notifications.

You can also answer ordinary daily-life questions conversationally. Do not
invent live stock, order, alert, or sales facts.
```

## MCP tools

Read tools: `search_inventory`, `get_product`, `get_inventory_summary`, `get_inventory_alerts`, `get_reorder_shortages`, `get_stock_history`, `get_daily_dispatch_summary`, `get_dispatch_analytics`, `list_pending_order_batches`, and `get_order_batch`.

Write flow: `prepare_inventory_action` and `confirm_inventory_action`. Supported actions are stock add/set/remove, product create/update/archive/restore, size add/archive/restore, structured order import (up to 500 SKU/size/quantity rows), and partial or full order dispatch. Imported order lists use the app's SKU/size and cross-brand matcher, block duplicate content, and create pending demand without deducting physical stock. Raw Excel uploads and product photos remain in the inventory UI. Writes use the same database ledger, alert updates, and pending-order rules as the app. Tokens expire after 10 minutes; changed stock or batch lines are rejected so the agent has to refresh and ask again.

`whatsapp_draft` returns a `wa.me` URL only. It never sends a message.

## Limitations

Dograh's configured chat widget is enabled through the public embed URL. Dograh's LLM, TTS/STT, and call usage are configured and billed by the Dograh deployment/provider you choose. The app's existing browser microphone, transcription, and speech output remain available. Spreadsheet imports, product photos, and permanent trash deletion stay in the inventory UI; destructive permanent deletion is intentionally not exposed as an agent tool.
