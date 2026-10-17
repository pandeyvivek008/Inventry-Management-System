# दिशा को बोलकर इस्तेमाल करना

1. दिशा खोलें और browser prompt आने पर Microphone permission Allow करें। Earbud microphone चलाने के लिए Windows/phone की sound settings में सही input device चुनें। Browser microphone को website अपने-आप चालू नहीं कर सकती।
2. Mic/listening चालू होने पर हिंदी में बोलें। App configured speech-to-text endpoint से transcription लेती है। Disha के Hindi-only जवाब Gemini Hindi TTS से आते हैं, या `AI_PROVIDER=local` पर `LOCAL_TTS_URL` से।
3. Product/stock पूछें—जैसे “नीली ड्रेस के एम साइज़ का स्टॉक बताओ” या “आज कितने पीस dispatch हुए?” Disha actual catalog/database से उत्तर बनाती है।
4. बदलाव के लिए साफ़ command दें—जैसे “नीली ड्रेस के एम साइज़ में पाँच जोड़ो।” दिशा current stock और proposed stock बताकर confirmation माँगेगी। पुष्टि के बाद ही app update करेगी। अस्पष्ट product/size पर पहले clarification माँगें।
5. Product बनाते समय SKU, नाम और सभी sizes साफ़ बोलें। दिशा review card दिखाएगी; confirm करने से पहले details जाँचें।
6. रोज़मर्रा के सवाल भी पूछ सकती हैं। WhatsApp draft के लिए recipient का international phone number (+91...) और exact message दें; app WhatsApp chat खोलती है, जहाँ recipient/text जाँचकर Send आपको खुद दबाना है। Personal WhatsApp notifications या contacts app नहीं पढ़ सकती।

गलत जवाब दिखे तो उसे stock update न दें। Product search में सही colour/design चुनें और database में photo/catalog details ठीक करें। Disha model को arbitrary SQL, direct database credentials या automatic product deletion access नहीं दिया जाता; actions predefined app endpoints और confirmation flow से चलते हैं।

## सेवा सेटअप

Self-hosted Ollama, Hindi Whisper transcription, Piper TTS और Gemini 503 fallback की setup details [SELF_HOSTED_AI_HI.md](SELF_HOSTED_AI_HI.md) में हैं। `AI_PROVIDER=local` तभी चुनें जब तीनों services reachable और tested हों।
