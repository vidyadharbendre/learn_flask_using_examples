# Day 06 — Sessions, Cookies and Flash

> **Goal:** understand how a stateless protocol remembers you — signed session
> cookies, raw cookies, the security flags that matter, and why `flash()` shows
> a message exactly once.
> **Time:** ~75 minutes · **Port:** 5006 · **Builds on:** Day 05

---

## 1. Why this matters

**HTTP is stateless.** Every request arrives with no memory of the last one. The
*only* reason your server can tell two requests came from the same person is
that the browser echoes back a cookie.

You have been relying on this since Day 04 — `flash()` needs a session, and CSRF
tokens live in one. Today you look inside.

## 2. What you will build

A bookstore shopping cart:

```
06_sessions_cookies_and_flash/
├── app.py
├── templates/
│   ├── catalogue.html       # POST forms, not links, to change state
│   ├── cart.html            # prices looked up live, never trusted from the cookie
│   ├── session_debug.html   # see + decode what your browser stores
│   └── base.html            # flash rendering, theme toggle
└── static/css/style.css     # light theme driven by a raw cookie
```

## 3. Run it

```bash
source .venv/bin/activate
flask --app 06_sessions_cookies_and_flash/app.py run --port 5006 --debug
```

Open <http://127.0.0.1:5006/>.

## 4. Try it — learn by doing

### The exercise that changes how you think

1. Add two books to your cart.
2. Go to **Session debug** and copy the value of the `session` cookie.
3. Run this — and read your own cart back **in plain text**:

```bash
python - <<'PY'
import base64, zlib
raw = "PASTE_THE_SESSION_COOKIE_HERE"
# Format: <payload>.<timestamp>.<signature>
# A LEADING dot means the payload is zlib-compressed, so check `raw` first.
if raw.startswith("."):
    payload, decode = raw[1:].split(".")[0], zlib.decompress
else:
    payload, decode = raw.split(".")[0], (lambda b: b)
padded = payload + "=" * (-len(payload) % 4)
print(decode(base64.urlsafe_b64decode(padded)))
PY
```

You will see something like:

```
b'{"_flashes":[...],"cart":{"flask-101":1,"sql-deep":1}}'
```

> ### **Signed ≠ encrypted**
> The signature after the last `.` stops you **changing** the value without the
> `SECRET_KEY`. It does **not** stop you **reading** it. Never put a password,
> API key, card number, or unverified `is_admin` flag in a session.

### The rest

```bash
# The cart is per-cookie-jar. Two jars = two independent carts.
curl -s -c a.txt -X POST http://127.0.0.1:5006/cart/add/flask-101 > /dev/null
curl -s -b a.txt http://127.0.0.1:5006/cart | grep -c "Flask in Practice"   # 1
curl -s http://127.0.0.1:5006/cart | grep -c "Flask in Practice"           # 0 — no cookie

# Stock is enforced on the SERVER (py-arch has 3), not by the disabled button.
for i in 1 2 3 4 5; do
  curl -s -b a.txt -c a.txt -X POST http://127.0.0.1:5006/cart/add/py-arch > /dev/null
done
curl -s -b a.txt http://127.0.0.1:5006/session-debug | grep py-arch        # capped at 3

# Inspect the Set-Cookie flags
curl -si -X POST http://127.0.0.1:5006/theme/light | grep -i set-cookie
```

**In the browser**, watch a flash message: it appears once after a redirect, and
**disappears on refresh**. Then open DevTools → Application → Cookies and watch
the `session` cookie change as you add items.

## 5. Session vs cookie — which do I use?

| | `session` | raw cookie |
|---|---|---|
| Signed (tamper-proof) | ✅ | ❌ |
| Readable by the user | ✅ (base64 — not a secret) | ✅ |
| Readable by JavaScript | ❌ (`HttpOnly`) | your choice |
| Good for | cart, user id, CSRF token, flashes | theme, locale, "hide this banner" |
| Bad for | anything secret, anything large | anything that must not be forged |

This app uses **both**: the cart in the session (must not be forged), the theme
in a plain cookie (harmless, and JS may want it).

## 6. The mutation trap — the #1 session bug

```python
session["cart"]["flask-101"] = 2       # ❌ silently does NOT save
```

Flask cannot detect changes *inside* a mutable value it already handed you, so
the session is never marked dirty and no new cookie is sent. The code looks
correct and simply does nothing.

Two fixes:

```python
cart = get_cart(); cart["flask-101"] = 2; session["cart"] = cart   # ✅ reassign
# or
session["cart"]["flask-101"] = 2; session.modified = True          # ✅ force it
```

**Prefer reassignment** — it is explicit, and it is what `save_cart()` does.

## 7. Never store prices in the session

The cart stores `{sku: quantity}` only. Every price is looked up from the
catalogue **on each request**:

```python
lines.append({"book": book, "subtotal": book["price_inr"] * quantity})
```

If you stored `{"sku": "x", "price": 899}` instead, a user with a cookie editor
could buy anything for ₹1. This has happened to real shops.

**The rule: the session holds *what the user chose*, never *what it costs*.**

## 8. Cookie flags that matter

```python
SESSION_COOKIE_HTTPONLY = True    # JS cannot read it — contains XSS damage
SESSION_COOKIE_SECURE   = True    # HTTPS only — MUST be True in production
SESSION_COOKIE_SAMESITE = "Lax"   # browser-level CSRF defence
PERMANENT_SESSION_LIFETIME = timedelta(days=7)
```

| Flag | Attack it blocks |
|---|---|
| `HttpOnly` | XSS stealing the session via `document.cookie` |
| `Secure` | passive network capture over plain HTTP |
| `SameSite=Lax` | the cross-site POST from Day 04's CSRF story |

`SESSION_COOKIE_SECURE=False` in this example **only** so it works on
`http://127.0.0.1`. Day 18 flips it via config; Day 20 enforces it in production.

## 9. Permanent vs browser sessions

```python
session.permanent = False   # default: cookie dies when the browser closes
session.permanent = True    # cookie expires after PERMANENT_SESSION_LIFETIME
```

The lifetime is a **rolling** window — Flask refreshes the expiry on each
request, so an active user is never logged out mid-task. Try the "remember for
7 days" button and watch the cookie's `Expires` attribute appear in DevTools.

## 10. How `flash()` actually works

```python
flash("Added to cart.", "success")        # appends to session["_flashes"]
get_flashed_messages(with_categories=True) # READS AND DELETES that list
```

The read-and-delete is the whole mechanism, and it explains two things:

- **Why a flash survives exactly one redirect** — the POST stores it, the
  redirected GET renders and clears it. Perfect for POST/Redirect/GET (Day 04).
- **Why calling `get_flashed_messages()` twice on one page shows nothing the
  second time.** Call it once, in `base.html`.

Since flashes live in the session, they are **also** in that cookie you decoded.
Do not flash anything you would not put in a session.

## 11. When client-side sessions stop being enough

Flask's default session is stored **entirely in the cookie**. That is fast and
requires no infrastructure, but:

| Limit | Consequence |
|---|---|
| ~4 KB per cookie | large carts silently break |
| Sent on **every** request | wasted bandwidth on every asset |
| Cannot be revoked server-side | "log out all devices" is impossible |
| Contents readable by the user | no secrets, ever |

When you hit any of these, move to **server-side sessions** (Flask-Session with
Redis or a database): the cookie then carries only an opaque session **id**, and
the data lives on your server where it can be revoked. Same `session` API — only
the backend changes.

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| State changes are POST, never GET | crawlers, prefetch and `<img src>` all fire GETs |
| Store identifiers, look up values | a cookie editor must not be able to set a price |
| Reassign instead of mutating in place | in-place changes are not saved |
| `HttpOnly` + `Secure` + `SameSite=Lax` | three distinct attacks, three flags |
| `session.pop(key)` on clear, `session.clear()` on logout | don't wipe unrelated keys |
| Never redirect to `request.referrer` unvalidated | open-redirect vulnerability |
| Validate/clamp on the server (`stock`) | a disabled button is a suggestion |
| Skip unknown SKUs instead of raising | a stale cookie must not 500 the page |
| `request.form.get(..., type=int)` | coerces safely; garbage becomes the default |
| Keep the session small | it rides on every single request |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: The session is unavailable…` | no `SECRET_KEY` | set it |
| Changes to the cart never persist | mutated a nested object | reassign, or `session.modified = True` |
| Everyone logged out after a deploy | `SECRET_KEY` regenerated | load a stable key from env (Day 18) |
| Flash appears twice | `get_flashed_messages()` called in base *and* child | call it once |
| Flash never appears | you rendered instead of redirecting | use POST/Redirect/GET |
| Session empty in production | `SESSION_COOKIE_SECURE=True` on plain HTTP | serve HTTPS |
| `Cookie too large` warnings | storing objects in the session | store ids; go server-side |
| Cart shared between users | data in a module-level global, not the session | put per-user state in `session` |

## 14. Exercises

1. Add a "recently viewed" list (max 5 SKUs, most recent first) in the session.
   Watch what happens if you `append()` without reassigning.
2. Add a promo code: `session["promo"] = "FLASK10"` giving 10% off. Compute the
   discount from the **server's** table, never from the session.
3. Add `/cart/remove/<sku>` as a POST and wire up a per-line "Remove" button.
4. Set `SESSION_COOKIE_SECURE=True` and reload over `http://`. Explain what broke.
5. Add a `banner_dismissed` **raw cookie** (not session) that hides the header
   banner for 30 days.
6. Add 60+ books to the cart and inspect the cookie size. At what point would
   you move to server-side sessions?

## 15. What's next

**[Day 07 — Week 1 Project: Expense Tracker →](../07_project_expense_tracker/)**
Everything from Days 01–06 in one application, with no database yet — the last
day before SQLAlchemy makes persistence real.
