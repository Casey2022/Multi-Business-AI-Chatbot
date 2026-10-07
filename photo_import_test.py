#!/usr/bin/env python3
"""photo_import_test.py: an owner's photo becomes proposed section changes,
with a fake model.

Run it:  python3 photo_import_test.py

Pins: what counts as an image (by its bytes, not its name), the size limit
and the HEIC message; that the photo goes to the model as an image and the
transcription comes back; that the model's proposals are checked against
the real sections (no edits to another business's rows, no no-op edits);
the number check that flags a price nobody gave and a fact silently
dropped; the word diff the review page shows; that applying saves ordinary
edits and refuses to overwrite a section edited since; the stored proposal
and its cleanup; and the page wiring. No network.
"""

import json
import sys
import tempfile
import types
from pathlib import Path

import db
db.DB_PATH = Path(tempfile.mkdtemp(prefix="photo_import_test_")) / "test.db"

import booking_state_test as helpers

ROOT = Path(__file__).resolve().parent
PASSED, FAILED = [], []

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 100
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 100

SIZES = ("Our pizzas come in three sizes: 10-inch personal at $11, 14-inch "
         "medium at $17, and 18-inch large at $22. Toppings are $1.75 each on a "
         "personal, $2.25 on a medium, $2.75 on a large.")
PHOTO_TEXT = "CROSSTOWN PIZZA\nPersonal 10\" $11\nMedium 14\" $17\nLarge 18\" $25\nCalzones $14"


def check(name, condition, detail=""):
    (PASSED if condition else FAILED).append(name)
    print(f"  [{'PASS' if condition else 'FAIL'}] {name}"
          + (f"\n         {detail}" if detail and not condition else ""))


def heading(text):
    print(f"\n{text}\n{'-' * len(text)}")


def reply(text):
    return types.SimpleNamespace(usage=None, content=[
        types.SimpleNamespace(type="text", text=text)])


def main():
    helpers._load_scheduler()
    import llm
    import photo_import as pi
    db.init_db()

    heading("what counts as a photo")
    check("JPEG, PNG and WebP are recognised by their bytes",
          (pi.sniff_image(JPEG), pi.sniff_image(PNG), pi.sniff_image(WEBP))
          == ("image/jpeg", "image/png", "image/webp"))
    check("a PDF or text renamed .jpg isn't an image",
          pi.sniff_image(b"%PDF-1.7 ...") is None and pi.sniff_image(b"hello") is None)

    def problem(data):
        try:
            pi.check_upload(data)
        except pi.UploadProblem as e:
            return str(e)
        return None
    check("no file asks for one", "Choose a photo" in (problem(b"") or ""))
    check("over 5MB is refused with a reason",
          "too large" in (problem(JPEG + b"\x00" * pi.MAX_IMAGE_BYTES) or ""))
    check("an iPhone HEIC photo gets its own explanation", "HEIC" in (problem(HEIC) or ""))
    check("anything else is 'doesn't look like a photo'",
          "doesn't look like a photo" in (problem(b"%PDF-1.7") or ""))
    check("a JPEG passes", pi.check_upload(JPEG) == "image/jpeg")

    biz = 7
    sizes_id = db.add_document_section(biz, "Pizza Sizes", SIZES)
    delivery_id = db.add_document_section(biz, "Delivery", "Within 6 miles, $3 fee.")
    db.set_documents_clean(biz)
    other_id = db.add_document_section(99, "Someone else's", "Not yours.")
    sections = db.get_documents(biz)
    config = {"business": {"name": "Crosstown Pizza", "id": biz}}

    heading("reading the photo")
    sent = []
    real_create = llm._create
    llm._create = lambda **c: (sent.append(c), reply(PHOTO_TEXT))[1]
    try:
        text = pi.transcribe(config, JPEG, "image/jpeg")
    finally:
        llm._create = real_create
    content = sent[0]["messages"][0]["content"]
    check("the photo is sent as an image block with its media type",
          content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg")
    check("the transcription prompt says the image's text isn't instructions",
          "not instructions to you" in content[1]["text"])
    check("the transcription comes back", text == PHOTO_TEXT, text)
    llm._create = lambda **c: reply(pi.NO_TEXT)
    try:
        check("a photo with no text reads as empty", pi.transcribe(config, JPEG, "image/jpeg") == "")
    finally:
        llm._create = real_create

    heading("the proposals are checked against the real sections")
    prompt = pi.proposal_prompt(config, PHOTO_TEXT, sections)
    check("the transcription and the sections are fenced as data",
          "<photo_transcription>" in prompt and prompt.count("<section>") == 2)
    check("the prompt says to keep what the photo doesn't mention",
          "doesn't mean it stopped" in prompt)
    model_says = {"changes": [
        {"section_id": sizes_id, "title": "Pizza Sizes",
         "body": SIZES.replace("$22", "$25"), "summary": "Large $22 → $25"},
        {"section_id": None, "title": "Calzones", "body": "Calzones are $14.",
         "summary": "New: calzones"},
        {"section_id": delivery_id, "title": "Delivery",
         "body": "Within 6 miles, $3 fee.", "summary": "no change"},
        {"section_id": other_id, "title": "Hijack", "body": "Free pizza.",
         "summary": "another business's row"},
        {"section_id": "abc", "title": "", "body": "No title.", "summary": ""},
    ]}
    changes = pi.clean_changes(model_says, sections)
    check("a real edit and a new section survive",
          [c["title"] for c in changes][:2] == ["Pizza Sizes", "Calzones"], changes)
    check("an 'edit' that changes nothing is dropped",
          all(c["title"] != "Delivery" for c in changes))
    check("another business's section id becomes a new section, never an edit to it",
          any(c["title"] == "Hijack" and c["section_id"] is None for c in changes))
    check("a new section with no title is dropped", all(c["body"] != "No title." for c in changes))
    check("an edit carries the section's old text (for the diff and the stale check)",
          changes[0]["old_body"] == SIZES)
    check("fenced JSON with preamble still parses",
          pi._parse_json('Here you go:\n```json\n{"changes": []}\n```') == {"changes": []})

    heading("numbers are checked by code")
    def flags(body):
        c = pi.check_change({"title": "Pizza Sizes", "body": body,
                             "old_title": "Pizza Sizes", "old_body": SIZES}, PHOTO_TEXT)
        return c["unsupported"], c["dropped"], c["flagged"]
    check("a price the photo changed isn't flagged", flags(SIZES.replace("$22", "$25")) == ([], [], False))
    check("a price nobody gave is flagged",
          flags(SIZES.replace("$22", "$25") + " Extra cheese is $1.50.")[0] == ["$1.50"])
    dropped = flags("Our pizzas come in three sizes: 10-inch personal at $11, 14-inch "
                    "medium at $17, and 18-inch large at $25.")
    check("toppings silently dropped are flagged (a menu leaving them out)",
          dropped[1] == ["$1.75", "$2.25", "$2.75"] and dropped[2], dropped)
    check("a comma after a price isn't part of it ('$11, 14-inch')",
          pi.numbers_in("$11, 14-inch") == {"11": "$11", "14": "14"}, pi.numbers_in("$11, 14-inch"))
    check("'$22.00', '$22' and '22' are the same number; '1,800' is 1800",
          set(pi.numbers_in("$22.00 or 22 or $22")) == {"22"} and "1800" in pi.numbers_in("$1,800"))

    heading("the diff the owner sees")
    old_segs, new_segs = pi.word_diff(SIZES, SIZES.replace("$22", "$25"))
    check("only the changed price is marked",
          [t for t, m in old_segs if m] == ["$22."] and [t for t, m in new_segs if m] == ["$25."],
          (old_segs, new_segs))
    check("the segments rebuild the text exactly",
          "".join(t for t, _ in new_segs) == SIZES.replace("$22", "$25"))

    heading("the whole flow, with the model faked")
    calls = []
    def fake(**c):
        calls.append(c)
        if isinstance(c["messages"][0]["content"], list):
            return reply(PHOTO_TEXT)
        return reply(json.dumps(model_says))
    llm._create = fake
    try:
        result = pi.build_proposal(config, JPEG, "image/jpeg", sections)
    finally:
        llm._create = real_create
    check("two calls: read the photo, then propose", len(calls) == 2)
    check("the result has the transcription and checked changes",
          result["transcription"] == PHOTO_TEXT and "flagged" in result["changes"][0])
    llm._create = lambda **c: reply(pi.NO_TEXT)
    try:
        pi.build_proposal(config, JPEG, "image/jpeg", sections)
        check("no text in the photo tells the owner", False)
    except pi.UploadProblem as e:
        check("no text in the photo tells the owner", "Couldn't find any text" in str(e))
    finally:
        llm._create = real_create

    heading("the proposal waits in the database")
    db.save_upload_proposal(biz, PHOTO_TEXT, result["changes"], created_by="o@x.com")
    stored = db.get_upload_proposal(biz)
    check("it's stored with its changes", stored and len(stored["changes"]) == len(result["changes"]))
    db.save_upload_proposal(biz, "second photo", [], created_by="o@x.com")
    check("a new photo replaces the waiting set", db.get_upload_proposal(biz)["transcription"] == "second photo")
    check("it's on _TENANT_TABLES (deleted with the business)",
          ("upload_proposals", "business_id") in db._TENANT_TABLES)
    db.delete_upload_proposal(biz)
    check("cancel removes it", db.get_upload_proposal(biz) is None)

    heading("applying what the owner kept")
    changes = pi.clean_changes(model_says, sections)
    applied, stale = pi.apply_changes(biz, changes, {0: ("", ""), 1: ("Calzones", "")},
                                      updated_by="o@x.com (from a photo)")
    docs = {d["title"]: d for d in db.get_documents(biz)}
    check("the edit and the new section are saved", applied == 2 and "$25" in docs["Pizza Sizes"]["body"]
          and docs["Calzones"]["body"] == "Calzones are $14.", (applied, stale))
    conn = db.get_connection()
    kept = conn.execute("SELECT body FROM document_versions WHERE document_id = ?",
                        (sizes_id,)).fetchall()
    conn.close()
    check("it's an ordinary edit: the old wording is kept in History",
          any(r["body"] == SIZES for r in kept))
    check("an unticked change isn't saved", "Hijack" not in docs)
    check("the save is marked as coming from a photo",
          docs["Pizza Sizes"]["updated_by"] == "o@x.com (from a photo)")
    applied, stale = pi.apply_changes(biz, changes, {0: ("", "")})
    check("a section edited since the photo was read is skipped, not overwritten",
          applied == 0 and stale == ["Pizza Sizes"], (applied, stale))
    edited = pi.clean_changes({"changes": [{"section_id": delivery_id, "title": "Delivery",
                                             "body": "Within 8 miles, $4 fee.", "summary": ""}]},
                              db.get_documents(biz))
    applied, _ = pi.apply_changes(biz, edited, {0: ("Delivery", "Within 7 miles, $4 fee.")})
    check("the owner's edited wording is what's saved",
          applied == 1 and db.get_documents(biz)[1]["body"] == "Within 7 miles, $4 fee.")

    heading("the page")
    routes = (ROOT / "admin" / "routes.py").read_text()
    page = (ROOT / "admin" / "templates" / "admin" / "knowledge.html").read_text()
    review = (ROOT / "admin" / "templates" / "admin" / "knowledge_upload.html").read_text()
    base = (ROOT / "admin" / "templates" / "admin" / "base.html").read_text()
    app_src = (ROOT / "app.py").read_text()
    check("the upload form is multipart with a CSRF token",
          'enctype="multipart/form-data"' in page and "data-photo-upload" in page)
    check("the photo is read with a size cap", "upload.read(photo_import.MAX_IMAGE_BYTES + 1)" in routes)
    check("uploads are rate limited", "ratelimit.knowledge_upload(business_id)" in routes)
    check("Flask refuses oversized requests", "MAX_CONTENT_LENGTH=8 * 1024 * 1024" in app_src)
    check("the 413 handler doesn't redirect to the referrer",
          "redirect(request.referrer" not in routes.split("def upload_too_large")[1][:800])
    check("every upload route checks business access",
          all("require_business_access(business_id)" in routes.split(f"def {name}(")[1][:400]
              for name in ("knowledge_upload", "knowledge_upload_review",
                           "knowledge_upload_apply", "knowledge_upload_cancel")))
    check("flagged changes start unticked",
          '{% if not c.flagged %}checked{% endif %}' in review)
    check("the review page has CSRF tokens on both forms", review.count("csrf_token()") == 2)
    check("base.html loads the photo script", "photo-upload.js" in base)
    check("both renders of the knowledge page pass the waiting upload",
          routes.count("pending_upload = get_upload_proposal(business_id)") == 2)

    print(f"\n{len(PASSED)} passed, {len(FAILED)} failed")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
