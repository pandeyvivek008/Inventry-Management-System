const MAX_SELECTED_TEXT = 350;
const APP_TABS_URL = "https://inventry-management-system-production.up.railway.app/app/*";
const textarea = document.getElementById("selected-text");
const counter = document.getElementById("counter");
const statusLine = document.getElementById("status");
const readButton = document.getElementById("read-selection");
const shareButton = document.getElementById("share-with-disha");

function setStatus(message, kind = "info") {
  statusLine.textContent = message;
  statusLine.dataset.kind = kind;
}

function updateSelection(value) {
  textarea.value = String(value || "").slice(0, MAX_SELECTED_TEXT);
  counter.textContent = `${textarea.value.length}/${MAX_SELECTED_TEXT}`;
  shareButton.disabled = !textarea.value.trim();
}

textarea.addEventListener("input", () => updateSelection(textarea.value));

readButton.addEventListener("click", async () => {
  readButton.disabled = true;
  setStatus("Checking the active tab for a selected WhatsApp message…");
  try {
    const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const activeUrl = new URL(activeTab?.url || "");
    if (activeUrl.protocol !== "https:" || activeUrl.hostname !== "web.whatsapp.com") {
      updateSelection("");
      setStatus("Open WhatsApp Web, select the message text, then try again.", "error");
      return;
    }

    const [result] = await chrome.scripting.executeScript({
      target: { tabId: activeTab.id },
      func: () => window.getSelection()?.toString() || ""
    });
    const selected = String(result?.result || "").trim();
    if (!selected) {
      updateSelection("");
      setStatus("No text is selected. Select only the message you want to share.", "error");
      return;
    }
    updateSelection(selected);
    const clipped = selected.length > MAX_SELECTED_TEXT;
    setStatus(clipped
      ? `Selection loaded; showing the first ${MAX_SELECTED_TEXT} characters. Review before sharing.`
      : "Selection loaded. Review or edit it before sharing.");
  } catch (_error) {
    updateSelection("");
    setStatus("Could not read the selection. Reload WhatsApp Web and try again.", "error");
  } finally {
    readButton.disabled = false;
  }
});

shareButton.addEventListener("click", async () => {
  const text = textarea.value.trim();
  if (!text) return;
  shareButton.disabled = true;
  setStatus("Sending the reviewed selection to the Disha input box…");
  try {
    const appTabs = await chrome.tabs.query({ url: APP_TABS_URL });
    const appTab = appTabs.find(tab => tab.active) || appTabs[0];
    if (!appTab?.id) {
      setStatus("Open the signed-in Inventory app in a tab, then share again.", "error");
      shareButton.disabled = false;
      return;
    }

    const response = await chrome.tabs.sendMessage(appTab.id, {
      type: "DISHA_SHARE_SELECTED_WHATSAPP_TEXT",
      text
    });
    if (!response?.ok) {
      setStatus(response?.reason || "Disha could not receive this selection.", "error");
      shareButton.disabled = false;
      return;
    }
    await chrome.tabs.update(appTab.id, { active: true });
    setStatus("Shared to Disha. Review the filled input and press Send there when ready.");
  } catch (_error) {
    setStatus("Keep the Inventory app open and reload it, then try again.", "error");
    shareButton.disabled = false;
  }
});
