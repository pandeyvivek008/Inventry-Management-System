# Disha का Self-hosted AI

यह repo Disha के लिए वैकल्पिक local/self-hosted pipeline देता है। `AI_PROVIDER=local` होने पर चैट intent Ollama से, आवाज़ का transcript `LOCAL_STT_URL` से और बोला हुआ जवाब `LOCAL_TTS_URL` से आता है। Gemini/OpenAI keys की जरूरत नहीं पड़ती। मौजूदा सुरक्षित inventory tools ही database पढ़ते/बदलते हैं; model को SQL या database credentials नहीं दिए जाते। Product/stock/order बदलावों पर app की confirmation और validation जारी रहती है।

## क्या इस बदलाव में जुड़ा है

- **Text brain:** Ollama `/api/chat` के strict JSON schema के साथ। Default `qwen2.5:3b` है; दूसरे compatible Ollama model को `LOCAL_AI_MODEL` से चुनें। Ollama के native chat endpoint में schema आधारित output समर्थित है: [Ollama API](https://github.com/ollama/ollama/blob/main/docs/api.md?plain=1).
- **Hindi speech-to-text:** backend OpenAI-compatible `/v1/audio/transcriptions` endpoint पर Hindi (`language=hi`) भेजता है। उदाहरण implementation [faster-whisper-server](https://github.com/lightforgemedia/faster-whisper-server) है।
- **Speech output:** Piper HTTP `/synthesize` endpoint से WAV audio; server-side Hindi voice sample सुनकर उपयुक्त voice configure करें। [Piper HTTP API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_HTTP.md).
- **Gemini 503 recovery:** temporary 500/502/503/504 या network failure पर अधिकतम दो छोटे retries; फिर `LOCAL_STT_URL` configured हो तो local Whisper fallback। Retry भी fail हो तो Disha साफ़ error देती है और typed chat चालू रहता है।
- **Photo search:** upload/camera scan के बाद catalog की अधिकतम पाँच तस्वीरों से स्थानीय colour histogram + perceptual hash comparison। यह CLIP/vision LLM नहीं है, इसलिए result suggestion है; stock अपने-आप नहीं बदलता। PostgreSQL में pgvector index setup अलग one-time command है।

## Local laptop पर शुरू करें

1. Windows पर Ollama install करें और PowerShell में:

   ```powershell
   ollama pull qwen2.5:3b
   ollama serve
   ```

   Ollama default रूप से `http://127.0.0.1:11434` पर सुनता है। उसी laptop पर backend चल रहा हो तो `.env` में `LOCAL_AI_URL=http://127.0.0.1:11434` रखें।

2. Hindi transcription service को अलग process/container में चलाएँ। ऐसा server चुनें जो OpenAI-compatible `POST /v1/audio/transcriptions` दे। `.env` में पूरा endpoint रखें, जैसे `LOCAL_STT_URL=http://127.0.0.1:8000/v1/audio/transcriptions`; चुने server की model configuration उसी के docs के अनुसार करें।

3. Piper HTTP service चलाएँ और `.env` में `LOCAL_TTS_URL=http://127.0.0.1:5000/synthesize` रखें। `LOCAL_TTS_VOICE` तभी भरें जब उस Piper server पर voice override उपलब्ध हो। “Female/cute” आवाज़ model की voice data पर निर्भर है—इसे sample सुनकर चुनें; code voice को बदलकर female नहीं बना सकता।

4. `.env` में:

   ```dotenv
   AI_PROVIDER=local
   LOCAL_AI_URL=http://127.0.0.1:11434
   LOCAL_AI_MODEL=qwen2.5:3b
   LOCAL_STT_URL=http://127.0.0.1:8000/v1/audio/transcriptions
   LOCAL_TTS_URL=http://127.0.0.1:5000/synthesize
   ```

5. Backend restart करें। पहले typed chat में simple Hindi सवाल, फिर stock lookup, फिर confirmation वाला test करें। Local model का जवाब intent निकालता है; actual stock response हमेशा live database से बनता है।

## Railway पर अपने server के साथ

Railway का backend आपकी laptop वाली `127.0.0.1` सेवाओं तक नहीं पहुँच सकता। Ollama, Whisper और Piper को उस निजी मशीन/server पर चलाएँ जो स्थिर रूप से online हो, फिर backend से सुरक्षित HTTPS/private-network URL दें:

```dotenv
AI_PROVIDER=local
LOCAL_AI_URL=https://<private-ai-host>
LOCAL_STT_URL=https://<private-speech-host>/v1/audio/transcriptions
LOCAL_TTS_URL=https://<private-voice-host>/synthesize
```

Railway Variables में secrets रखें, `.env`/Git में नहीं। Public internet पर Ollama `11434`, Piper `5000` या Whisper port को बिना authentication expose न करें। Private networking/VPN या authenticated TLS reverse proxy लगाएँ; service keys को `LOCAL_AI_API_KEY`, `LOCAL_STT_API_KEY`, `LOCAL_TTS_API_KEY` में रखें। Railway service का outbound route इन hosts तक पहुँचना चाहिए।

## Photos: index और सीमा

Product photo upload database में photo path save करने के बाद visual index को best-effort update करता है; index बंद होने पर photo save सफल रहती है। PostgreSQL पर पहले `pgvector` support enable करके, backup/maintenance window में एक बार चलाएँ:

```powershell
Set-Location backend
python scripts/rebuild_visual_index.py
```

यह script production database में extension/table/index बनाती और saved images पढ़ती है। इसे web request/startup में नहीं चलाया जाता। SQLite local matcher 500 catalog images तक सीमित है (`VISUAL_SEARCH_DEV_MAX_CATALOG`); बड़ा production catalog PostgreSQL + pgvector माँगता है। वर्तमान `colour-shape-v1` basic visual signature है, fashion-trained model नहीं।

## खर्च, capacity और data safety

Local model की प्रति-request API fee नहीं होगी, लेकिन server की RAM/CPU/GPU, disk, bandwidth और uptime का खर्च रहेगा। 3B model laptop CPU पर चल सकता है मगर response speed hardware और concurrency पर निर्भर है। Whisper/Piper भी अतिरिक्त memory और compute लेते हैं। पहले low-traffic pilot में latency, RAM, Hindi intent accuracy और backup/restore जाँचें; केवल तभी traffic बढ़ाएँ।

`AI_PROVIDER=local` default नहीं किया गया है: जब तक आपके self-host services deploy और verify न हों, मौजूदा Gemini setup चलता रहेगा। Self-host server बंद होने पर local mode कोई चुपचाप cloud API call नहीं करता—typed/ manual fallback दिखाता है।
