# Railway par visual AI setup (safe runbook)

Yeh guide product-photo matching ke liye CPU OpenCLIP ViT-B/32, PostgreSQL pgvector aur persistent product images/model cache set karta hai. Yeh app-service code ko GitHub/Railway par deploy-ready banata hai, lekin live Railway variables ya database ko yahan se nahi badalta.

## Pehle production data safe karein

1. Railway project ka **production environment** kholen; web app service aur attached PostgreSQL ko identify karein.
2. PostgreSQL ka Railway backup/snapshot available ho, aur restore point samajh mein ho tabhi aage badhein. `DATABASE_URL` ko replace na karein.
3. Pehle Railway ka isolated **staging environment** banayein. Usme staging PostgreSQL use karein; production ke saath staging URL share na karein.
4. Is code ko GitHub branch par push karne ke baad pehle staging deploy karein.

## 1) Build dependencies

Railway → web app service → **Settings → Build → Custom Build Command** mein yeh command set karein:

```bash
pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision && pip install -r requirements.txt -r requirements-visual-ai.txt
```

Isse CPU PyTorch aur OpenCLIP install hote hain; GPU required nahi. Existing start command/Procfile chalta rahe: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`. Official OpenCLIP package isi model creation API ko document karta hai: ViT-B/32 + `laion2b_s34b_b79k`.

## 2) Persistent volume aur variables

Web app service ko ek Railway Volume attach karein, mount path **`/data`**. Yeh folder product photos aur first-run model cache ko deployments ke beech bachata hai. Railway volumes runtime par mount hote hain (build ke dauran nahi); isliye model runtime par pehli baar download hota hai.

Web app **service** ke variables mein ye add karein:

```text
PRODUCT_IMAGE_DIR=/data/product_images
HF_HOME=/data/huggingface
TORCH_HOME=/data/torch
VISUAL_MATCH_MIN_SCORE=0.82
VISUAL_MATCH_MIN_MARGIN=0.06
```

`DATABASE_URL` ko current Railway Postgres reference se hi rehne dein. `REDIS_URL` optional hai. Existing production variables jaise Gemini/OpenAI keys ko change karne ki zaroorat nahi.

`0.82` aur `0.06` starting guardrails hain, certified accuracy nahi. Real catalog photos par labeled testing ke baad hi inhe badlein. Score probability nahi hai.

## 3) Deploy aur persistent storage verify karein

Staging deploy successful hone par service log mein app startup dekhein. Ek **staging-only** product par photo upload karke confirm karein ki image URL khulti hai. Phir `railway ssh` se `/data/product_images` check karein; deployment ke baad bhi image wahi honi chahiye.

Railway dashboard ke **service variables** aur **volume** app web service par lagen, Postgres service par nahi. Image files abhi isi web service ke `/data/product_images` volume mein rahengi. Volume wali Railway service replicas ke saath horizontally scale nahi hoti; multiple replicas/regions ke liye S3-compatible shared object storage chahiye.

## 4) PostgreSQL image index build/rebuild karein

PostgreSQL ko `vector` extension support karna hoga. Staging web service par Railway CLI se shell kholen (Railway CLI login/project/service link pehle zaroori hai):

```powershell
railway ssh --service <YOUR-WEB-SERVICE-NAME>
```

Railway container ke shell mein:

```bash
cd /app/backend
python scripts/rebuild_visual_index.py
```

Yeh command catalog ke current saved product photos padhegi aur table `product_visual_embeddings_clip_v1` mein vectors upsert karegi. Yeh inventory stock, orders ya transactions ko nahi badalti. Script `CREATE EXTENSION vector` par fail ho to baar-baar run na karein: pehle confirm karein ki aapke Railway Postgres image/plan mein pgvector extension installed/allowed hai. Is setup mein extension mandatory hai; use bypass karke nearest color result ko match mat dikhayein.

Photo upload ke baad embedding automatically update hoti hai. Agar index update fail ho to photo save rehti hai aur purana vector hata diya jata hai, taa ki purani photo se galat design return na ho. App log mein “Product photo saved but visual indexing failed” dekhein; database/index theek karke rebuild chalayein.

## 5) Staging validation—production catalog se pehle

Staging par kam-se-kam 20–30 manually labeled samples rakhein:

- Wahi product: front/side, phone lighting, thoda distance (positive match).
- Same color ke alag prints/neckline/sleeves wale garments (hard negative).
- Alag design ya catalog mein na hone wala garment (no match).
- Do almost-identical designs (ambiguous; auto-choice nahi).

Har photo ko Scan & Recount mein upload karein. `matched` SKU actual photo jaisa ho; `ambiguous` kisi size ko update na kare; `no_match` mein wrong top result na aaye. Threshold tune karne se false positives badh rahe hon to threshold ya margin badhayein, ya `no_match` rakhein. Manual count/stock flows ko staging mein alag se verify karein; image scan khud stock nahi badalta.

Real photos se measured precision/false-positive rate note kiye bina production rollout ya “perfect/accurate” claim na karein. Current automated regression tests mocked embeddings par decision rules verify karte hain; woh fashion accuracy benchmark nahi.

## Rollback

- Matching logic ko rollback karte waqt product database/stock data ko rollback na karein.
- Purana color-vector table `product_visual_embeddings` delete nahi hota; naya version `product_visual_embeddings_clip_v1` alag hai.
- Agar model package/Railway build memory fail kare, visual matcher 503 safe response dega; manual SKU/name search aur recount available rehna chahiye.
- Image volume ko detach/delete mat karein. Volume delete karne se product photo files kho sakti hain.

## Current deployment limitations

- CLIP is a general photo-image encoder, fashion-specific fine-tune nahi; subtle print/cut differences can still be confused.
- First image search downloads/loads model if cache is cold; first request can take longer. Later requests reuse the process model/cache.
- One product currently has one indexed image; multiple-angle references and garment cropping are not implemented.
- Railway/PostgreSQL extension availability and actual service RAM/build capacity must be confirmed in your own staging project. This repository does not have access to those project settings.
