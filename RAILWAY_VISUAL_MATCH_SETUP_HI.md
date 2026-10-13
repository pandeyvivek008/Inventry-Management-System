# Railway: SigLIP 2 visual product search

This deploy package switches the visual matcher from generic CLIP to Google's public `google/siglip2-base-patch16-224` image-text encoder and stores versioned 768-dimensional vectors for product-linked reference photos. SigLIP 2 is multilingual and supports image retrieval; it is not fine-tuned on Twins Lady's catalog. Use actual catalog photos to set thresholds before treating any match as confirmed. Model weights are downloaded from Hugging Face at first use unless already cached.

## Before enabling on production

1. Deploy first to a staging Railway environment with a copy of the catalog and image volume. Do not run test uploads or index rebuilds against a production database without a restorable backup and an approved maintenance plan.
2. Give the app persistent storage for product images and model cache. Example volume mount `/data` with:

   ```text
   PRODUCT_IMAGE_DIR=/data/product_images
   HF_HOME=/data/huggingface
   TRANSFORMERS_CACHE=/data/huggingface/transformers
   ```

3. Install CPU PyTorch and visual dependencies in the Railway Build Command:

   ```sh
   pip install --index-url https://download.pytorch.org/whl/cpu torch && pip install -r requirements.txt -r requirements-visual-ai.txt
   ```

   Keep the existing app start command. This model package is substantially heavier than the old CLIP encoder; confirm the service can build, start, load the model, and keep enough memory for the rest of the app before rolling it out. The Hugging Face model card lists roughly 1.5 GB of weight files, before Python/PyTorch runtime memory.

4. Set the existing conservative starting values, then calibrate them on a labeled validation set:

   ```text
   VISUAL_MATCH_MIN_SCORE=0.82
   VISUAL_MATCH_MIN_MARGIN=0.06
   VISUAL_SEARCH_DEV_MAX_CATALOG=500
   VISUAL_SEARCH_RPM=30
   ```

   These scores are cosine similarities, not confidence percentages. The values above are provisional; do not lower them just to force a result. Include same-color near-duplicate designs, different colors, alternate camera angles, dark/blurred photos, non-garment images, and products absent from the catalog. Record top-1 accuracy, top-5 recall, false-accept rate, and no-match rate.

## Index existing catalog photos

After the app deploys with the new `product_images` table and the persistent image volume is mounted, run once from the same Railway service/container environment:

```sh
cd backend
python scripts/rebuild_visual_index.py
```

The script links legacy `products.image_path` records to their actual product ID, then writes embeddings to `product_visual_embeddings_siglip2_v1`. It does not update stock, orders, or transaction history. If the script skips any photos, fix the file/volume issue and rerun it before relying on visual search.

Each new product's first photo is the main catalog photo. Add up to seven extra genuine reference views using the Add Product photo picker; they are linked to the same product record and indexed separately. Replacing the main photo refreshes its vector. A failed index leaves the saved image in place, logs the failure, and causes the search to fail closed until reindexing succeeds.

## Staging acceptance

- Confirm product images still load after redeploy (persistent volume mounted to the same path).
- Try several real phone photos of each design and hard negatives. A close candidate must return `ambiguous`; unknown/poor images must return `no_match`.
- Verify each result shows the product identity and current size-wise stock from the database.
- Verify the scan endpoint never changes stock or creates products.
- If the model/index is unavailable, manual product search and recount remain available.

## Rollback and limits

- The prior `product_visual_embeddings_clip_v1` table and product images are retained; this version creates a separate SigLIP 2 index. No existing inventory rows are migrated or deleted by index rebuild.
- Roll back the app build if model loading causes memory/startup problems; visual search will be unavailable on that version, but manual inventory workflows remain.
- Visual search is a candidate retrieval feature, not a guarantee of exact identification. A Twins Lady photo benchmark and calibrated thresholds are still required before claiming production accuracy.
- The service requires outbound access to download the public Hugging Face model on first use. No Hugging Face token is required for this public checkpoint.
