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

## Strata या free OpenAI-compatible API

Disha अब OpenAI-compatible `POST /v1/chat/completions` सेवा को text LLM के रूप में इस्तेमाल कर सकती है। इसमें API key code/Git में नहीं जाती; key Railway Variables या ignored local `.env` में रखें। पुराने `OPENAI_COMPATIBLE_*` variables काम करते रहेंगे; नए deploy में `DISHA_AI_*` variables इस्तेमाल करें। यह connector [Strata](https://github.com/Niko1221/Strata) और [free LLM API providers की सूची](https://github.com/mnfst/awesome-free-llm-apis) से लिए गए common API format पर काम करता है।

### Disha provider variables

Railway के inventory app service में Variables खोलकर यह सुरक्षित baseline भरें:

```dotenv
DISHA_AI_ENABLED=true
DISHA_AI_PROVIDER=strata
DISHA_AI_BASE_URL=https://<private-gpu-host>/v1
DISHA_AI_API_KEY=<provider-issued-secret>
DISHA_AI_MODEL=<exact-model-id-served-by-strata>
DISHA_AI_TIMEOUT_SECONDS=60
DISHA_AI_MAX_OUTPUT_TOKENS=1200
DISHA_AI_TEMPERATURE=0.2
DISHA_AI_ALLOW_EXTERNAL_INFERENCE=true
DISHA_AI_DIAGNOSTICS_TOKEN=<long-random-admin-only-token>
```

`DISHA_AI_ALLOW_EXTERNAL_INFERENCE` का default `false` है। उसी Railway private network में `http://strata:8080/v1` जैसे private host का उपयोग करें तो external access खोलने की जरूरत नहीं। किसी अलग GPU host या hosted free API के लिए secure HTTPS और authentication लगाएँ, फिर ही allow flag `true` करें। Free API को चुनने के लिए `DISHA_AI_PROVIDER=compatible`, base URL, provider का exact model ID और API key दें; free-tier availability/limits provider बदल सकता है। जब कोई `DISHA_AI_*` value blank हो, backend पुराने `AI_PROVIDER`/`OPENAI_COMPATIBLE_*` values पर वापस जाता है। `DISHA_AI_ENABLED=false` chat को बंद करता है।

Diagnostics endpoint `POST /api/assistant/diagnostics` है। इसे सिर्फ admin tooling से इस header के साथ call करें: `Authorization: Bearer <DISHA_AI_DIAGNOSTICS_TOKEN>`. यह inventory history या chat conversation नहीं भेजता; सिर्फ एक न्यूनतम JSON response check करता है। सही response में app, endpoint, model, inventory-tool और visual-search readiness अलग दिखाई जाती है। Token configure न होने पर route छुपा रहता है। इस app में अभी login/role system नहीं है, इसलिए token को कभी frontend में न रखें।

### Hosted free API, उदाहरण Groq

`awesome-free-llm-apis` सूची Groq और OpenRouter के OpenAI-compatible endpoints बताती है। Groq का free tier/key और model availability बदल सकती है; अपने provider dashboard से आज का exact model ID लें। उदाहरण के लिए:

```dotenv
DISHA_AI_PROVIDER=compatible
DISHA_AI_BASE_URL=https://api.groq.com/openai/v1
DISHA_AI_MODEL=<Groq dashboard में उपलब्ध exact model ID>
DISHA_AI_API_KEY=<Groq dashboard की key>
DISHA_AI_ALLOW_EXTERNAL_INFERENCE=true
```

OpenRouter के लिए base URL `https://openrouter.ai/api/v1` रखें और उसके dashboard में दिखा exact free model ID `OPENAI_COMPATIBLE_MODEL` में डालें। Free model catalog और quotas बदलते रहते हैं; इनमें uptime की गारंटी नहीं है।

### अपने कंप्यूटर पर Strata

Strata app को OpenAI-compatible chat endpoint देता है। इसका documented model Qwen3.8-Flash-Next है; Strata README के अनुसार supported NVIDIA/AMD graphics card पर कम-से-कम 12 GB VRAM, 32 GB RAM और करीब 80 GB खाली SSD चाहिए। Model size/quantization से actual RAM और disk जरूरत बदल सकती है। Disha local computer पर चल रही हो तो:

```dotenv
DISHA_AI_PROVIDER=strata
DISHA_AI_BASE_URL=http://127.0.0.1:8080/v1
DISHA_AI_MODEL=<exact model ID served by Strata>
DISHA_AI_ALLOW_EXTERNAL_INFERENCE=false
```

Railway पर चल रही Disha के लिए `127.0.0.1` Railway container होता है, आपका computer नहीं। Railway को Strata तक पहुँचाने के लिए हमेशा चालू host और authenticated private network/VPN या HTTPS gateway चाहिए। Strata में API key चालू करें; बिना authentication model server को public internet पर expose न करें। Railway Variables में private URL और key रखें। Remote public host होने पर `DISHA_AI_ALLOW_EXTERNAL_INFERENCE=true` भी लगाएँ।

यह integration अभी text chat/intents के लिए है। Disha इस connector से photos Strata को नहीं भेजती; visual product search अपना SigLIP/catalog-index path इस्तेमाल करता है। Strata में optional image input मौजूद है, पर इस app connector में multimodal request/evaluation लागू नहीं है। Voice के लिए `LOCAL_STT_URL` और `LOCAL_TTS_URL` रखें; Gemini transcription/speech तभी इस्तेमाल होगा जब उसका key मौजूद हो और external inference explicitly allowed हो।

### Privacy और free-tier सीमा

Hosted API को Disha का user message और सीमित chat history भेजी जाती है। Provider की data retention/training policy जाँचें और customer personal data या passwords chat में न भेजें। Free tiers की rate limits, model names और शर्तें बदल सकती हैं; इसका status provider dashboard में देखें।

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
