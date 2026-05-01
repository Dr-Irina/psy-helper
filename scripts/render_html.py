"""Конвертация markdown с mermaid-блоками в самодостаточный HTML.

HTML открывается в браузере (двойным кликом или ⌘O в Safari), mermaid-диаграммы
рендерятся через CDN. Дальше браузером ⌘P → Save as PDF.

Запуск:
    python3 scripts/render_html.py docs/architecture.md
    # → создаст docs/architecture.html
"""
from __future__ import annotations

import argparse
import html as htmllib
import json
import sys
from pathlib import Path


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{
    font-family: -apple-system, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
    max-width: 920px; margin: 2em auto; padding: 0 1em;
    line-height: 1.55; color: #222;
  }}
  h1, h2, h3, h4 {{ margin-top: 1.6em; line-height: 1.25; }}
  h1 {{ border-bottom: 2px solid #444; padding-bottom: 0.3em; }}
  h2 {{ border-bottom: 1px solid #aaa; padding-bottom: 0.25em; }}
  code {{ background: #f3f3f3; padding: 0.1em 0.3em; border-radius: 3px;
         font-family: "SF Mono", "Menlo", monospace; font-size: 0.92em; }}
  pre code {{ display: block; padding: 0.8em; overflow-x: auto; }}
  table {{ border-collapse: collapse; margin: 1em 0; }}
  th, td {{ border: 1px solid #ccc; padding: 0.4em 0.8em; text-align: left; }}
  th {{ background: #f5f5f5; }}
  blockquote {{ border-left: 4px solid #aaa; margin: 1em 0; padding: 0.2em 1em;
               color: #555; background: #fafafa; }}
  .mermaid {{ background: white; margin: 1em 0; text-align: center;
             padding: 0.5em; border: 1px solid #eee; border-radius: 6px; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 2em 0; }}
  ul, ol {{ padding-left: 1.6em; }}
  li {{ margin: 0.25em 0; }}
  @media print {{
    body {{ max-width: none; margin: 0; padding: 0.5cm; }}
    h2 {{ page-break-before: auto; }}
  }}
</style>
</head>
<body>
<div id="content">Загрузка…</div>

<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
  mermaid.initialize({{ startOnLoad: false, theme: 'default', securityLevel: 'loose' }});
  window.mermaid = mermaid;
</script>
<script>
  const md = JSON.parse({markdown_json});
  // marked рендерит ```mermaid``` как <pre><code class="language-mermaid">…</code></pre>;
  // меняем такие блоки на <div class="mermaid">…</div>, чтобы mermaid их подхватил.
  let html = marked.parse(md);
  html = html.replace(
    /<pre><code class="language-mermaid">([\\s\\S]*?)<\\/code><\\/pre>/g,
    (_, code) => `<div class="mermaid">${{decodeHtml(code)}}</div>`
  );
  function decodeHtml(s) {{
    const t = document.createElement('textarea');
    t.innerHTML = s;
    return t.value;
  }}
  document.getElementById('content').innerHTML = html;
  // mermaid грузится из ESM-модуля чуть позже — ждём
  function tryRender() {{
    if (window.mermaid) window.mermaid.run();
    else setTimeout(tryRender, 50);
  }}
  tryRender();
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("md_path", type=Path)
    parser.add_argument("--out", type=Path, help="Куда писать .html (по умолчанию рядом)")
    args = parser.parse_args()

    md_text = args.md_path.read_text(encoding="utf-8")
    out_path = args.out or args.md_path.with_suffix(".html")

    title = htmllib.escape(args.md_path.stem)
    md_json = json.dumps(md_text, ensure_ascii=False)
    md_json = json.dumps(md_json)  # ещё раз — чтобы внутри JS было JSON.parse(строка)

    out_path.write_text(
        HTML_TEMPLATE.format(title=title, markdown_json=md_json),
        encoding="utf-8",
    )
    print(f"Готово: {out_path}")
    print()
    print("Открыть в браузере:")
    print(f"  open '{out_path}'")
    print("Сохранить как PDF: в браузере ⌘P → 'Save as PDF'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
