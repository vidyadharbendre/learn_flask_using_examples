# Day 05 — Flask-WTF and Validation

> **Goal:** replace yesterday's hand-written validation with declarative form
> classes — built-in validators, custom validators, cross-field rules, and
> automatic CSRF.
> **Time:** ~75 minutes · **Port:** 5005 · **Builds on:** Day 04

---

## 1. Why this matters

Day 04's form had five fields and ~40 lines of validation. Today's has
**thirteen**, including dates, numbers, radios and checkboxes. Hand-rolling that
would be ~120 lines of `if` statements that nobody wants to maintain.

The whole POST handler is now:

```python
form = ApplicationForm()
if form.validate_on_submit():
    ...                      # save and redirect
return render_template("apply.html", form=form), 422
```

**But you had to write Day 04 first.** When Flask-WTF returns a mysterious 400,
you now know it is the CSRF token — because you implemented one yourself.

## 2. Day 04 → Day 05, line by line

| Day 04, by hand | Day 05, declared |
|---|---|
| `request.form.get("name","").strip()` | `StringField(filters=[...])` |
| `if not name: errors["name"] = ...` | `DataRequired(message=...)` |
| `if len(name) > 80: ...` | `Length(min=2, max=80)` |
| `if not EMAIL_RE.match(email): ...` | `Email()` |
| `if team_size not in TEAM_SIZES: ...` | `SelectField(choices=...)` — automatic |
| `issue_csrf_token()` / `csrf_is_valid()` | `CSRFProtect(app)` + `form.hidden_tag()` |
| `value="{{ data.name }}"` sticky fields | automatic — the form re-binds itself |
| building an `errors` dict | `field.errors` |

## 3. What you will build

A job-application portal:

```
05_flask_wtf_and_validation/
├── app.py                     # views only — thin, because forms.py does the work
├── forms.py                   # the validation boundary
├── templates/
│   ├── _formhelpers.html      # render_field / render_checkbox / render_radios
│   ├── apply.html             # 13 fields, ~20 lines of markup
│   ├── applications.html      # GET filter form (CSRF deliberately off)
│   ├── csrf_error.html        # friendly "session expired" page
│   └── base.html
└── static/css/style.css
```

> **Why a separate `forms.py`?** Forms are neither routing nor persistence —
> they are the boundary where untrusted input becomes trusted data. Separating
> them keeps views short and lets tests import forms with no request context.

## 4. Run it

```bash
source .venv/bin/activate
flask --app 05_flask_wtf_and_validation/app.py run --port 5005 --debug
```

Open <http://127.0.0.1:5005/>.

## 5. Try it — learn by doing

### In the browser

1. Submit the empty form. Note the **error summary at the top** with jump links,
   *and* per-field messages.
2. Enter `you@gmail.com` → the custom `work_email` validator rejects it.
3. Enter **0** years of experience with everything else valid → **accepted**.
   This is the `InputRequired` vs `DataRequired` distinction (see §7).
4. Enter 1 year and ₹50,00,000 expected → the cross-field rule fires.
5. Pick a past start date → `validate_available_from` rejects it.
6. Leave the consent box unticked → rejected.

### From the command line

```bash
# CSRF is enforced app-wide: no token, no entry.
curl -i -X POST http://127.0.0.1:5005/ -d "full_name=Ada"      # 400

# The <select> allow-list is enforced with no code of your own:
#   "Not a valid choice." comes from SelectField itself.
# (Grab a token from the form first — see the note below.)

# The GET filter form carries no CSRF token at all:
curl -s "http://127.0.0.1:5005/applications?q=ananya" | grep -c csrf_token   # 0
```

To POST successfully from `curl` you need a token *and* the session cookie that
signed it:

```bash
curl -s -c jar.txt http://127.0.0.1:5005/ \
  | grep -o 'name="csrf_token"[^>]*value="[^"]*"'      # copy the value
curl -i -b jar.txt -X POST http://127.0.0.1:5005/ \
  -d "csrf_token=PASTE_IT_HERE" -d "full_name=Ada" ...
```

That the token is useless without the matching cookie **is** the CSRF defence.

## 6. Validator catalogue used here

| Validator | Purpose |
|---|---|
| `DataRequired()` | value must be present **and truthy** |
| `InputRequired()` | the *input* must be present — `0` and `""` count as submitted |
| `Optional()` | empty value → **skip the rest of the chain** |
| `Length(min, max)` | string length |
| `NumberRange(min, max)` | numeric bounds |
| `Email()` | address shape (needs `email-validator`) |
| `Regexp(pattern)` | custom format |
| `AnyOf([...])` | allow-list |
| `EqualTo("other")` | password confirmation (Day 13) |

Plus **three ways to write your own**:

```python
# 1. Reusable function — attach to any field on any form
def work_email(form, field):
    if field.data.rsplit("@", 1)[1].lower() in BLOCKED:
        raise ValidationError("Please use your work email.")

# 2. Inline method — cross-field rules, auto-called for `expected_salary`
class ApplicationForm(FlaskForm):
    def validate_expected_salary(self, field):
        if self.years_experience.data < 2 and field.data > 2_000_000:
            raise ValidationError("…")

# 3. Filters — normalise BEFORE validating
email = EmailField(filters=[lambda v: v.strip().lower() if v else v])
```

## 7. Two traps that cost everyone an afternoon

### `DataRequired` treats `0` as missing

`DataRequired` checks *truthiness*. For `years_experience`, `0` is a perfectly
valid answer that `DataRequired` would reject with "this field is required".

```python
years_experience = IntegerField(validators=[InputRequired()])   # ✅
years_experience = IntegerField(validators=[DataRequired()])    # ❌ rejects 0
```

The same applies to checkboxes and any field where `0`, `False` or `""` are
legitimate values.

### `Optional()` must come **first**

```python
phone = StringField(validators=[Optional(), Regexp(r"^\+?[0-9\s-]{10,15}$")])  # ✅
phone = StringField(validators=[Regexp(r"^\+?[0-9\s-]{10,15}$"), Optional()])  # ❌
```

`Optional()` short-circuits the chain when the field is empty. Put it second and
an untouched optional field fails its own format check.

## 8. Rendering: one macro, every field type

```jinja
{% from '_formhelpers.html' import render_field %}
{{ form.hidden_tag() }}        {# CSRF + all hidden fields — never omit #}
{{ render_field(form.email) }}
```

`field()` renders the correct widget for the field's type. Useful attributes:

| Expression | Gives you |
|---|---|
| `field.label` | `<label for="…">` with the right `for` |
| `field()` | the widget; kwargs become HTML attributes (`class_` → `class`) |
| `field.errors` | list of messages (empty before validation) |
| `field.id` / `field.name` | DOM id / form key |
| `form.errors` | `{field_name: [messages]}` for a summary block |

A `RadioField` is **iterable** — loop it to control the markup per choice.

## 9. CSRF: `CSRFProtect` vs per-form

A `FlaskForm` validates its own token. `CSRFProtect(app)` goes further and
rejects **every** unsafe request (POST/PUT/PATCH/DELETE) without a valid token —
including routes that use no form at all. That closes the gap where a teammate
adds a POST endpoint and forgets protection.

Opt out only where it is correct to:

```python
class SearchForm(FlaskForm):
    class Meta:
        csrf = False          # GET filter — changes nothing
```

Also set `WTF_CSRF_TIME_LIMIT`. The default is one hour; a long application form
left open over lunch will otherwise fail, and "CSRF token expired" is a genuine
support ticket. Catch `CSRFError` and render a human explanation.

## 10. Best practices introduced today

| Practice | Reason |
|---|---|
| Forms in `forms.py` | validation is its own layer; importable from tests |
| `validate_on_submit()` | POST-check + validation + CSRF in one call |
| `InputRequired` where `0`/`False` are valid | avoids the falsy-value trap |
| `Optional()` first in the chain | empty optional fields must not fail |
| `filters=[...]` to normalise | one canonical form of an email in your data |
| `SelectField(choices=…)` as the allow-list | no hand-written membership check |
| `validate_<field>` for cross-field rules | `self` gives you the whole form |
| Guard against `None` in inline validators | a failed sibling field yields `None`, not a value |
| `CSRFProtect(app)` app-wide | protection cannot be forgotten on a new route |
| `Meta.csrf = False` for GET filters | tokens do not belong in query strings |
| One `render_field` macro | markup fixes apply everywhere at once |
| Error summary + per-field errors | long forms are unusable without both |
| Return **422** on invalid | honest status code for tests and API clients |

## 11. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| Every POST returns 400 | `form.hidden_tag()` missing from the template | add it inside `<form>` |
| `RuntimeError: A secret key is required` | no `SECRET_KEY` | set it (Day 18: from env) |
| `0` rejected as "required" | `DataRequired` on a numeric field | use `InputRequired` |
| Empty optional field fails a format rule | `Optional()` not first | move it to the front |
| `validate_on_submit()` always `False` | it was a GET, or validation failed silently | inspect `form.errors` |
| `Email()` raises at import | `email-validator` not installed | `pip install email-validator` |
| Unticked checkbox "missing" | absent checkboxes are simply not submitted | that is correct — use `InputRequired` if it must be ticked |
| `TypeError` inside a cross-field validator | sibling field failed, so `.data` is `None` | guard with `if x is None: return` |
| Filter form 400s on GET | CSRF enabled on a GET form | `Meta.csrf = False` |

## 12. Exercises

1. Add a `FileField` for a CV, accepting only `.pdf` under 2 MB, using
   `flask_wtf.file.FileAllowed` and `FileSize`. (Day 16 covers saving it.)
2. Add `notice_period` (weeks) and a rule that a candidate available *today*
   must have a notice period of `0`.
3. Extract `work_email` into `validators.py` and reuse it on a second form.
4. Add a `password` / `confirm_password` pair with `EqualTo("password")`.
5. Sort the applications table by expected salary via a `SelectField` on the
   GET form.
6. Write pytest cases for `ApplicationForm` that construct it directly with a
   dict — no HTTP client needed. This is why `forms.py` is separate.

## 13. What's next

**[Day 06 — Sessions, Cookies and Flash →](../06_sessions_cookies_and_flash/)**
How the signed session cookie you have been relying on since Day 04 actually
works — and a shopping cart built on it.
