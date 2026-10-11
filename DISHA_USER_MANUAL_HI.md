# Disha ka user manual

Disha Hindi, Hinglish aur English mein inventory ke sawaal samajhne aur app ke kaam guide karne ke liye hai. Aap **robot button** dabakar chat khol sakte hain, type karke bhej sakte hain, ya supported browser mein voice use kar sakte hain.

## Chat se shuru karein

1. App ko HTTPS link par kholen aur robot icon dabayen.
2. Message box mein apni baat aam bol-chal ki bhasha mein type karein; `Enter` dabayen ya send button chunein.
3. Disha jawab dega. Product ka naam, colour, size ya SKU yaad ho to batayein. SKU bolna zaroori nahi.
4. Agar ek jaise kai designs milen, sahi design/color chunein. Disha bina aapse poochhe andaza laga kar stock nahi badalta.

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

1. Chrome ya Edge mein app ka HTTPS address kholen. Browser site controls/lock icon se **Microphone: Allow** karein, phir page reload karke Disha kholen.
2. Earbuds istemal karte waqt unka microphone phone/PC ke Bluetooth ya sound input settings mein select karein. Browser wahi input device use karega jo system/browser ne select kiya hai.
3. Disha panel khula rakhein. Supported browser mein status **Listening for ‘Hi Disha’** aaye to wake phrase bolkar request dein: “Hi Disha, blue kurta size M ka stock batao.”
4. Confirmation ke liye **haan** ya **nahi** saaf bol sakte hain. Jawab na sunna ho to pause button dabayen; dobara chalane ke liye resume button dabayen.
5. Nayi alag baatcheet ke liye **+** dabayen. Pichhli conversation kholne ke liye history button use karein.

Voice browser aur device par depend karta hai. Browser tab background mein ho, phone lock ho, microphone permission band ho, ya browser Speech Recognition support na kare to wake phrase available nahi ho sakta. Aise mein type karke chat chalti rahegi. Agar status mic permission maange to browser ke site settings mein Disha app ke liye mic allow karke page reload karein. Photo zoom ke liye phone par `+`/`−` controls aur computer par `+`, `−`, `0` keys ya mouse wheel use karein.

## Product photo

“[design naam] ki photo dikhao” bolen ya type karein. Photo preview ke **Open product & sizes** se us design ka detail khul sakta hai; **Change photo** se nayi product image upload hoti hai. Photo badalne se pehle sahi design khula hai ye check karein.

## Agar Disha jawab na de

- Text chat se ek baar dobara bhejein; send button loading mein ho to doosra message bhejne se pehle response ka wait karein.
- “Assistant temporarily unavailable” aaye to page reload karke dobara try karein. Tab tak app ke normal Products, Scan & Recount aur stock tools use kar sakte hain.
- `429`/rate limit aaye to thoda wait karein. `401/403` aaye to app admin Railway Variables mein provider key/config check kare; key chat mein mat bhejein.
- Product galat match ho to naam ke saath colour/SKU yaad ho to add karein; multiple results mein sahi design choose karein.

## Conversation history

History is browser ke local storage mein isi device/browser par rehti hai. Browser storage clear karne, private/incognito window band karne, ya doosre device par jaane se purani local chats nahi milengi. History mein inventory details ho sakti hain; shared computer par use karne ke baad history ko dhyan se manage karein.
