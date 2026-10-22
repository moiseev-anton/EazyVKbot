"""VK presentation: conservative keyboard sizes and plain text pages."""
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from vkbot.dto import DateSpanDTO, GroupDTO

PAGE_SIZE = 6
TEXT_LIMIT = 3500


def schedule_format_data(text):
    """VK formatting offsets count UTF-16 code units, including emoji pairs."""
    items = []
    for match in re.finditer(r"^▰▰▰▰ ((?:ПН|ВТ|СР|ЧТ|ПТ|СБ|ВС) \d{2}\.\d{2}\.\d{4}) ▰▰▰▰$", text, re.MULTILINE):
        items.append({"type": "bold",
                      "offset": len(text[:match.start(1)].encode("utf-16-le")) // 2,
                      "length": len(match.group(1).encode("utf-16-le")) // 2})
    return json.dumps({"version": 1, "items": items})


def button(label, command, **data):
    return {"action": {"type": "callback", "label": label[:40],
                       "payload": json.dumps({"command": command, **data})}}


def link(label, url):
    return {"action": {"type": "open_link", "label": label[:40], "link": url}}


def keyboard(rows, revision=None):
    if len(rows) > 6 or sum(len(row) for row in rows) > 10 or any(len(row) > 5 for row in rows):
        raise ValueError("Inline keyboard exceeds configured VK limits")
    for row in rows:
        for item in row:
            action = item["action"]
            if revision and action["type"] == "callback":
                payload = json.loads(action["payload"])
                payload["revision"] = revision
                action["payload"] = json.dumps(payload)
    return json.dumps({"inline": True, "one_time": False, "buttons": rows}, ensure_ascii=False)


def persistent_keyboard(site_url=None):
    def text(label, command):
        return {"action": {"type": "text", "label": label,
                           "payload": json.dumps({"command": command})}}
    rows = [
        [text("🏠 Главная", "main")],
        [text("🔐 Начать авторизацию", "auth_start")],
    ]
    if site_url:
        rows.append([link("🌍 Источник", site_url)])
    return json.dumps({"inline": False, "one_time": False, "buttons": rows}, ensure_ascii=False)


def paginate(items, page):
    # A single page has room for 8 choices + Back/Home; long lists reserve
    # another two buttons for paging.
    page_size = 8 if len(items) <= 8 else PAGE_SIZE
    pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(0, min(int(page), pages - 1))
    return items[page * page_size:(page + 1) * page_size], page, pages


def date_span(mode, shift, timezone):
    days, back, forward = {"1day": (1, 180, 6), "3days": (3, 60, 1), "week": (7, 26, 1)}[mode]
    shift = max(-back, min(int(shift), forward))
    today = datetime.now(ZoneInfo(timezone)).date()
    start = today - timedelta(days=today.weekday()) if mode == "week" else today
    start += timedelta(days=days * shift)
    return DateSpanDTO(start=start, end=start + timedelta(days=days - 1)), shift, back, forward


def schedule_pages(target, lessons, span):
    weekdays = ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС")
    title = f"🗓 {target.display_name}"
    body_limit = TEXT_LIMIT - len(title) - 2
    blocks = []
    current = span.start
    while current <= span.end:
        heading = f"▰▰▰▰ {weekdays[current.weekday()]} {current:%d.%m.%Y} ▰▰▰▰"
        daily = sorted((l for l in lessons if l.date == current.isoformat()),
                       key=lambda l: (l.number, l.part or 0))
        if not daily:
            blocks.append((heading, "Занятий нет"))
        for lesson in daily:
            start = lesson.startTime.strftime("%H:%M") if lesson.startTime else "--:--"
            end = lesson.endTime.strftime("%H:%M") if lesson.endTime else "--:--"
            part = f" / {lesson.part}" if lesson.part else ""
            subject = lesson.subject + (f" ({lesson.annotation})" if lesson.annotation else "")
            number = "🔟" if lesson.number == 10 else "".join(
                digit + "\ufe0f\u20e3" if digit.isdecimal() else digit for digit in str(lesson.number)
            )
            lines = [f"{number}{part} {start}–{end} 📍 {lesson.classroom or '—'}", subject]
            if lesson.subgroup and lesson.subgroup != "0":
                lines.append(f"{lesson.subgroup} подгруппа")
            if isinstance(target, GroupDTO) and lesson.teacher:
                lines.append(lesson.teacher.short_name)
            elif not isinstance(target, GroupDTO) and lesson.group:
                lines.append(lesson.group.title)
            blocks.append((heading, "\n".join(lines)))
        current += timedelta(days=1)
    pages, text, current_heading = [], "", None
    for heading, block in blocks:
        # Pathological long values are split too, without dropping lesson data.
        chunk_limit = body_limit - len(heading) - 2
        for offset in range(0, len(block), chunk_limit):
            chunk = block[offset:offset + chunk_limit]
            prefix = heading + "\n\n" if current_heading != heading else ""
            if text and len(text) + len(prefix) + len(chunk) + 2 > body_limit:
                pages.append(text)
                text = ""
                prefix = heading + "\n\n"
            text += ("\n\n" if text else "") + prefix + chunk
            current_heading = heading
    if text:
        pages.append(text)
    # Keep the schedule title and day context even on continuation pages.
    return [title + "\n\n" + page for page in pages] or [title]
