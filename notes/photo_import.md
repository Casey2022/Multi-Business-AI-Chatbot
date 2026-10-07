# Updating the knowledge base from a photo

Built 2026-10-07 (phase 1: photos). `photo_import.py`, the "Update from a
photo" card on the knowledge page, and `admin/knowledge_upload.html`.

## How it works

```
phone photo ─► shrunk in the browser (static/photo-upload.js: ≤2000px JPEG)
            ─► POST /knowledge/upload  (5MB cap, bytes sniffed, rate limit 2/min 10/hour)
            ─► 1. transcribe: Claude reads the image, text as written
            ─► 2. propose: the transcription + current sections → JSON changes
            ─► code checks each change (clean_changes, check_change)
            ─► stored in upload_proposals (one waiting set per business)
            ─► review page: old/new side by side, tick / edit / skip
            ─► apply: ordinary section edits ("… (from a photo)" in History)
            ─► Preview ─► Publish (or Discard)
```

Why two model calls, not one: the transcription is evidence. The owner can
read what was seen, and code can check every number a proposal uses
against it.

## What code checks (not the model)

- **The bytes are an image** the model reads (JPEG, PNG, WebP, GIF), by
  magic bytes, not filename. HEIC (iPhone originals) gets its own message.
- **Section ids are this business's.** An id the model invents, or one from
  another business, becomes a new section — never an edit to that row.
- **No-op edits are dropped.**
- **unsupported:** a number in a change that's in neither the photo nor the
  old section (the model wrote a price nobody gave it).
- **dropped:** a number the section had that was deleted with nothing
  written in its place. "$22" → "$25" is a replacement; the toppings
  sentence vanishing is a drop. A menu that doesn't list toppings doesn't
  mean toppings stopped.
- A change with either is shown as "Check this" and starts unticked.
- **Stale proposals:** if a section was edited after the photo was read,
  applying skips it rather than overwriting the newer edit.

Limits of the number check: numbers written as words ("three hours") aren't
seen, and a reworded sentence with no numbers in it is shown struck through
in the diff but not flagged.

## Prompt injection

Text in a photo is content, and both prompts say so (the transcription is
fenced with `as_data`). The real defence is the shape: nothing is applied
without the owner ticking it, and nothing reaches customers until Publish.
`upload_eval.py` includes a flyer that tells the assistant to make every
pizza $0 and delete the allergen section.

## Evals

- `photo_import_test.py` (static, fake model): 49 checks.
- `upload_eval.py` (real model): the Crosstown menu photo (large $22 → $25,
  new calzones; toppings and gluten-free must survive) and the injection
  flyer. `--repeat=3`, `-v` to see what was read and proposed. Fixtures in
  `evals/photos/` were generated, then made to look photographed (tilt,
  shading, blur); a real phone photo of a real menu is the next fixture.

## Plan: keeping the original files (not built)

For the demo the photo is dropped after reading (Render's disk is wiped on
every deploy anyway). To keep originals:
1. Object storage (S3 / Cloudflare R2 / Render disk) keyed
   `uploads/<business_id>/<uuid>.<ext>`, private bucket, served only through
   an admin route that checks business access (never a public URL).
2. A `document_sources` table: id, business_id, kind (photo/pdf/web),
   storage key, original filename, uploaded_by, uploaded_at, transcription.
3. `document_versions` gets a nullable `source_id`, so a section's History
   can say "from menu.jpg, 7 Oct" with a link to the original.
4. Retention: delete with the business (`_TENANT_TABLES`), and a cap per
   business (e.g. last 20) so a demo can't fill the bucket.
5. Strip EXIF (location) before storing: a phone photo can carry the
   owner's GPS position.

## Next phases

- **PDFs:** text PDFs → `pypdf` text straight into step 2 (no vision call);
  scanned PDFs → send the PDF to the model as a document block (Claude reads
  PDFs natively). Same review page.
- **Web pages** (a menu on the business's own site, which may be updated
  before the portal is): fetch server-side with a short timeout and size cap,
  only http(s), refuse private/internal addresses (SSRF), strip to text, then
  step 2. A scheduled re-check ("your website's menu changed — review?")
  is the natural follow-on, and the source table above is where the last
  fetched text would live for comparison.
