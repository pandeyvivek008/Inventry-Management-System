# फोटो से प्रोडक्ट खोजना

इस सुविधा में स्कैन से stock नहीं बदलता। यह catalog की सुरक्षित तस्वीरों से अधिकतम पाँच मिलते-जुलते designs दिखाती है; सही design चुनने के बाद ही मौजूदा size-wise stock form में बदलाव करें।

## इस्तेमाल करें

1. **Scan & Recount** खोलें।
2. **फोटो से प्रोडक्ट खोजें** में कैमरा खोलें या JPG/PNG/WEBP फोटो अपलोड करें। फ़ाइल अधिकतम 10 MB होनी चाहिए।
3. Top matches में सही design चुनें। App उसी product record से live sizes/stock दिखाती है।

Catalog में फोटो नहीं है तो पहले Products → Edit से साफ़ front image जोड़ें। Photo save होना visual index पर निर्भर नहीं: index अनुपलब्ध हो तो image upload फिर भी सफल रहता है।

## पहचान की सीमा

वर्तमान `colour-shape-v1` विधि local colour histogram और perceptual hash तुलना करती है। यह CLIP/fashion-trained AI नहीं है और समान रंग, embroidery, crop, background या lighting में गलत ranking हो सकती है। Match percentage को confidence guarantee न मानें। हमेशा सही design स्वयं confirm करें।

SQLite में local fallback catalog 500 images तक सीमित है। PostgreSQL में visual search के लिए pgvector extension और एक बार index setup चाहिए:

```powershell
Set-Location backend
python scripts/rebuild_visual_index.py
```

पहले production backup लें और database permissions verify करें। Script live database की saved images पढ़ती है और vector index बनाती है; इसे app startup या customer request में न चलाएँ। Product photo update के बाद नई image vector best-effort में upsert होती है।

Redis optional है: `REDIS_URL` repeated scan cache और instances के बीच scan rate limit के लिए है। Image files अभी app-local path में रखी जाती हैं; multiple isolated Railway instances चलाने से पहले shared durable image storage जोड़ना जरूरी है।
