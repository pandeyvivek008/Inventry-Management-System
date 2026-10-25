# MYRAA ko inventory app se connect karein

MYRAA do jagah kaam karta hai: apni Windows desktop app mein, aur inventory website ko usi PC ke browser mein kholne par uske assistant panel ke andar. Browser panel local MYRAA companion se connect hota hai; isliye companion us PC par chalna chahiye. Website server ko aapke PC ka microphone ya desktop access nahi milta.

## 1. Local setup

Requirements: Node.js 20+ aur Windows par Python 3.11+.

PowerShell mein repository ke MYRAA folder se:

```powershell
Set-Location E:\Inventry-Management-System\companion\myraa
npm ci
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r desktop_agent\requirements.txt
npm run app
```

`npm run app` MYRAA desktop window kholta hai, local voice/chat service start karta hai, aur PC-control agent ko `127.0.0.1` par chalata hai. Desktop agent ko alag network par expose na karein. Browser panel ke liye isi PC ke Chrome/Edge mein inventory website kholen, MYRAA desktop app chalu rakhein, aur website par MYRAA icon dabayen. Chrome/Edge poochhe ki inventory site aapke local network ke devices se connect kar sakti hai, to **Allow** chunein. Yeh browser permission local desktop companion ke liye zaroori hai. Agar app ya permission pehle block hui thi, site settings mein **Local network access** allow karke **Retry connection** dabayen.

Build files banane ke liye:

```powershell
npm run build
```

## 2. API keys

MYRAA settings mein:

1. Gemini API key paste karke save karein. Yeh MYRAA voice/chat model ko connect karti hai.
2. Inventory MCP endpoint ko default `https://inventry-management-system-production.up.railway.app/api/mcp/` par rehne dein, jab tak aapka server URL alag na ho.
3. Railway ke inventory service mein `INVENTORY_MCP_API_KEY` set karein. Wahi key MYRAA settings ke **Inventory MCP API key** mein paste karke **Save & test** dabayen.
4. Connection test tool count dikhata hai. Voice dobara connect karne par session inventory tools load karta hai.

Keys MYRAA ke per-user data folder mein rakhi jati hain (Windows par Electron ka user-data folder, aam taur par `%APPDATA%\myraa\`; direct development mode mein `%APPDATA%\MYRAA\`). Key browser history, frontend source, URL, logs, ya Git mein nahi rakhi jati. Root `.env.example` sirf server-side MCP key ke variable ka naam dikhata hai—usme asli key kabhi na daalein.

## 3. Kya kar sakta hai

- MYRAA Gemini Live ke through voice aur typed chat deta hai; typed chat microphone ke bina start ho sakti hai. Hindi, English, Hinglish ya doosri samarthit bhasha mein baat karein. Reply ko user ki latest language/script mein dene ke liye prompt kiya gaya hai. Voice model ki actual bhasha/pronunciation provider par nirbhar hai.
- Inventory MCP ke read tools se products, size-wise stock, orders, dispatch, reorder aur analytics dekhta hai.
- Inventory write ya dispatch ko pehle prepare karta hai. Exact product/SKU, size, quantity aur asar confirmation card mein dikhane ke baad hi user **Confirm change** dabaye to write execute hota hai.
- Local desktop agent alag se approved PC tasks ke liye hai. `/execute` ke liye per-install random bearer token chahiye; agent sirf loopback par bind hota hai.
- WhatsApp personal notifications/contacts scan karna aur background auto-send is setup mein nahi hai. Message/draft ko WhatsApp mein review karke user khud send kare.

## 4. Troubleshooting

- **Website mein MYRAA offline:** MYRAA desktop app chalu karein, Chrome/Edge prompt mein local network access allow karein, phir browser panel ka **Retry connection** dabayen. Browser aur desktop app same PC par hone chahiye. Chrome 142 se secure website ko local companion tak pahunchne ke liye alag permission prompt dikh sakta hai.
- **Microphone nahi chalta:** browser mein microphone permission allow karein; MYRAA mein **Start voice** khud dabayen.
- **Gemini connect nahi hota:** MYRAA settings mein API key status dekhein aur internet/API quota check karein.
- **Inventory tools nahi dikhte:** MCP key, Railway deployment par `INVENTORY_MCP_API_KEY`, endpoint URL, aur **Save & test** result check karein. Key change ke baad voice reconnect karein.
- **PC control offline:** Python 3.11+ dependencies install karein, MYRAA restart karein, phir settings mein agent status check karein. Playwright browser automation ke liye `py -3.11 -m playwright install chromium` bhi chalayein.

## Inventory MCP security

`/api/mcp/` is protected by `INVENTORY_MCP_API_KEY`. Requests use the `X-API-Key` header over HTTPS. The MCP endpoint is not a public, anonymous API. Write tools use short-lived prepared actions and server-side confirmation tokens; a successful preview alone never changes inventory.
