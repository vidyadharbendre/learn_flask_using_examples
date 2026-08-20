"""
Day 10 — Blueprints package.

A **blueprint** is a self-contained group of routes, templates, static files and
error handlers that gets *registered onto* an application. Think of it as a
module of your site: ``main``, ``products``, ``api``, ``auth``, ``admin``.

Why they matter:

- **Separation.** Each file owns one area; two people can work without conflict.
- **Reuse.** A blueprint can be registered on more than one app, or twice at
  different prefixes.
- **Namespaced endpoints.** ``url_for("products.detail")`` — the prefix is the
  blueprint name, which is why two blueprints can both have an ``index`` view.
- **Deferred registration.** A blueprint records what you asked for; nothing is
  bound until ``app.register_blueprint()`` runs inside the factory.
"""
