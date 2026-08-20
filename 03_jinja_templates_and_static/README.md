# Day 03 — Jinja Templates and Static Files

> **Goal:** stop repeating yourself in HTML. Macros, includes, custom filters,
> context processors — and a hands-on look at how autoescaping stops XSS.
> **Time:** ~75 minutes · **Port:** 5003 · **Builds on:** Day 02

---

## 1. Why this matters

Catalogue and pricing pages are where template duplication is born. You write
one beautiful card, then paste it three times. Six weeks later a designer
changes the button and you fix it in three places — badly, in two.

Jinja gives you four tools to prevent that, and today you use all four:

| Tool | Answers the question |
|---|---|
| **Macro** | "How do I reuse markup that takes *arguments*?" |
| **Include** | "How do I reuse markup that never varies?" |
| **Filter** | "Where does *formatting* logic live?" |
| **Context processor** | "How do I get a value into *every* template?" |

## 2. What you will build

A pricing page for an analytics SaaS: three plan cards from one macro, a
comparison table, plan detail pages, and a page that demonstrates autoescaping.

```
03_jinja_templates_and_static/
├── app.py
├── templates/
│   ├── base.html            # skeleton, macro-driven nav
│   ├── _macros.html         # plan_card, feature_row, nav_link
│   ├── _flash_banner.html   # an include (no arguments)
│   ├── pricing.html         # macro loop + comparison table
│   ├── plan_detail.html     # macros with defaults, loop.first/index
│   ├── escaping_demo.html   # autoescaping vs |safe, side by side
│   └── 404.html
└── static/
    ├── css/style.css        # note the sub-folders
    └── js/app.js
```

> **Convention:** partials are prefixed with `_` so they read as
> "not a page". Flask does not enforce this; your teammates will thank you.

## 3. Run it

```bash
source .venv/bin/activate
flask --app 03_jinja_templates_and_static/app.py run --port 5003 --debug
```

Open <http://127.0.0.1:5003/>.

## 4. Try it — learn by doing

```bash
curl -s http://127.0.0.1:5003/ | grep -E '₹|seat'      # custom filters at work
curl -i http://127.0.0.1:5003/plans/growth
curl -i http://127.0.0.1:5003/plans/nope               # 404
curl -s http://127.0.0.1:5003/static/css/style.css | head -3
```

Test the filters directly — they are ordinary Python functions, which is
precisely why formatting belongs in a filter:

```bash
python - <<'PY'
import sys; sys.path.insert(0, '03_jinja_templates_and_static')
from app import format_inr, pluralize, highlight
print(format_inr(1499))        # ₹1,499
print(format_inr(2400000))     # ₹24,00,000   <- lakh grouping, not 2,400,000
print(pluralize(1, 'seat'), '|', pluralize(15, 'seat'))
print(highlight("<b>Growth</b>", "growth"))   # escapes first, marks second
PY
```

**The exercise that matters most:** open
<http://127.0.0.1:5003/escaping-demo> and **view page source**. The same
hostile string is rendered five ways. Find the one that actually injected a
`<script>` tag into your DOM, and understand why.

## 5. The four tools, concretely

### Macro — a template function

```jinja
{# _macros.html #}
{% macro plan_card(plan, detail_url) %}
  <li class="card">…</li>
{% endmacro %}

{# pricing.html #}
{% from '_macros.html' import plan_card %}
{% for plan in plans %}
  {{ plan_card(plan, url_for('plan_detail', slug=plan.slug)) }}
{% endfor %}
```

Macros take arguments, support defaults (`{% macro row(label, value, emphasis=false) %}`),
and can be called positionally or by keyword.

### Include — paste a fixed partial

```jinja
{% include '_flash_banner.html' %}
```

`include` shares the *calling* template's context; a macro is isolated and
parameterised. **If it varies, macro. If it never varies, include.**

### Custom filter — formatting logic, out of your views

```python
@app.template_filter("inr")
def format_inr(amount: int | float) -> str:
    ...            # ₹6,999 and ₹24,00,000 (lakh grouping)
```

```jinja
{{ plan.price_inr|inr }}
```

The win is testability: `format_inr` is a pure function you can unit-test
without spinning up a request.

### Context processor — inject into every template

```python
@app.context_processor
def inject_site_globals() -> dict[str, Any]:
    return {"company": …, "current_year": …, "support_email": …}
```

No view passes `company`, yet `base.html` and `_flash_banner.html` both use it.
**Keep these cheap** — they run on every single render, so never query a
database here.

## 6. Autoescaping: the security lesson

| Rendering | Result |
|---|---|
| `{{ hostile }}` | escaped ✅ — this is the **default** |
| `{{ hostile\|e }}` | escaped ✅ (redundant) |
| `{{ hostile\|safe }}` | **injected ❌ — an XSS hole** |
| `{{ trusted }}` where `trusted = Markup(...)` in Python | safe ✅ |
| `{{ hostile\|highlight('bobby') }}` | safe ✅ — escapes *then* marks |

**The rule:** escape untrusted input **first**, add your trusted markup
**second**, mark the result safe **last**. Building a string and appending
`|safe` is exactly how XSS vulnerabilities are written.

Note that autoescaping is keyed on the **file extension**. `.html`, `.htm`,
`.xml` and `.xhtml` are escaped; a template named `report.txt` is **not**.

## 7. Loop variables worth knowing

Inside `{% for %}` Jinja gives you a free `loop` object:

| Expression | Meaning |
|---|---|
| `loop.index` / `loop.index0` | 1-based / 0-based counter |
| `loop.first` / `loop.last` | edge detection without extra variables |
| `loop.length` / `loop.revindex` | total / countdown |
| `loop.cycle('a','b')` | alternate values (zebra striping) |
| `{% else %}` | runs when the sequence is empty |

And filters can replace loops entirely:

```jinja
{{ plans|map(attribute='price_inr')|sum|inr }}   {# ₹33,497 #}
```

## 8. Best practices introduced today

| Practice | Reason |
|---|---|
| Views prepare data, templates present it | never build HTML strings in Python |
| Formatting lives in filters | pure, unit-testable, reusable everywhere |
| One macro per repeated component | fix the bug once, not three times |
| `_`-prefix for partials | signals "not a page" at a glance |
| Cheap context processors | they run on every render |
| Organise `static/` into `css/ js/ img/` | scales past ten files |
| `url_for('static', filename='css/style.css')` | survives CDN and sub-path mounts |
| `defer` on `<script>` | HTML parses before JS runs |
| `|safe` only on `Markup` you produced | the difference between safe and exploited |
| `aria-current="page"` on the active nav link | accessibility is not optional |

## 9. Common mistakes

| Symptom | Cause | Fix |
|---|---|---|
| `'plan_card' is undefined` | forgot `{% from '_macros.html' import plan_card %}` | import in **each** template that calls it |
| Macro can't see a variable | macros are **isolated** from the caller's context | pass it as an argument |
| Filter not found | `@app.template_filter` registered after `render_template` ran, or name typo | register at import time |
| CSS 404 after moving into `css/` | hardcoded `/static/style.css` | `url_for('static', filename='css/style.css')` |
| Context-processor value missing | processor returned `None` instead of a dict | always `return {...}` |
| Page suddenly slow | DB query inside a context processor | move it into the view or cache it |
| User-supplied HTML renders as tags | `|safe` on untrusted data | remove it; escape first, wrap in `Markup` last |

## 10. Exercises

1. Add a `discount(price, percent)` filter and show an annual price with 15% off
   on each card. (`plan_detail.html` already does this inline — move it.)
2. Convert the comparison table rows into a `comparison_row` macro.
3. Add `{% block breadcrumbs %}` to `base.html` and fill it on the detail page.
4. Add a `relative_date` filter that turns a `datetime` into "3 days ago".
5. Create `templates/plans.txt` and render it via `render_template`. Confirm
   that autoescaping is **off** for `.txt` — then explain why that is correct.
6. Move the `<mark>` styling into the macro so `highlight` works on the pricing page too.

## 11. What's next

**[Day 04 — Forms and Request Handling →](../04_forms_and_request_handling/)**
`request.form`, the POST/Redirect/GET pattern, server-side validation, and
manual CSRF — the groundwork before Flask-WTF does it for you on Day 05.
