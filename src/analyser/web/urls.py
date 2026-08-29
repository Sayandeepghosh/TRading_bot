"""URL helpers shared by the live server and the static export.

The same templates render in two very different places:

  live server   absolute paths off the root      /stock/TITAN
  GitHub Pages  relative paths, .html suffixes   ../stock/TITAN.html

GitHub Pages serves a project site from a subpath (username.github.io/repo/),
so absolute paths break there. Rather than maintaining two sets of templates,
every link goes through these helpers and the mode decides the shape.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UrlMode:
    """How to build links for the current render.

    static: emit relative paths with .html suffixes
    depth:  how many directories deep the page being rendered sits, so relative
            prefixes resolve ("stock/TITAN.html" is depth 1)
    """

    static: bool = False
    depth: int = 0

    @property
    def prefix(self) -> str:
        return "" if not self.static else ("../" * self.depth)

    # ------------------------------------------------------------- assets

    def asset(self, path: str) -> str:
        """CSS, JS, images. `path` is relative to the site root, no leading slash."""
        path = path.lstrip("/")
        return f"{self.prefix}{path}" if self.static else f"/{path}"

    # -------------------------------------------------------------- pages

    def page(self, name: str) -> str:
        """Top-level page. `name` is '', 'holdings' or 'settings'."""
        name = name.strip("/")
        if not self.static:
            return "/" + name
        if not name or name == "index":
            return f"{self.prefix}index.html"
        return f"{self.prefix}{name}.html"

    def stock(self, symbol: str) -> str:
        if not self.static:
            return f"/stock/{symbol}"
        return f"{self.prefix}stock/{_slug(symbol)}.html"

    # ---------------------------------------------------------- endpoints

    def action(self, path: str) -> str:
        """Form POST target. Meaningless in static mode; forms are disabled there."""
        return "" if self.static else "/" + path.lstrip("/")

    def as_globals(self) -> dict:
        """Jinja globals bundle.

        `has_stock_page` matters only for the static build: it caps how many
        per-stock pages get rendered to keep the repo small, so templates must
        not link to a page that was never written. On the live server every
        symbol resolves, so it is always true there.
        """
        return {
            "u": self.asset,
            "page": self.page,
            "stock_url": self.stock,
            "action": self.action,
            "static_mode": self.static,
            "has_stock_page": lambda _sym: True,
            # Plotly is loaded from a CDN. Vendoring it locally would add ~3.5MB
            # to the repo for no functional gain, and Pages serves the CDN fine.
            "plotly_src": "https://cdn.plot.ly/plotly-2.35.2.min.js",
            "stock_ext": ".html" if self.static else "",
            "stock_base": (f"{self.prefix}stock/" if self.static else "/stock/"),
        }


def _slug(symbol: str) -> str:
    """Filesystem-safe filename for a symbol. BAJAJ-AUTO and M&M both work."""
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in symbol.upper())


SERVER = UrlMode(static=False, depth=0)
