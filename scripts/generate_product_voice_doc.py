"""Генерация продуктовой версии voice-document Анны (на «Вы», без мата).

Берёт лекторский data/voice_document/v2_draft.md (написан «от первого лица на ты»,
с матом точечно как усилитель — для подкастов/лекций) и переписывает его для
ПРОДУКТОВОГО контекста: тон спокойный, взрослый, без вульгарностей, на «Вы».

Используется как источник «семантики» для voice_profile `anna_product`
(вместо лекторского — там утечка «ты»-формулировок в продуктовый контент).

Map-Reduce: каждый из 6 разделов перегенерируется отдельным промтом
параллельно (ThreadPool, 3 workers). Sonnet 4.6.

Запуск:
    docker compose run --rm app python scripts/generate_product_voice_doc.py

Output:
    data/voice_document/v2_product_draft.md

Cost: ~$0.20-0.40 (Sonnet sync, 6 коротких генераций).
"""
from __future__ import annotations

import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

MODEL = "claude-sonnet-4-6"
INPUT_PATH = Path("data/voice_document/v2_draft.md")
OUTPUT_PATH = Path("data/voice_document/v2_product_draft.md")
MAX_WORKERS = 3


SYSTEM_PROMPT = """Ты помогаешь подготовить продуктовую версию voice-document психолога Анны.

КОНТЕКСТ: исходный voice-document написан в ЛЕКТОРСКОМ регистре — Анна в лекциях говорит свободно: на «ты», с матом точечно как усилитель, с гротеском и провокацией. Это её сознательный риторический приём в подкастах и эфирах.

ПРОБЛЕМА: тот же voice-document используется как «знание про Анну» для генерации ПРОДУКТОВОГО контента (посты, email-рассылки, лендинги Академии Супружества). Главный сегмент аудитории — «Усталая жена», главный психотип — «Тёрпеливая». Этому сегменту нужен спокойный, взрослый, без пафоса тон. На «Вы». Без мата. Без сектоподобной риторики.

ТВОЯ ЗАДАЧА: переписать данный раздел voice-document для продуктового контекста.

ПРАВИЛА:
1. Тон — спокойный, взрослый, без пафоса. Признаёт сложность реальности.
2. Без мата, без вульгарных формулировок («херня», «нахер», «говно» — убирать или заменять)
3. Без сектоподобной риторики («наш круг», «наши девочки», «девушка-плюс»)
4. Без гарантий результата («100%», «спасу», «точно»)
5. Если в исходнике есть обращение «ты» к слушателю — менять на «Вы»
6. Если в исходнике от первого лица «Я работаю исходя из...» — оставлять как есть (это не обращение к читателю)
7. Сохранять весь СМЫСЛ и фирменные принципы Анны
8. Сохранять СТРУКТУРУ раздела (заголовки, нумерация, формат пунктов)
9. Сохранять метки `[нужно подтвердить с Анной]` где есть
10. Использовать «супружество», не «брак»

ВАЖНО: НЕ выдумывать новые принципы / red lines / техники. Если в исходнике уже подходящий тон — оставь как есть.

Верни ТОЛЬКО переписанный раздел в markdown. Без преамбулы, без приветствий, без послесловий — начинай прямо с заголовка раздела."""


def split_sections(text: str) -> list[tuple[str, str]]:
    """Разбить markdown по `## N. Название` → [(header, content_with_header)].

    Раздел 7 (статичный «что нужно дополнить») возвращаем как есть, не отправляем в LLM.
    """
    pattern = re.compile(r"^## (\d+)\. ", re.MULTILINE)
    matches = list(pattern.finditer(text))
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_text = text[start:end].rstrip() + "\n"
        # Заголовок типа "## 1. Принципы работы"
        header_line = text[start:text.index("\n", start)]
        sections.append((header_line, section_text))
    return sections


def rewrite_section(client: Anthropic, section_text: str, section_header: str) -> tuple[str, str]:
    print(f"  Rewriting: {section_header}…", flush=True)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=6000,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": f"Раздел для перезаписи:\n\n{section_text}",
            }
        ],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("markdown").strip()
    return section_header, text


def main() -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 1
    if not INPUT_PATH.exists():
        print(f"ERROR: {INPUT_PATH} не существует", file=sys.stderr)
        return 1

    text = INPUT_PATH.read_text(encoding="utf-8")
    sections = split_sections(text)
    print(f"Found {len(sections)} sections in {INPUT_PATH}\n", flush=True)

    # Раздел 7 («что нужно дополнить») — статичный, не трогаем.
    sections_to_rewrite = [s for s in sections if not s[0].startswith("## 7.")]
    static_section = next((s[1] for s in sections if s[0].startswith("## 7.")), "")

    print(f"Rewriting {len(sections_to_rewrite)} sections via {MODEL} ({MAX_WORKERS} parallel)…\n", flush=True)
    client = Anthropic(max_retries=8)
    rewritten: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(rewrite_section, client, s_text, s_header): s_header
            for s_header, s_text in sections_to_rewrite
        }
        for fut in as_completed(futures):
            header, new_text = fut.result()
            rewritten[header] = new_text

    # Собираем результат в правильном порядке
    final_parts: list[str] = []
    final_parts.append(
        f"# Voice-document Анны (v2 ПРОДУКТОВЫЙ, черновик)\n\n"
        f"> Продуктовая версия лекторского voice-doc v2. На «Вы», без мата, "
        f"спокойный взрослый тон. Сгенерирована {datetime.utcnow().strftime('%Y-%m-%d')}.\n\n"
        f"> Используется в voice_profile `anna_product` как источник «семантики» "
        f"(принципы / red lines / техники / источники). Лекторская версия "
        f"`v2_draft.md` остаётся для voice_profile `anna_lecture`.\n\n"
        f"> Каждое утверждение, помеченное `[нужно подтвердить с Анной]`, требует её прямого ответа.\n"
    )
    for s_header, _ in sections_to_rewrite:
        if s_header in rewritten:
            final_parts.append(rewritten[s_header])
            final_parts.append("")
    if static_section:
        final_parts.append(static_section.strip())

    final = "\n\n".join(final_parts) + "\n"
    OUTPUT_PATH.write_text(final, encoding="utf-8")
    print(f"\n✓ Saved → {OUTPUT_PATH} ({len(final)} chars)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
