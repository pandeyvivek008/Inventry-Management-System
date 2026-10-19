chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "DISHA_SHARE_SELECTED_WHATSAPP_TEXT") return false;

  const text = String(message.text || "").trim().slice(0, 350);
  if (!text) {
    sendResponse({ ok: false, reason: "Select a WhatsApp message first." });
    return false;
  }

  const input = document.getElementById("assistant-chat-input");
  if (!input) {
    sendResponse({ ok: false, reason: "Open the Inventory app and Dograh assistant first." });
    return false;
  }
  if (input.value.trim()) {
    sendResponse({ ok: false, reason: "Send or clear the current Disha draft first." });
    return false;
  }

  window.postMessage({
    source: "disha-whatsapp-companion",
    type: "selected-text",
    text
  }, window.location.origin);
  sendResponse({ ok: true });
  return false;
});
