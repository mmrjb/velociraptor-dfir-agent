"""Embed the same local font subsets as the GUI in standalone HTML reports."""

import base64
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "web" / "static" / "assets"
NOTICES = Path(__file__).with_name("font_notices.txt")
FONTS = (
    ("Inter Variable", "inter-latin-wght-normal-*.woff2", "U+0000-00FF,U+2000-206F"),
    ("Vazirmatn Variable", "vazirmatn-arabic-wght-normal-*.woff2", "U+0600-06FF,U+200C-200E,U+FB50-FDFF,U+FE70-FEFF"),
    ("JetBrains Mono Variable", "jetbrains-mono-latin-wght-normal-*.woff2", "U+0000-00FF,U+2000-206F"),
)


def embedded_fonts() -> tuple[str, str]:
    css = []
    for family, pattern, unicode_range in FONTS:
        matches = sorted(ASSETS.glob(pattern))
        if not matches:
            continue  # An unbuilt source checkout uses the CSS system-font fallback.
        data = base64.b64encode(matches[0].read_bytes()).decode("ascii")
        css.append(f'@font-face{{font-family:"{family}";font-style:normal;'
                   f'font-weight:100 900;font-display:swap;'
                   f'src:url(data:font/woff2;base64,{data}) format("woff2");'
                   f'unicode-range:{unicode_range}}}')
    return "\n".join(css), NOTICES.read_text(encoding="utf-8") if css else ""
