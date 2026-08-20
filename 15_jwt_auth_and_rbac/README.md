# Day 15 — JWT Auth and Role-Based Access Control

> **Goal:** authenticate non-browser clients with tokens — access/refresh pairs,
> role claims, and the two kinds of revocation a stateless system needs.
> **Time:** ~2 hours · **Port:** 5015 · **Builds on:** Day 13

---

## 1. Why this matters

Day 13's session cookie is the **right** answer for a browser talking to your own
server. It is the **wrong** answer for a mobile app, a partner's API client, a
service-to-service call, or a front-end on a different origin.

| | Session cookie | JWT |
|---|---|---|
| Server state | session store / signed cookie | **none** — self-contained |
| Revocation | delete the session | **hard** (see §6) |
| Sent automatically | yes — which is why CSRF exists | no — the client attaches it |
| Good for | your own browser front end | APIs, mobile, services |

> **A JWT is signed, not encrypted.** Exactly like Day 06's session cookie,
> anyone holding it can read every claim. Put an id and a role in it. Never a
> password, an API key, or personal data you would not print in a log.

## 2. What you will build

A fleet-management API with four roles and one endpoint per privilege level.

```
15_jwt_auth_and_rbac/
├── wsgi.py
└── fleet/
    ├── auth.py      ← issue, validate, revoke; the role_required decorator
    ├── api.py       login / refresh / logout / logout-all + vehicle endpoints
    ├── models.py    User (role, token_version), Vehicle, RevokedToken
    └── extensions.py
```

## 3. Run it

```bash
source .venv/bin/activate
cd 15_jwt_auth_and_rbac

FLASK_APP=wsgi.py flask seed
FLASK_APP=wsgi.py flask run --port 5015 --debug
```

Four accounts, all with the password `FleetPassword123`:

| Email | Role | Can |
|---|---|---|
| `viewer@fleet.test` | viewer | read vehicles |
| `driver@fleet.test` | driver | + update odometer |
| `manager@fleet.test` | manager | + create vehicles |
| `admin@fleet.test` | admin | + delete vehicles, change roles |

## 4. Try it — learn by doing

```bash
API=http://127.0.0.1:5015/api/v1

# 1. Get a token pair
TOKENS=$(curl -s -X POST $API/auth/login -H "Content-Type: application/json" \
  -d '{"email":"driver@fleet.test","password":"FleetPassword123"}')
ACCESS=$(echo $TOKENS | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Use it — note the header, NOT a cookie
curl -s $API/vehicles -H "Authorization: Bearer $ACCESS" | python -m json.tool
curl -s $API/auth/me  -H "Authorization: Bearer $ACCESS" | python -m json.tool

# 3. Without it
curl -s $API/vehicles                                  # 401 authorization_required
curl -s $API/vehicles -H "Authorization: Bearer junk"  # 401 token_invalid
```

### The exercise that changes how you treat tokens

```bash
FLASK_APP=wsgi.py flask decode-token "$ACCESS"
```

```
payload: {'sub': '2', 'role': 'driver', 'name': 'Dev Driver', 'tv': 1,
          'exp': 1787234224, 'jti': 'fe6b46e3-…', 'type': 'access'}

Note: NO key was needed to read that payload.
```

**No secret. No verification. Just base64.** The signature stops you *changing*
a token, not *reading* it.

### The RBAC matrix

Log in as each account and hit each endpoint. You should see exactly this:

| role | `GET /vehicles` | `PATCH …/odometer` | `POST /vehicles` | `DELETE /vehicles/1` |
|---|---|---|---|---|
| viewer | 200 | **403** | **403** | **403** |
| driver | 200 | 200 | **403** | **403** |
| manager | 200 | 200 | 201 | **403** |
| admin | 200 | 200 | 201 | 204 |

**403, not 401.** The caller *is* authenticated — they simply may not do this.
Returning 401 would tell a well-behaved client to fetch a new token, which
would not help.

### Both kinds of revocation

```bash
# One token — the blocklist
curl -s -X POST $API/auth/logout -H "Authorization: Bearer $ACCESS"
curl -s $API/vehicles -H "Authorization: Bearer $ACCESS"   # 401 token_revoked

# Every token for that user — token_version
# (log in twice to simulate two devices, then:)
curl -s -X POST $API/auth/logout-all -H "Authorization: Bearer $DEVICE1"
# BOTH devices now get 401
```

### Refresh, and rotation

```bash
REFRESH=$(echo $TOKENS | python -c "import sys,json; print(json.load(sys.stdin)['refresh_token'])")
curl -s -X POST $API/auth/refresh -H "Authorization: Bearer $REFRESH"   # 200, new pair
curl -s -X POST $API/auth/refresh -H "Authorization: Bearer $REFRESH"   # 401 — rotated!
curl -s -X POST $API/auth/refresh -H "Authorization: Bearer $ACCESS"    # 401 — wrong type
```

## 5. Two tokens, and why

```python
JWT_ACCESS_TOKEN_EXPIRES  = timedelta(minutes=15)
JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=30)
```

| | Access | Refresh |
|---|---|---|
| Sent | on **every** request | only to `/auth/refresh` |
| Exposure | high — logs, proxies, crash reports | low |
| Lifetime | short, to bound the damage | long, for convenience |

A single long-lived access token means one leak grants months of access. That is
the mistake this design exists to prevent.

**Rotation:** `/auth/refresh` revokes the token you presented and issues a new
one. If a stolen refresh token is used, the legitimate user's next refresh
fails — a *detectable signal* rather than silent long-term compromise.

## 6. Revocation: the price of statelessness

A JWT is verified by checking a signature — no lookup, no state. That is what
makes it fast and horizontally scalable, and it is exactly why you cannot
"delete" one.

Two mechanisms, for two different needs:

| | Blocklist | `token_version` |
|---|---|---|
| Scope | **one** token | **all** of a user's tokens |
| Cost | an indexed lookup per request | a PK lookup per request |
| Use for | one logout, one leaked token | password change, "sign out everywhere", compromise, **role change** |
| Growth | needs pruning | never grows |

```python
user.token_version += 1     # every existing token is now stale
```

Each token carries the version it was minted with; a mismatch rejects it. One
integer, no scanning.

> **The blocklist must be pruned.** An expired token is rejected on its own
> merits, so keeping the row buys nothing — and an unbounded blocklist slowly
> degrades a lookup you added to *every authenticated request*. See
> `flask prune-tokens`.

Note what this means honestly: **adding revocation makes your JWT system
stateful again.** You have traded some of the benefit for a necessary feature.
That is a legitimate trade — just make it knowingly.

## 7. Claims: fast authorisation, stale authority

```python
additional_claims = {"role": user.role.value, "tv": user.token_version}
```

Putting the role in the token means authorisation needs **no database query**.
The trade-off is staleness: demote a user and their current access token still
says "manager" until it expires.

That is why `PATCH /users/<id>/role` bumps `token_version`:

```python
user.role = new_role
user.token_version += 1     # tokens carrying the OLD role die immediately
```

**A privilege change must invalidate outstanding tokens**, or the demotion is
merely advisory for the next fifteen minutes. There is a demonstration of this
in §4 — the old token gets `401 token_revoked` and the user must sign in again.

## 8. `role_required`

```python
@api_bp.post("/vehicles")
@role_required(Role.MANAGER)
def create_vehicle(): ...
```

```python
def role_required(minimum: Role):
    def decorator(view):
        @wraps(view)                       # ← or every view registers as "wrapper"
        def wrapper(*args, **kwargs):
            verify_jwt_in_request()        # ← does @jwt_required's job too
            role = Role(get_jwt().get("role", "viewer"))
            if not role.at_least(minimum):
                return jsonify(error={"code": "insufficient_role", ...}), 403
            return view(*args, **kwargs)
        return wrapper
    return decorator
```

Calling `verify_jwt_in_request()` inside the decorator means **one** decorator
does both jobs, so the Day 13 ordering trap cannot happen.

> **Hierarchies are not always right.** A rank order works when privileges
> genuinely nest — an admin really can do everything a driver can. When they do
> not (an auditor reads finance data an engineer cannot, and vice versa), model
> **permissions as a set** instead. Forcing unrelated duties onto one axis is
> how somebody who needed one report ends up with admin.

## 9. No CSRF here — and why that is not a contradiction

```python
JWT_TOKEN_LOCATION = ["headers"]
```

CSRF exists because browsers attach **cookies** automatically (Day 04). A token
in an `Authorization` header is attached by *your client code*, so a malicious
site cannot cause it to be sent. No cookie, no CSRF.

The moment you store a JWT in a **cookie**, CSRF protection becomes mandatory
again — and Flask-JWT-Extended has `JWT_COOKIE_CSRF_PROTECT` for exactly that.
The vulnerability follows the *transport*, not the token format.

## 10. Best practices introduced today

| Practice | Reason |
|---|---|
| Short access + long refresh | bounds the damage from the token most likely to leak |
| Refresh only at `/auth/refresh` | minimises exposure of the long-lived credential |
| `@jwt_required(refresh=True)` | otherwise the two token types are decorative |
| Rotate refresh tokens | reuse becomes a detectable theft signal |
| Role in a claim | authorisation with no query |
| Bump `token_version` on role change | a demotion must take effect immediately |
| Blocklist for single-token revocation | logout means something |
| **Prune** the blocklist | it is checked on every request |
| 403 for role failures, 401 for token failures | tells the client whether to retry |
| Distinct error codes | clients can refresh automatically instead of guessing |
| `JWT_DECODE_LEEWAY` | a clock 20s fast should not reject valid tokens |
| Fresh tokens for dangerous actions | a stolen refresh token cannot escalate |
| Never put secrets in a token | it is readable by anyone holding it |
| Guard against admin self-demotion | prevents locking everyone out |
| Uniform login failure message | no user enumeration (Day 13) |

## 11. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `422 Subject must be a string` | `identity=user.id` as an int | `identity=str(user.id)` |
| Logout "works" but the token still does | no blocklist callback | enable it (§6) |
| Demoted user keeps privileges | role read from a stale claim | bump `token_version` |
| Tokens rejected on one server only | clock skew | `JWT_DECODE_LEEWAY` |
| Refresh endpoint accepts access tokens | missing `refresh=True` | add it |
| Every view named `wrapper` | decorator without `@wraps` | add `@wraps(view)` |
| Auth gets slower over months | blocklist never pruned | `flask prune-tokens` |
| Client cannot tell expiry from corruption | one generic 401 | distinct `code`s |
| Personal data leaks from tokens | claims treated as private | they are base64 — see §4 |
| CSRF returns after moving to cookies | token moved out of the header | `JWT_COOKIE_CSRF_PROTECT` |
| All tokens invalid after a deploy | `JWT_SECRET_KEY` regenerated | load a stable key from env |

## 12. Exercises

1. Add `@jwt_required(fresh=True)` to a "change password" endpoint and confirm a
   refreshed token is rejected while a login token is accepted.
2. Move the blocklist to **Redis** with a TTL equal to the token lifetime. Note
   that pruning becomes automatic, and the lookup gets much faster.
3. Add a `permissions` list claim and a `permission_required("vehicle:delete")`
   decorator. Compare it with the role hierarchy — which suits this domain?
4. Add rate limiting to `/auth/login` (Day 19), and explain why it matters more
   here than on a session login form.
5. Add `aud` and `iss` claims and verify them. What attack do they prevent when
   one auth server issues tokens for several services?
6. Implement refresh-token *reuse detection*: if a rotated token is presented
   again, revoke the whole family and force a re-login.
7. Switch to asymmetric signing (`RS256`) so services can **verify** tokens
   without holding the key that can **mint** them.

## 13. What's next

**[Day 16 — File Uploads and Media →](../16_file_uploads_and_media/)**
Accepting files from strangers: `secure_filename`, content sniffing, size
limits, thumbnails, and serving uploads without handing out your filesystem.
