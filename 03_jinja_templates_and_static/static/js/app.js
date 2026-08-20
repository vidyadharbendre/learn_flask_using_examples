/* Day 03 — static/js/app.js
   Loaded from base.html with `defer` so it runs after HTML parsing.
   Kept deliberately tiny: this course teaches Flask, not front-end frameworks.
   In production, a reverse proxy or CDN serves this file, not Python. */
document.addEventListener("DOMContentLoaded", () => {
  const popular = document.querySelector(".plan-popular h3");
  if (popular) console.log(`Most popular plan: ${popular.textContent.trim()}`);
});
