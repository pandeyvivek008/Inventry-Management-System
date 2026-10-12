# Disha ka user manual

Disha Hindi, Hinglish aur English mein inventory ke sawaal samajhne aur app ke kaam guide karne ke liye hai. **Robot button** dabane par chat aur voice session khulta hai; mic page load par apne aap start nahi hota.

## Chat se shuru karein

1. App ko HTTPS link par kholen aur robot icon dabayen. Isi click par mic session shuru hota hai.
2. Hindi, Hinglish ya English mein seedha bolna shuru karein. Har sentence ke baad Disha transcript bhejti hai; “Hi Disha” bolne ya mic ko baar-baar dabane ki zaroorat nahi.
3. Type karna ho to message box mein aam bol-chal ki bhasha likhein; `Enter` dabayen ya send button chunein.
4. Disha jawab degi. Product ka naam, colour, size ya SKU yaad ho to batayein. SKU bolna zaroori nahi.
5. Agar ek jaise kai designs milen, sahi design/color chunein. Disha bina aapse poochhe andaza laga kar stock nahi badalti.

## Roz ke kaam ke examples

| Kaam | Aise poochhein |
| --- | --- |
| Stock dekhna | “Blue kurta size M mein kitna stock hai?” |
| Design aur sab sizes dekhna | “Kids NK 101 ki photo dikhao” / “Kids NK 101 ke sabhi sizes batao” |
| Stock badhana | “Mustard design size M mein 5 pieces add karo” |
| Total stock set karna | “Blue kurta size S ka total stock 12 set karo” |
| Alerts | “Kaunse designs low stock hain?” |
| Reorder | “Pending orders ke liye kya restock karna hai?” |
| Orders | “Pending orders ka status batao” |
| Aaj ka dispatch | “Aaj ki sales ka short summary do” |
| Recent changes | “Aaj ke stock changes dikhao” |
| App ka overview | “App ka status batao” |
| Normal conversation | Seedha apna aam sawaal poochhein; har sawaal inventory ka hona zaroori nahi. |

## Stock update ko confirm karein

Stock update se pehle Disha current count aur proposed count dikhata hai. Dhyan se check karein:

- **Haan / Yes / Confirm** bolne ya action card ka **Confirm** button dabane par update hota hai.
- **Nahi / Cancel** bolne par update cancel hota hai.
- “5 add karo” ka matlab current count mein 5 jodna hai. “Total 12 set karo” ka matlab final count 12 karna hai.
- Agar size pehle physical count nahi hua, pehle us size ka actual count enter karna hoga. Isse anjaane mein zero ya galat stock se total nahi banega.
- Agar Disha current aur proposed count dikhaye, galat design, colour, size ya number ho to **confirm na karein**. Sahi details dobara bhejein.

Product banana/edit karna, Trash mein bhejna/restore karna, aur order batch dispatch karna bhi action card ke baad hi hota hai. Dispatch se pehle batch number aur units dekh lein. Disha ke paas arbitrary database, file system, ya provider account ka access nahi hai.

## Voice aur earbuds

1. Chrome ya Edge mein app ka HTTPS address kholen. Browser site controls/lock icon se **Microphone: Allow** karein, phir robot button dabayen.
2. Earbuds istemal karte waqt unka microphone phone/PC ke Bluetooth ya sound input settings mein select karein. Browser wahi input device use karega jo system/browser ne select kiya hai.
3. Robot button dabane ke baad status **Listening · Hindi and English** aayega. Seedha bolen: “Blue kurta size M ka stock batao.”
4. Confirmation ke liye **haan** ya **nahi** saaf bol sakte hain. Jawab na sunna ho to pause button dabayen; dobara chalane ke liye resume button dabayen.
5. Nayi alag baatcheet ke liye **+** dabayen. Pichhli conversation kholne ke liye history button use karein.

Mic ek continuous stream mein chalta hai aur sentence recording alag hoti hai, isliye har sentence ke beech browser mic ko stop/start nahi karta. Disha bolte waqt echo se bachne ke liye listening thodi der rukti hai. Header ka pause button mic aur voice dono band karta hai. Panel band karne ya tab background mein jaane par mic off hota hai; phir robot button se dobara start karein.

Voice ke liye HTTPS, microphone permission, OpenAI API key/credits aur internet connection chahiye. Audio server ke zariye OpenAI transcription API ko jaata hai. Spoken reply warm built-in voice se aata hai; API response unavailable ho to browser/device voice fallback hoti hai. Uploaded audio ko exact voice clone ke roop mein use nahi kiya gaya. Earbuds ka mic phone/PC ke active sound input device mein select hona chahiye. Photo zoom ke liye phone par `+`/`−` controls aur computer par `+`, `−`, `0` keys ya mouse wheel use karein.

Print Labels mein har design ke liye ek Code128 product barcode hai. Barcode scanner/camera ise padhkar product ke sabhi sizes kholta hai; size-wise label list use karne ki zaroorat nahi.

## Product photo

“[design naam] ki photo dikhao” bolen ya type karein. Photo preview ke **Open product & sizes** se us design ka detail khul sakta hai; **Change photo** se nayi product image upload hoti hai. Photo badalne se pehle sahi design khula hai ye check karein.

## Agar Disha jawab na de

- Text chat se ek baar dobara bhejein; send button loading mein ho to doosra message bhejne se pehle response ka wait karein.
- “Assistant temporarily unavailable” aaye to page reload karke dobara try karein. Tab tak app ke normal Products, Scan & Recount aur stock tools use kar sakte hain.
- `429`/rate limit aaye to thoda wait karein. `401/403` aaye to app admin Railway Variables mein provider key/config check kare; key chat mein mat bhejein.
- Product galat match ho to naam ke saath colour/SKU yaad ho to add karein; multiple results mein sahi design choose karein.

## Conversation history

History is browser ke local storage mein isi device/browser par rehti hai. Browser storage clear karne, private/incognito window band karne, ya doosre device par jaane se purani local chats nahi milengi. History mein inventory details ho sakti hain; shared computer par use karne ke baad history ko dhyan se manage karein.
