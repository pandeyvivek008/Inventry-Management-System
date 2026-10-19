# Dograh ko bolkar istemal karna

1. Dograh panel kholen. **Start voice call** dabakar browser microphone permission Allow karein. Panel kholne se mic apne-aap start nahi hota.
2. Hindi, Roman Hindi/Hinglish ya English mein baat karein. Dograh har turn ki language auto-detect karke usi language/script mein jawab dene ke liye configured hona chahiye. Beech conversation mein English se Hindi ya Hindi se English switch karke check karein.
3. Product/stock poochhein—jaise “नीले कुर्ते के M साइज़ में कितने पीस हैं?”—ya rozmarra ka sawaal poochhein.
4. Stock/order/dispatch badlav par Dograh pehle exact item aur quantity review ke liye degi. Sirf sahi details par **haan/yes** bolen; galat ho to **nahi/cancel** bolen.
5. Call khatam hone par **End voice call** dabayen. Panel band karne, browser tab background mein jaane, ya pause karne par app Dograh call end karti hai.

## Sahi Hindi/English voice ke liye Dograh setup

- Workflow mein multilingual, auto-detect transcriber select karein.
- Aisi TTS voice chunein jo Hindi aur English dono ke liye documented support rakhti ho. Agar per-language voice setting hai to Hindi/English ke liye sahi voice map karein.
- Hindi reply Devanagari aur natural, dheemi pace mein dene ko prompt karein; English input ko Hindi accent se zabardasti na padhne ko kahen.
- Voice preview mein Hindi, Roman Hindi/Hinglish, English aur language-switch test karein. Sirf tabhi kisi language ko working batayen jab recognition aur spoken reply dono sahi hon.
- Website embedding mein **Voice → Headless** se Voice script URL lein. Chat/Headless URL alag hota hai. Voice script ko Railway `DOGRAH_VOICE_WIDGET_SCRIPT_URL` mein rakhna zaroori hai.

Official setup: [Dograh website voice widget](https://docs.dograh.com/voice-agent/add-to-website), [transcribers](https://docs.dograh.com/configurations/transcriber), [voice providers](https://docs.dograh.com/configurations/voice). Microphone recognition aur spoken reply Dograh workflow ke configured provider se chalte hain.

## Troubleshooting

- **Voice button nahi dikhta:** `/api/dograh/runtime-config` par `dograh_voice_enabled` dekhein; false/null ka matlab Voice script URL Railway par set nahi hai.
- **Mic connect nahi hota:** HTTPS, browser microphone permission, system ka active mic, Dograh Voice widget allow-domain aur Dograh call provider check karein.
- **Hindi ko galat bolta/samajhta hai:** Dograh Voice workflow ke transcriber ka auto-detect, selected TTS voice ki Hindi support, aur reply-language prompt ko check karein. App mein text chat `Dograh` workflow aur voice `Dograh Voice` workflow use karte hain; dono mein same MCP tools/prompts rakhein.
- **Inventory action:** confirmation se pehle SKU, size aur quantity verify karein. Dograh ko `prepare_inventory_action` ke baad explicit confirmation ke bina `confirm_inventory_action` nahi karna chahiye.

Secrets ko frontend ya chat mein na daalein. Inventory MCP `X-API-Key` ka secret Railway aur Dograh credential vault mein hi rakhein. Full setup: [DOGRAH_INTEGRATION_HI.md](DOGRAH_INTEGRATION_HI.md).
