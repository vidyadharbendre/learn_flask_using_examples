# Day 16 — File Uploads and Media

> **Goal:** accept files from strangers without handing them your filesystem —
> size limits, content sniffing, generated filenames, safe serving, thumbnails.
> **Time:** ~90 minutes · **Port:** 5016 · **Builds on:** Days 04, 10

---

## 1. Why this matters

An upload endpoint is the point where **an untrusted party writes bytes to your
disk and chooses their name**. Almost every rule below exists because somebody
once skipped it.

Filenames alone are a whole attack surface:

```
"../../../../etc/passwd"     path traversal
"../../app/routes.py"        overwrite your own code
"invoice.pdf.exe"            double extension
"CON", "NUL", "PRN"          reserved Windows device names
"logo.svg"                   XML containing <script> → stored XSS
```

**The defence that beats all of them at once:** never build a filesystem path
from user-supplied text. Generate a random name; keep theirs only as a label.

## 2. The five questions every upload must answer

| Question | Answer in this app |
|---|---|
| How big? | `MAX_CONTENT_LENGTH` (outer) + a per-file check (inner) |
| What is it? | parse the bytes — never the extension or declared type |
| Where does it go? | a `uuid4` name, in a directory **outside** `static/` |
| Who may read it? | a view function, never a direct URL |
| What if it's hostile? | no SVG, pixel limits, `Content-Disposition: attachment` |

## 3. Run it

```bash
source .venv/bin/activate
cd 16_file_uploads_and_media
FLASK_APP=wsgi.py flask run --port 5016 --debug
```

Open <http://127.0.0.1:5016/>.

## 4. Try it — learn by doing

Make some test files:

```bash
python - <<'PY'
from PIL import Image
Image.new("RGB", (900, 600), (56,189,248)).save("/tmp/photo.png")
open("/tmp/notes.txt","w").write("definitely not an image")
open("/tmp/evil.svg","w").write('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
PY
```

Now attack your own endpoint:

```bash
V=http://127.0.0.1:5016

# 1. A PNG named .pdf, DECLARED as application/pdf  → accepted, stored as .png
curl -sX POST $V/ -F "file=@/tmp/photo.png;filename=invoice.pdf;type=application/pdf"

# 2. A text file named .jpg, declared image/jpeg    → REJECTED
curl -sX POST $V/ -F "file=@/tmp/notes.txt;filename=sneaky.jpg;type=image/jpeg"

# 3. An SVG containing <script>                     → REJECTED
curl -sX POST $V/ -F "file=@/tmp/evil.svg;type=image/svg+xml"

# 4. Path traversal in the filename                 → stored under a uuid
curl -sX POST $V/ -F "file=@/tmp/photo.png;filename=../../../../etc/passwd.png"

# 5. Oversized body                                 → 413, before your view runs
head -c 6000000 /dev/urandom > /tmp/huge.png
curl -s -o /dev/null -w "%{http_code}\n" -X POST $V/ -F "file=@/tmp/huge.png"

curl -s $V/api/files | python -m json.tool
```

Then look at the results:

```bash
ls uploads/originals/     # every file is a uuid — none of your names survive
```

```
6b404e533a804167b0b090c783017735.png
6d23bfa534a44b2da11be74f6a8c09fd.png
```

The list page shows `etc_passwd.png` as the **label** and the uuid as the path.
Your text never touched the filesystem.

## 5. `secure_filename` — necessary, not sufficient

```python
secure_filename("../../etc/passwd")   # -> "etc_passwd"
secure_filename("my photo.jpg")       # -> "my_photo.jpg"
secure_filename("../../../☃.png")     # -> "png"
```

It strips directories, normalises unicode, and removes dangerous characters. But
it has **two gaps that matter**:

1. It can return an **empty string** — and `open(directory / "")` does not do
   what you want.
2. It does not stop one user **overwriting another's** file of the same name.

So use it for the *label* and let the server pick the path:

```python
stored_name = f"{uuid.uuid4().hex}{detected_extension}"
```

One decision, and path traversal, overwrites, double extensions, reserved device
names and null-byte tricks all stop mattering.

## 6. Content sniffing: the bytes decide

```python
# ❌ all client-controlled, all trivially forged
if file.filename.endswith(".png"): ...
if file.content_type == "image/png": ...

# ✅ ask the bytes
with Image.open(stream) as image:
    image_format = image.format          # "PNG", "JPEG", …
    image.verify()                       # structural integrity check
if image_format not in ALLOWED_IMAGE_FORMATS:
    raise UploadRejected(...)
```

`curl -F "file=@shell.php;type=image/png"` sets whatever type it likes. **Type
is a property of the content, not of the metadata.**

Note `Image.verify()` must be the **last** operation on that `Image` object — it
consumes the file. And the extension written to disk comes from what was
*detected*, so `invoice.pdf.exe` containing a PNG is stored as `.png`.

## 7. Why SVG is not on the allow-list

An SVG is **XML**, and XML can contain `<script>`. Serve a user-supplied SVG
from your own domain and it executes in *your* origin, with access to your
users' cookies. That is stored XSS.

If you must accept SVG: sanitise the XML, serve it from a **separate origin**,
and never inline it into a page.

More generally, use an **allow-list**. "Everything except `.exe`" is unwinnable —
you cannot enumerate every dangerous type, and new ones keep arriving.

## 8. Decompression bombs

A 10 KB PNG can legitimately declare dimensions of 50,000 × 50,000. Decoding it
allocates gigabytes — a denial of service that costs the attacker nothing.

```python
Image.MAX_IMAGE_PIXELS = 40_000_000       # Pillow warns above, refuses at 2×
if width * height > MAX_IMAGE_PIXELS:
    raise UploadRejected("dimensions unreasonably large")
```

**A size limit in bytes is not a limit on the work you will do.**

## 9. Where files live, and how they are served

```python
upload_root = Path(__file__).parent.parent / "uploads"     # NOT static/
```

If uploads sat in `static/`, the web server would serve them **directly** —
with whatever content type it inferred, to anyone who guessed a URL, with no
authorisation check possible.

Serving through a view gives you control:

```python
response = send_from_directory(
    ORIGINALS_DIR, upload.stored_name,
    as_attachment=True,                    # ← Content-Disposition: attachment
    download_name=upload.original_name,    # ← give the user THEIR name back
)
response.headers["X-Content-Type-Options"] = "nosniff"
```

| Choice | Why |
|---|---|
| Look up by database **id** | the client never names a path |
| `send_from_directory` | refuses anything resolving outside the directory |
| `as_attachment=True` | the browser **saves**, never renders — this neutralises an uploaded HTML/SVG |
| `download_name` | the user's filename, without it touching the disk |
| `nosniff` | stops the browser second-guessing the declared type |

Thumbnails *are* served inline — safe **only** because we generated them: they
are re-encoded JPEGs produced by Pillow, not the user's bytes.

## 10. Thumbnails (and a privacy bonus)

```python
image.thumbnail((320, 320))            # in place, aspect preserved, never enlarges
image.save(dest, "JPEG", quality=82)
```

- `thumbnail()`, not `resize()` — the latter distorts anything off-ratio.
- RGBA/P images are flattened onto white first: **JPEG has no alpha channel**,
  and saving an RGBA image as JPEG raises `OSError`.
- Re-encoding **strips EXIF**, including GPS coordinates. Serving a user's
  original holiday photo with its location intact is a real privacy leak.
- Thumbnail failure is logged and swallowed — a missing preview is cosmetic and
  must not lose an upload the user already made.

## 11. Two stores that cannot share a transaction

The row is in the database; the bytes are on disk. No transaction spans both, so
they *will* drift. Choose the failure you can detect:

- **Delete the file first**, then the row → worst case, a row pointing at a
  missing file: visible, and fixable.
- **Delete the row first** → worst case, an orphaned file nothing references and
  nobody will ever identify.

And reconcile periodically:

```bash
FLASK_APP=wsgi.py flask find-orphans
```

```
rows: 5   files: 5
files with no row : 0
rows with no file : 0
```

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| `enctype="multipart/form-data"` | without it `request.files` is **empty** |
| `MAX_CONTENT_LENGTH` | rejects a huge body before it is buffered |
| Per-file size check from the stream | never trust `Content-Length` |
| Cheap checks before expensive ones | don't decode 5 MB to find it's empty |
| Detect type from **content** | extensions and MIME types are client-supplied |
| Allow-list of formats | you cannot enumerate everything dangerous |
| `uuid4` filenames | defeats traversal, overwrites, double extensions at once |
| Extension from the **detected** type | `.pdf.exe` holding a PNG becomes `.png` |
| Store outside `static/` | otherwise the web server serves it unchecked |
| Serve via a view, keyed by id | authorisation becomes possible |
| `as_attachment=True` | uploaded HTML/SVG is saved, never executed |
| `nosniff` on every file response | no content-type second-guessing |
| Pixel limits, not just byte limits | decompression bombs |
| Re-encode thumbnails | strips EXIF/GPS as a side effect |
| Delete files **and** rows | orphans are invisible waste |
| Idempotent deletes (`missing_ok=True`) | the two stores can desynchronise |
| A reconciliation command | drift is a matter of when, not if |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `request.files` empty | missing `enctype` | add `multipart/form-data` |
| `400 Bad Request` on upload | `request.files["file"]` on a missing key | use `.get()` |
| Files overwrite each other | user-supplied filename kept | generate a `uuid4` name |
| Path traversal succeeds | path built from user text | never do that |
| `.php`/`.svg` executes on your domain | uploads served from `static/` | serve via a view, `as_attachment` |
| A "PNG" crashes the image library | trusted the extension | sniff the content |
| Server OOMs on a small file | decompression bomb | `MAX_IMAGE_PIXELS` |
| `OSError: cannot write mode RGBA as JPEG` | alpha channel | convert to RGB first |
| Blank error page on a big upload | no 413 handler | add one |
| Disk fills over years | rows deleted, files kept | delete both; run `find-orphans` |
| Photos leak users' locations | EXIF preserved | re-encode |
| `secure_filename` returns `""` | name was entirely unsafe characters | fall back to a generated name |

## 14. Exercises

1. Add per-user ownership (Day 13) and check it in `download` — right now
   **any** visitor can fetch **any** file by id.
2. Store uploads in S3 with `boto3` and serve them via short-lived **presigned
   URLs**. Note this moves the authorisation decision to URL-issue time.
3. Add a virus scan with `clamd` before accepting a file.
4. Support resumable/chunked uploads for large files.
5. Add `python-magic` (libmagic) as a second opinion alongside Pillow, and
   handle the case where the two disagree.
6. Generate thumbnails in a background job (Day 19) so the upload response is
   not blocked by image processing.
7. Add a `?w=` query parameter that generates and caches sized variants on
   demand — and cap the allowed widths, or you have invited a CPU-exhaustion
   attack.

## 15. What's next

**[Day 17 — Testing with pytest →](../17_testing_with_pytest/)**
Day 14 shipped a test suite. Now learn the craft: fixtures, isolation, what to
test and — just as important — what not to.

---

<!-- nav -->
[← Day 15 — JWT Auth and Role-Based Access Control](../15_jwt_auth_and_rbac/) · **[All 21 days](../README.md)** · [Day 17 — Testing with pytest →](../17_testing_with_pytest/)
