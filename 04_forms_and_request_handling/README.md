# Day 04 — Forms and Request Handling

> **Goal:** accept user input safely — the `request` object, POST/Redirect/GET,
> server-side validation, sticky fields, CSRF, and honeypots — **without a form
> library**, so that Day 05's Flask-WTF is comprehensible rather than magical.
> **Time:** ~90 minutes · **Port:** 5004 · **Builds on:** Day 03

---

## 1. Why this matters

> **Never trust the client.**

Every `required`, `maxlength` and `type="email"` in your HTML is a *polite
request* to a cooperating browser. `curl` ignores all of them. A form is not a
security boundary — your Python is. Today you will attack your own form from the
command line and watch the server hold the line.

## 2. What you will build

A "Request a demo" lead-capture form with five defences layered on it:

| Layer | Protects against |
|---|---|
| CSRF token | another site submitting the form as you |
| Honeypot field | naive spam bots |
| `MAX_CONTENT_LENGTH` | memory-exhaustion via huge bodies |
| Server-side validation | anything a browser would have blocked |
| POST/Redirect/GET | duplicate submissions from a refresh |

```
04_forms_and_request_handling/
├── app.py
├── templates/
│   ├── base.html      # flash-message rendering
│   ├── form.html      # sticky fields, per-field errors, honeypot, CSRF
│   ├── thanks.html    # the "G" in POST/Redirect/GET
│   └── leads.html     # HTML or JSON via content negotiation
└── static/css/style.css
```

## 3. Run it

```bash
source .venv/bin/activate
flask --app 04_forms_and_request_handling/app.py run --port 5004 --debug
```

Open <http://127.0.0.1:5004/>.

## 4. Try it — learn by doing

### In the browser

1. Submit the form **empty**. Every error appears at once, not one at a time.
2. Fill in only the company, submit, and note the company field **keeps** your
   text. That is a *sticky field* — without it, one typo wipes the form and the
   user leaves.
3. Submit a valid form. You land on `/thanks`. Press <kbd>F5</kbd>: **no**
   "confirm resubmission" dialog and no duplicate lead. That is POST/Redirect/GET.
4. Open DevTools → Network, submit again, and read the `303` response's
   `Location` header.

### From the command line — where the real lesson is

```bash
# 1. The browser's `required` attribute means nothing to curl.
#    The SERVER still rejects it — with 422, not 200.
curl -i -X POST http://127.0.0.1:5004/ -d "name=&email=nope"

# 2. No CSRF token -> 400, before any validation runs.
curl -i -X POST http://127.0.0.1:5004/ \
     -d "name=Ada&email=ada@example.com&company=Acme&team_size=6-20"

# 3. team_size is validated against an allow-list, so a value that was never
#    in the <select> is rejected.
curl -i -X POST http://127.0.0.1:5004/ -d "team_size=9999"

# 4. Bodies over 64 KB are rejected with 413 before your view function runs.
curl -i -X POST http://127.0.0.1:5004/ --data "x=$(python -c 'print("y"*70000)')"

# 5. Same URL, two representations.
curl -s http://127.0.0.1:5004/leads | head -20
curl -s -H "Accept: application/json" http://127.0.0.1:5004/leads

# 6. Where does my data actually land? Compare these three.
curl -s "http://127.0.0.1:5004/inspect?q=1&q=2"        | python -m json.tool
curl -s -X POST http://127.0.0.1:5004/inspect -d "a=1" | python -m json.tool
curl -s -X POST http://127.0.0.1:5004/inspect \
     -H "Content-Type: application/json" -d '{"a":1}'  | python -m json.tool
```

Experiment 6 cures the most common beginner bug: **POSTing JSON and reading
`request.form`**, which is always empty. They are different parsers for
different content types.

## 5. The `request` object — a map

| Attribute | Contains | Typical source |
|---|---|---|
| `request.method` | `"GET"`, `"POST"`, … | the request line |
| `request.args` | query string | `?q=flask` |
| `request.form` | form body | `<form method="post">` |
| `request.get_json(silent=True)` | parsed JSON body | `Content-Type: application/json` |
| `request.files` | uploads (Day 16) | `enctype="multipart/form-data"` |
| `request.headers` | HTTP headers | the client |
| `request.cookies` | cookies (Day 06) | the browser |
| `request.remote_addr` | client IP | the socket (or proxy — see Day 20) |

**Always use `.get(key, default)`.** `request.form["name"]` raises a `400 Bad
Request` when the key is missing, which is exactly what a probing client will
trigger.

## 6. POST/Redirect/GET

```
   BAD                                  GOOD
   POST /  ──▶ 200 HTML                 POST /  ──▶ 303 See Other
   user presses F5                                   │  Location: /thanks
   POST /  ──▶ duplicate lead ❌         GET /thanks ──▶ 200 HTML
                                        user presses F5
                                        GET /thanks ──▶ 200 HTML ✅
```

Use **303 See Other**, not the default 302: 303 tells every client
unambiguously to follow up with a GET.

## 7. Status codes that matter here

| Code | Meaning | When this app returns it |
|---|---|---|
| `200` | OK | rendering the form |
| `303` | See Other | successful POST → redirect |
| `400` | Bad Request | missing/invalid CSRF token |
| `413` | Content Too Large | body over `MAX_CONTENT_LENGTH` |
| `422` | Unprocessable Content | well-formed request, invalid field values |

Returning `200` on a validation failure is a real bug: your tests pass, your
monitoring stays green, and API clients believe they succeeded.

## 8. CSRF in three sentences

1. You are logged into `bank.example`; its session cookie sits in your browser.
2. You visit `evil.example`, whose page auto-submits a hidden form to
   `bank.example/transfer` — and your browser attaches your cookie automatically.
3. The fix: put an unguessable token in the **session** *and* in a hidden form
   field. `evil.example` cannot read your session, so it cannot forge the field.

Compare with `secrets.compare_digest`, never `==`: a plain comparison returns
early at the first differing byte, and that timing difference is measurable.

> Day 05 replaces this hand-rolled version with Flask-WTF's `CSRFProtect`, which
> does the same thing app-wide with one line. Now you know what it is doing.

## 9. Best practices introduced today

| Practice | Reason |
|---|---|
| Validate on the server, always | client-side validation is UX, not security |
| Return **all** field errors at once | one error per round-trip loses users |
| Sticky fields on re-render | a wiped form is an abandoned form |
| Allow-lists, not deny-lists | you cannot enumerate everything that is invalid |
| POST/Redirect/GET with **303** | refresh can never double-submit |
| Set `MAX_CONTENT_LENGTH` | bounded memory per request |
| `secrets.compare_digest` for secrets | constant-time, no timing leak |
| Keep validators side-effect free | trivially unit-testable (Day 17) |
| Normalise on input (`.strip()`, `.lower()` emails) | `A@X.com` and `a@x.com` are one person |
| `aria-invalid` / `aria-describedby` | screen readers announce the error |
| Honeypot before validation | cheap bot filter, zero user friction |
| `SECRET_KEY` from the environment | Day 18 — never hardcode it |

## 10. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: The session is unavailable... secret key` | no `SECRET_KEY` | set `app.config["SECRET_KEY"]` |
| `request.form` is empty | client sent JSON | use `request.get_json()` |
| `400 Bad Request` on a missing field | used `request.form["x"]` | `request.form.get("x", "")` |
| Refresh creates duplicates | rendered HTML from the POST | redirect instead |
| Form clears on every error | no `value="{{ data.x }}"` | make fields sticky |
| `<textarea>` never keeps its value | used a `value` attribute | put the text **between** the tags |
| Everyone logged out after restart | `SECRET_KEY` regenerated | load it from the environment |
| Validation passes in the browser, fails in prod | only client-side checks existed | mirror every rule in Python |

## 11. Exercises

1. Add a `phone` field: optional, but if present must be 10–15 digits after
   stripping spaces and `+`.
2. Reject free-mail domains (`gmail.com`, `yahoo.com`) with the message
   "Please use your work email."
3. Add rudimentary rate limiting: refuse more than 3 submissions per session.
   (Day 19 does this properly with Flask-Limiter.)
4. Make `/leads` return `404` for JSON clients when there are no leads, while
   still rendering the empty state for browsers.
5. Write `test_validate_lead()` covering every branch of `validate_lead`. It
   needs no app, no client, no database — that is the payoff for keeping
   validators side-effect free.
6. Remove `novalidate` from the `<form>` tag and observe how the browser now
   blocks submission *before* the request is sent. Then defeat it with `curl`.

## 12. What's next

**[Day 05 — Flask-WTF and Validation →](../05_flask_wtf_and_validation/)**
Everything you wrote by hand today, declared in ~15 lines: form classes,
built-in and custom validators, and automatic CSRF.
