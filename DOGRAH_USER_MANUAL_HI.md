# Dograh ka user manual

Dograh app ka conversational agent hai. Kaunsi languages samajh aur bol sakta hai, yeh Dograh workflow ke configured transcriber aur TTS voice par depend karta hai. Hindi, Hinglish/Roman Hindi aur English ko alag-alag bolkar test karein.

## Chat aur voice shuru karein

1. App ke HTTPS page par robot icon dabakar Dograh panel kholen.
2. Text chat mein Hindi, Hinglish, English, ya apna sawaal type karke send karein. Aam rozmarra ki baat bhi pooch sakte hain.
3. Voice ke liye panel ke andar **Start voice call** dabayen aur browser microphone prompt aaye to Allow karein. Baat khatam hone par **End voice call** dabayen.
4. Earbud mic use karna ho to Windows/phone ki sound input settings mein wahi device select karein.
5. Dograh se app data ya action ke baare mein poochte samay product, colour, size, SKU aur quantity saaf batayen. Dograh ko ambiguous match par clarification leni chahiye.

Chat aur voice alag Dograh workflows se judte hain (`Dograh` Chat aur `Dograh Voice` Voice), lekin dono mein same MCP tools aur assistant rules configure hone chahiye. Panel kholna apne aap microphone chalu nahi karta. Hindi ke liye Dograh se Devanagari mein dheere, saaf jawab maangein. Aap language badlein to agle turn mein usi language mein jawab aana chahiye, basharte Dograh transcriber/TTS provider us language ko support karta ho.

## Roz ke kaam ke examples

| Kaam | Aise poochhein |
| --- | --- |
| Stock dekhna | “Blue kurta size M mein kitna stock hai?” |
| Design aur sizes | “Kids NK 101 ke sabhi sizes ka stock batao” |
| Stock badhana | “Mustard design size M mein 5 pieces add karo” |
| Total stock set karna | “Blue kurta size S ka total stock 12 set karo” |
| Alerts | “Kaunse designs low stock hain?” |
| Reorder | “Pending orders ke liye kya restock karna hai?” |
| Orders | “Pending orders ka status batao” |
| Aaj ka dispatch | “Aaj kitne pieces dispatch hue?” |
| WhatsApp draft | “+91 98765 43210 ko message ka draft banao: aaj parcel dispatch ho gaya” |
| Normal conversation | Seedha apna aam sawaal poochhein. |

## App mein badlav aur dispatch

Product, stock, size, order aur dispatch ke live facts ke liye Dograh inventory MCP tools use karti hai. Har write/dispatch pehle prepare hota hai: product/SKU, exact size, quantity aur effect check karein. Sirf aapke explicit **haan/yes** ke baad confirmation ho sakta hai. Tool completed bataye bina Dograh ko action complete nahi kehna chahiye. Partial dispatch mein jitna counted stock available hai utna hi dispatch hota hai; baaki pending rehna chahiye.

WhatsApp feature sirf draft/click-to-chat link deta hai. Number aur exact text check karke WhatsApp mein Send aap khud dabayen. App personal WhatsApp notifications ya contacts nahi padh sakti.

## Agar awaaz ya bhasha galat ho

- Browser microphone permission, HTTPS, internet aur sahi active input device check karein.
- Hindi, Roman Hindi/Hinglish aur English ko alag-alag aur phir beech conversation mein switch karke test karein.
- Agar text chat chale par voice button na ho, Railway `/api/dograh/runtime-config` mein `dograh_voice_enabled` dekhein. Voice enable karne ke liye Dograh ka alag Voice/Headless script URL `DOGRAH_VOICE_WIDGET_SCRIPT_URL` mein chahiye; Chat widget URL live microphone call nahi hai.
- Galat accent/language par Dograh workflow mein multilingual auto-detect transcriber aur Hindi/English dono support karne wali TTS voice chunein. Voice provider ki language list ke bahar app code se support add nahi hota.
- Connection error par page reload karein. MCP/app writes ke liye live endpoint key-protected hai; API key chat ya screenshot mein share na karein.

Dograh, widget URLs, inventory MCP source, aur 12 available MCP tools ka setup [DOGRAH_INTEGRATION_HI.md](DOGRAH_INTEGRATION_HI.md) mein hai.
