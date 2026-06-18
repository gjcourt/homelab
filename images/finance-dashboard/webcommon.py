#!/usr/bin/env python3
"""Shared rendering layer for the finance-dashboard static site.

Every page renderer imports `page()` to get the consistent shell — shared
<head> (links style.css), the top nav, and footer — so the four pages
(balance sheet / cash flow / real estate / runway) look and navigate the same.
Common formatting helpers (usd/pct) live here too.
"""
from __future__ import annotations

import html

# (href, label) — the order of the top nav across every page.
NAV = [
    ("index.html", "Balance Sheet"),
    ("cashflow.html", "Cash Flow"),
    ("runway.html", "Runway"),
    ("realestate.html", "Real Estate"),
]


def usd(x) -> str:
    try:
        return f"${x:,.0f}"
    except (TypeError, ValueError):
        return "—"


def pct(x) -> str:
    try:
        return f"{x * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _nav(active: str) -> str:
    items = "".join(
        f'<a href="{href}"{" class=active" if href == active else ""}>{label}</a>'
        for href, label in NAV
    )
    return f'<nav class=topnav>{items}</nav>'


def page(title: str, active: str, body: str, head_extra: str = "") -> str:
    """Wrap a body fragment in the full HTML document with shared chrome.

    active = the nav href to highlight (e.g. "runway.html").
    head_extra = extra <head> content (e.g. a <script src> or inline JS).
    """
    return f"""<!doctype html>
<html lang=en><head>
<meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel=stylesheet href=style.css>
{head_extra}
</head><body>
<div class=wrap>
{_nav(active)}
{body}
<div class=foot>finance.burntbytes.com · internal · not financial advice</div>
</div>
</body></html>"""
