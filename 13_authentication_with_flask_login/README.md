# Day 13 — Authentication with Flask-Login

> **Goal:** sign users in safely — password hashing, sessions, and the
> difference between *"who are you?"* and *"what is yours?"*
> **Time:** ~2 hours · **Port:** 5013 · **Builds on:** Days 06, 10

---

## 1. Why this matters

Authentication is where beginner code most reliably becomes a security
incident. Every view in this example carries a named defence:

| Attack | Defence here |
|---|---|
| Leaked database → cracked passwords | scrypt: salted, deliberately slow |
| Session fixation | `session.clear()` before `login_user()` |
| Open redirect via `?next=` | `is_safe_redirect_url()` |
| User enumeration (timing) | hash a dummy password for unknown emails |
| User enumeration (messages) | one message for every login failure |
| **IDOR** | scope every query by `current_user.id` |
| CSRF logout | logout is `POST`, not a link |
| Privilege escalation | `@admin_required` returning 403 |

## 2. Authentication ≠ authorisation

| Question | Answered by |
|---|---|
| **Who are you?** | `@login_required` |
| **May you do this?** | an ownership or role check |

Conflating these is the most common serious access-control bug in web
applications. `@login_required` on `/notes/<id>` proves the visitor is
*somebody*. Only `note.user_id == current_user.id` proves the note is *theirs*.

## 3. What you will build

```
13_authentication_with_flask_login/
├── wsgi.py
└── portal/
    ├── __init__.py          factory, LoginManager config, CLI
    ├── models.py            User (UserMixin) + password hashing, Note
    ├── forms.py             register / login / change-password
    ├── blueprints/
    │   ├── auth.py          ← the security lessons live here
    │   └── main.py          protected area, @admin_required, IDOR check
    └── templates/  static/
```

## 4. Run it

```bash
source .venv/bin/activate
cd 13_authentication_with_flask_login

FLASK_APP=wsgi.py flask seed        # prints demo logins
FLASK_APP=wsgi.py flask run --port 5013 --debug
```

| Account | Password | Role |
|---|---|---|
| `ana@example.com` | `CorrectHorseBattery1` | member |
| `vik@example.com` | `CorrectHorseBattery2` | member |
| `admin@example.com` | `CorrectHorseBattery3` | admin |

## 5. Try it — learn by doing

### See salting with your own eyes

```bash
FLASK_APP=wsgi.py flask show-hash hunter2
```

```
hash #1: scrypt:32768:8:1$JavLoVVsEFBtohBp$8058060005200e09...
hash #2: scrypt:32768:8:1$4TjpqcMtQNTTxH1W$24759d40a896ba29...

identical? False   <- same password, different hashes
#1 verifies? True
#2 verifies? True
```

Same password, **different hashes** — because each carries its own random salt.
That is what defeats rainbow tables and stops an attacker learning that two
accounts share a password.

### Attack your own app

**1. IDOR.** Sign in as `ana`, open a note, note its id. Sign in as `vik` in a
private window and request the same `/notes/<id>`.

```
ana reading her note 1:      200
vik reading ana's note 1:    404      ← ownership check, not just login
```

It returns **404, not 403** — 403 would confirm the note exists and belongs to
someone else.

**2. Open redirect.**

```bash
# The evil target is discarded; you land on /dashboard
http://127.0.0.1:5013/auth/login?next=https://evil.example/phish

# A relative path is honoured
http://127.0.0.1:5013/auth/login?next=/notes/1
```

**3. Privilege escalation.** As `ana` (a member), visit `/admin` → **403**.
As `admin` → 200.

**4. Logout must be POST.**

```bash
curl -i http://127.0.0.1:5013/auth/logout          # 405 Method Not Allowed
```

**5. Login failures are indistinguishable.**

```bash
# unknown email vs wrong password — identical response, identical timing
curl -s .../auth/login -d "email=nobody@example.com&password=x"
curl -s .../auth/login -d "email=ana@example.com&password=wrong"
```

## 6. Password hashing

```python
password = "hunter2"                          # ❌ catastrophic
password = md5("hunter2")                     # ❌ billions of guesses/second
password = sha256("hunter2")                  # ❌ same problem: too fast
self.password_hash = generate_password_hash(password)   # ✅ scrypt
```

> **You never store a password. You store a verifier.**

MD5 and SHA-256 are *designed to be fast*, which is precisely wrong here. A
password hash must be **deliberately slow and memory-hard** so a leaked database
cannot be brute-forced. Werkzeug defaults to **scrypt**; bcrypt and Argon2 are
the other reasonable choices.

Two details that bite:

- **Size the column generously.** Werkzeug's scrypt hashes are ~160 characters.
  A `VARCHAR(60)` sized for bcrypt silently **truncates** them and every login
  fails with no obvious cause. This example uses `String(255)`.
- **Never `hash(guess) == stored`.** `check_password_hash` compares in constant
  time; `==` returns at the first differing byte, which is measurable (Day 04).

## 7. Session fixation

```python
def _sign_in(user, *, remember):
    session.clear()          # ← discard any pre-login session
    login_user(user, remember=remember)
```

An attacker who can set a victim's session cookie **before** they log in — via
XSS, a shared machine, or a crafted link — would otherwise still hold a valid
cookie *after* the victim authenticates, and be logged in as them.

Rotating the session makes the planted identifier worthless. Do it on **every
privilege change**: login, and any step-up such as entering an admin area.

## 8. Open redirect — the `?next=` trap

```python
next_url = request.args.get("next")
if not is_safe_redirect_url(next_url):
    next_url = url_for("main.dashboard")
return redirect(next_url, code=303)
```

Without the check:

```
/auth/login?next=https://evil.example/fake-login
```

The user signs in on your **genuine** site, then lands on a pixel-perfect clone
asking them to "confirm" their password. The phishing link points at *your*
domain, so it survives every filter and every wary user.

The validator rejects anything with a scheme or host, protocol-relative
`//evil.com` (browsers treat it as absolute — easy to miss), and backslashes:

| Target | Safe? |
|---|---|
| `/dashboard`, `/notes/1?x=1` | ✅ |
| `https://evil.example/x`, `http://evil.example` | ❌ |
| `//evil.example` | ❌ |
| `/\evil.example`, `javascript:alert(1)` | ❌ |

**Rule: only ever redirect to a relative path on your own host.**

## 9. User enumeration — two doors

### Through timing

```python
if user is None:
    check_password_hash(_DUMMY_HASH, password)   # burn the same CPU
    return None
```

Without this, a request for a **non-existent** account returns much faster,
because only the real-account path performs an expensive scrypt hash. That
difference is measurable over a few hundred requests and turns your login form
into an **enumeration oracle**.

### Through messages

```python
flash("Invalid email or password.", "error")     # ✅ one message for everything
flash("No account with that email.")             # ❌ confirms the address
flash("That email is already registered.")       # ❌ same leak, registration form
```

Log the specific reason server-side (Day 18), where only you can read it.

> For registration, the friendly production answer is to accept silently and
> **email** the address: a genuine owner gets a welcome or a "you already have
> an account" note, and an attacker learns nothing from the web response.

## 10. IDOR — scope the query, not the display

```python
# ✅ ownership is part of the QUERY
note = db.session.get(Note, note_id)
if note is None or note.user_id != current_user.id:
    abort(404)

# ❌ every user's notes fetched, then "hidden" in the template
notes = db.session.execute(select(Note)).scalars().all()
```

**Insecure Direct Object Reference** is what you have when `@login_required` is
your only check: sign in as anybody, walk `/notes/1`, `/notes/2`, … and read
everyone's data. It is consistently among the most common serious
vulnerabilities found in real applications.

## 11. Flask-Login essentials

```python
login_manager.login_view = "auth.login"        # where anonymous users go
login_manager.session_protection = "strong"    # drop session if IP/UA changes

@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))  # ← the id is a STRING
```

| Piece | Notes |
|---|---|
| `UserMixin` | supplies `is_authenticated`, `is_active`, `get_id()` |
| `user_loader` | runs on **every** request — keep it a PK lookup |
| `current_user` | injected into every template; `AnonymousUserMixin` when signed out |
| `login_required` | redirects with `?next=` automatically |
| `session_protection="strong"` | raises the cost of a stolen cookie, but signs out mobile users who switch networks |

Two traps:

- **`int(user_id)`.** The cookie stores text. Forgetting the conversion is a
  classic cause of "my user is always `None`".
- **Decorator order.** `@login_required` must come **above** `@admin_required`,
  or an anonymous visitor reaches the role check and
  `current_user.is_admin` raises `AttributeError` — a 500 instead of a redirect.

## 12. Best practices introduced today

| Practice | Reason |
|---|---|
| scrypt/bcrypt/Argon2, never MD5/SHA | fast hashes are crackable at scale |
| Generous `password_hash` column | truncation breaks every login silently |
| `session.clear()` before `login_user` | defeats session fixation |
| Validate `?next=` against a relative-path rule | prevents phishing via your own domain |
| One login error message | no enumeration through responses |
| Dummy hash for unknown users | no enumeration through timing |
| Scope queries by `current_user.id` | prevents IDOR |
| 404 (not 403) for someone else's record | reveals nothing about existence |
| 403 (not 404) for a role failure | authenticated but not permitted |
| Logout is `POST` | a GET logout is CSRF-able and prefetchable |
| Require the current password to change it | protects an unattended browser |
| `is_active` honoured by `login_user` | suspension is one column |
| Bounded `REMEMBER_COOKIE_DURATION` | "forever" means a stolen laptop is forever |
| `HttpOnly` + `SameSite` on both cookies | remember-me is a credential too |
| Never serialise `password_hash` | it can be cracked offline |
| `autocomplete="new-password"` | password managers generate stronger secrets |
| No password rules on the **login** form | leaks policy, and the hash decides anyway |
| 401 JSON for API paths | clients need a code, not an HTML login page |

## 13. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `current_user` always anonymous | `user_loader` returned `None` | `int(user_id)`; check the id type |
| Login "succeeds" then user is anonymous | `SECRET_KEY` changed, or cookies blocked | stable key (Day 18) |
| Every login fails after a while | hash column too short | `String(255)` |
| 500 on a role-protected page | `@admin_required` above `@login_required` | swap the order |
| `View function mapping is overwriting` | decorator without `@wraps` | add `@wraps` |
| Users log out at random | `session_protection="strong"` + changing IP | use `"basic"`, or accept it |
| Users read each other's data | no ownership check | scope the query |
| Phishing via your login page | unvalidated `?next=` | validate it |
| Attacker knows which emails exist | distinct messages or timings | uniform both |
| Everyone logged out on deploy | regenerated `SECRET_KEY` | load from env |
| Password reset does not evict an attacker | other sessions still valid | per-user session token (exercise 3) |

## 14. Exercises

1. Add rate limiting to `/auth/login` — 5 attempts per IP per 15 minutes.
   (Day 19 does this with Flask-Limiter.) Explain why this matters *more* than
   password complexity rules.
2. Add email verification: register inactive, email a signed token
   (`itsdangerous`), activate on click.
3. Add `session_token` to `User`, include it in the session, and check it in
   `user_loader`. Rotating it on password change is what makes "sign out
   everywhere" actually work.
4. Add a password-reset flow with a **time-limited, single-use** token. Why must
   the reset page not reveal whether the email exists?
5. Add a `last_seen` column updated in a `before_request`, and consider the
   write amplification that creates.
6. Check new passwords against the Have I Been Pwned k-anonymity API. Note that
   only the first 5 characters of the SHA-1 hash leave your server.
7. Add TOTP two-factor authentication with `pyotp`. Remember to rotate the
   session again after the second factor.

## 15. What's next

**[Day 14 — Week 2 Project: Task Manager →](../14_project_task_manager/)**
Blueprints, database, migrations, auth, an API and tests — assembled into one
application.
