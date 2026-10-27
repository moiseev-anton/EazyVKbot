import asyncio
import json
import logging
import secrets
from uuid import uuid4
from urllib.parse import urljoin, urlencode

from vkbot.context import request_context
from vkbot.dto import AuthDTO
from vkbot.storage import BusyError
from vkbot.ui import button, link, keyboard, persistent_keyboard, paginate, date_span, schedule_pages, schedule_format_data

logger = logging.getLogger(__name__)


def payload_dict(value):
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value or "{}")
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def open_button(label, name, **data):
    return button(label, "open", screen={"name": name, **data})


class Controller:
    def __init__(self, api, services, storage, settings):
        self.api, self.services, self.storage, self.settings = api, services, storage, settings

    async def send(self, peer_id, text, markup=None, *, format_data=None):
        return await self.api.messages.send(
            peer_id=peer_id, random_id=secrets.randbelow(2**31 - 1) + 1,
            message=text, keyboard=markup, dont_parse_links=True,
            **({"format_data": format_data} if format_data is not None else {}),
        )

    async def authenticate(self, user_id, peer_id, nonce=None):
        profile = (await self.api.users.get(user_ids=[user_id], fields=["domain", "photo_200", "sex"]))[0]
        auth = AuthDTO.from_vk(profile, peer_id)
        auth.nonce = nonce
        return await self.services.auth_user(auth)

    async def handle_message(self, message):
        user_id, peer_id = message.from_id, message.peer_id
        if user_id <= 0 or peer_id != user_id:
            return
        token = request_context.set({"user_id": str(user_id), "hmac": False})
        try:
            async with self.storage.lock(user_id), asyncio.timeout(45):
                data = payload_dict(message.payload)
                command = data.get("command") or {
                    "начать": "restart", "/start": "restart", "🔄 начать заново": "restart",
                    "🔐 начать авторизацию": "auth_start",
                    "главная": "main", "🏠 главная": "main", "/main": "main",
                    "расписание": "mine", "🗓 расписание": "mine",
                    "группы": "faculties", "🎓 группы": "faculties",
                    "преподаватели": "alphabet", "👨‍🏫 преподаватели": "alphabet",
                }.get((message.text or "").strip().lower(), "main")
                # Old panels used the same payload as VK's native Start button.
                if command == "start":
                    command = ("auth_start" if (message.text or "").strip().lower()
                               == "🔐 начать авторизацию" else "restart")
                # Link context takes precedence over unrelated text or menu payloads.
                if message.ref:
                    if len(message.ref) > 512:
                        await self.send(peer_id, "Ссылка входа некорректна. Начните вход на сайте заново.", persistent_keyboard(self.settings.base_link))
                        return
                    await self.storage.remember_nonce(user_id, message.ref)
                    if command in ("auth_start", "restart"):
                        await self.finish_auth(user_id, peer_id, message.ref)
                    else:
                        await self.show(user_id, peer_id, {"name": "auth"}, [])
                    return
                if command == "restart":
                    await self.storage.forget_nonce(user_id)
                    await self.authenticate(user_id, peer_id)
                    await self.show(user_id, peer_id, {"name": "main"}, [], install_keyboard=True)
                    return
                if command == "auth_start":
                    nonce = await self.storage.get_nonce(user_id)
                    if nonce:
                        await self.finish_auth(user_id, peer_id, nonce)
                    else:
                        nonce = str(uuid4())
                        account = await self.authenticate(user_id, peer_id, nonce)
                        if account.nonce_status == "authenticated":
                            login_url = "https://eazyclass.ru/schedule?" + urlencode({"nonce": nonce})
                            await self.send(peer_id, "Авторизация прошла успешно, нажмите кнопку для входа",
                                            keyboard([[link("Войти", login_url)]]))
                        else:
                            await self.send(peer_id, "Не удалось подтвердить авторизацию. Нажмите «Начать авторизацию» ещё раз.")
                    return
                await self.authenticate(user_id, peer_id)
                screen_name = command if command in ("mine", "faculties", "alphabet") else "main"
                await self.show(user_id, peer_id, {"name": screen_name}, [], install_keyboard=True)
        except BusyError:
            await self.send(peer_id, "⏳ Предыдущий запрос ещё обрабатывается.")
        except Exception:
            # Standard traceback, without dumping event payloads or local variables.
            logger.exception("Message processing failed")
            await self.send(peer_id, "⚠ Не удалось выполнить запрос. Попробуйте снова или откройте главную.", persistent_keyboard(self.settings.base_link))
        finally:
            request_context.reset(token)

    async def finish_auth(self, user_id, peer_id, nonce):
        account = await self.authenticate(user_id, peer_id, nonce)
        await self.storage.forget_nonce(user_id)
        if account.nonce_status == "authenticated":
            text = "✅ Вход подтверждён. Можно вернуться на сайт."
        else:
            text = "⚠ Вход не подтверждён. Ссылка могла устареть или уже использоваться. Начните вход на сайте заново."
        await self.send(peer_id, text, persistent_keyboard(self.settings.base_link))
        await self.show(user_id, peer_id, {"name": "main"}, [])

    async def handle_callback(self, event):
        obj = event["object"]
        user_id, peer_id = obj["user_id"], obj["peer_id"]
        if user_id <= 0 or user_id != peer_id:
            return
        token = request_context.set({"user_id": str(user_id), "hmac": False})
        try:
            await self.api.messages.send_message_event_answer(
                event_id=obj["event_id"], user_id=user_id, peer_id=peer_id,
            )
            async with self.storage.lock(user_id), asyncio.timeout(45):
                state = await self.storage.get(user_id)
                data = payload_dict(obj.get("payload"))
                if not state or not state.get("revision") or data.get("revision") != state.get("revision"):
                    await self.send(peer_id, "Это меню устарело. Открываю главную.", persistent_keyboard(self.settings.base_link))
                    await self.authenticate(user_id, peer_id)
                    await self.show(user_id, peer_id, {"name": "main"}, [])
                    return
                command = data.get("command")
                screen, history = state["screen"], state.get("history", [])
                if command == "auth_confirm" and screen["name"] == "auth":
                    nonce = await self.storage.get_nonce(user_id)
                    if nonce:
                        await self.finish_auth(user_id, peer_id, nonce)
                    else:
                        await self.send(peer_id, "Ссылка входа устарела. Начните вход на сайте заново.", persistent_keyboard(self.settings.base_link))
                    return
                if command == "auth_cancel" and screen["name"] == "auth":
                    await self.storage.forget_nonce(user_id)
                    await self.authenticate(user_id, peer_id)
                    screen, history = {"name": "main"}, []
                elif command == "open":
                    target = data.get("screen", {})
                    if not isinstance(target, dict):
                        raise ValueError("Invalid screen")
                    if target.get("name") == "main":
                        history = []
                    elif target.get("name") != screen["name"]:
                        history = (history + [screen])[-12:]
                    screen = target
                elif command == "back":
                    screen = history.pop() if history else {"name": "main"}
                elif command == "toggle" and screen["name"] == "settings":
                    kind = data.get("notification_type")
                    snapshot = screen.get("preferences", {}).get(kind)
                    if not snapshot:
                        # Old menus (including pre-migration callbacks) only refresh.
                        await self.show(user_id, peer_id, {"name": "settings"}, history, obj.get("conversation_message_id"))
                        return
                    state["revision"] = None
                    await self.storage.set(user_id, state)
                    await self.services.notifications.set_enabled(
                        str(user_id), kind, not snapshot["enabled"], snapshot["version"])
                elif command == "subscribe" and screen["name"] == "entity":
                    await self.services.refresh()
                    subscriptions = await self.services.subscriptions.get_user_subscriptions()
                    target = self.services.target(screen["kind"], screen["id"])
                    if subscriptions:
                        history = (history + [screen])[-12:]
                        screen = {**screen, "name": "confirm", "expected": sorted(s.id for s in subscriptions)}
                    else:
                        await self.services.subscriptions.create(target)
                        screen, history = {"name": "main"}, []
                elif command == "confirm" and screen["name"] == "confirm":
                    await self.services.refresh()
                    subscriptions = await self.services.subscriptions.get_user_subscriptions()
                    if sorted(s.id for s in subscriptions) != screen["expected"]:
                        await self.send(peer_id, "Подписка изменилась. Выберите расписание заново.")
                        screen, history = {"name": "main"}, []
                    else:
                        # Backend owns atomic replacement, exactly as in Telegram.
                        await self.services.subscriptions.create(self.services.target(screen["kind"], screen["id"]))
                        screen, history = {"name": "main"}, []
                elif command == "unsubscribe" and screen["name"] in ("entity", "settings"):
                    subscriptions = await self.services.subscriptions.get_user_subscriptions()
                    sub_id = int(data["id"])
                    if sub_id not in {s.id for s in subscriptions}:
                        raise ValueError("Subscription no longer belongs to user")
                    await self.services.subscriptions.delete(sub_id)
                await self.show(user_id, peer_id, screen, history, obj.get("conversation_message_id"))
        except BusyError:
            await self.send(peer_id, "⏳ Предыдущий запрос ещё обрабатывается.")
        except Exception:
            logger.exception("Callback processing failed")
            await self.send(peer_id, "⚠ Не удалось выполнить запрос. Попробуйте снова или откройте главную.", persistent_keyboard(self.settings.base_link))
        finally:
            request_context.reset(token)

    async def show(self, user_id, peer_id, screen, history, cmid=None, *, install_keyboard=False):
        text, rows = await self.render(screen)
        format_data = (schedule_format_data(text) if screen.get("name") == "schedule"
                       else json.dumps({"version": 1, "items": []}))
        revision = uuid4().hex[:16]
        markup = keyboard(rows, revision)
        if install_keyboard:
            # VK accepts one keyboard per request. Install the persistent panel on
            # the actual response, then attach inline buttons to that SAME message.
            message_id = await self.send(peer_id, text, persistent_keyboard(self.settings.base_link), format_data=format_data)
            await self.api.messages.edit(peer_id=peer_id, message_id=message_id, message=text, keyboard=markup, format_data=format_data)
        elif cmid:
            try:
                await self.api.messages.edit(peer_id=peer_id, cmid=cmid, message=text, keyboard=markup, format_data=format_data)
            except Exception as exc:
                # Only a known editing restriction warrants a new message.
                if getattr(exc, "code", None) not in (909, 920):
                    raise
                await self.send(peer_id, text, markup, format_data=format_data)
        else:
            await self.send(peer_id, text, markup, format_data=format_data)
        await self.storage.set(user_id, {"screen": screen, "history": history, "revision": revision})

    async def render(self, screen):
        name = screen.get("name")
        rows = []
        home = [button("◀ Назад", "back"), open_button("🏠 Главная", "main")]
        if name == "auth":
            return ("🔐 Подтвердить вход на сайт EazyClass?\nПодтверждайте, только если вы сами начали вход на сайте.",
                    [[button("✅ Подтвердить вход", "auth_confirm")], [button("Отмена", "auth_cancel")]])
        if name in ("main", "settings", "mine"):
            user = await self.services.get_user()
            subs = user.subscriptions or []
            if name == "settings":
                preferences = await self.services.notifications.get_preferences(request_context.get()["user_id"])
                screen["preferences"] = {}
                for kind, title in (("lesson_upcoming", "Предстоящие занятия"),
                                    ("schedule_updated", "Изменения расписания")):
                    preference = preferences[kind]
                    screen["preferences"][kind] = {"enabled": preference.enabled, "version": preference.version}
                    rows.append([button(("🔔 " if preference.enabled else "🔕 ") + title,
                                        "toggle", notification_type=kind)])
                if subs:
                    rows.append([button("✖ Отписаться", "unsubscribe", id=subs[0].id)])
                text = "⚙ Настройки" + (f"\n⭐ Подписка: {subs[0].display_name}" if subs else "\nПодписки нет")
                return text, rows + [home]
            if name == "mine" and subs:
                target = subs[0].object
                screen.clear()
                screen.update(name="schedule", kind=target.relation_name, id=target.id, mode="1day", shift=0)
                return await self.render(screen)
            if subs:
                text = f"⭐ Подписка: {subs[0].display_name}"
                rows.append([open_button("🗓 Расписание", "mine")])
            else:
                text = "Выберите группу или преподавателя, чтобы посмотреть расписание и подписаться."
            rows += [[open_button("🎓 Группы", "faculties"), open_button("👨‍🏫 Преподаватели", "alphabet")],
                     [open_button("⚙ Настройки", "settings")]]
            return text, rows
        await self.services.refresh()
        if name in ("faculties", "grades", "groups", "alphabet", "teachers"):
            if name == "faculties":
                faculties = {g.faculty.id: g.faculty for g in self.services.groups.values() if g.faculty}
                items = [(f.button_name, {"name": "grades", "faculty": f.id}) for f in sorted(faculties.values(), key=lambda f: f.button_name)]
                text = "🎓 Выберите факультет"
            elif name == "grades":
                grades = sorted({g.grade for g in self.services.groups.values() if g.faculty_id == int(screen["faculty"])})
                items = [(f"{grade} курс", {"name": "groups", "faculty": screen["faculty"], "grade": grade}) for grade in grades]
                text = "Выберите курс"
            elif name == "groups":
                groups = sorted((g for g in self.services.groups.values() if g.faculty_id == int(screen["faculty"]) and g.grade == int(screen["grade"])), key=lambda g: g.title)
                items = [(g.title, {"name": "entity", "kind": "group", "id": g.id}) for g in groups]
                text = "Выберите группу"
            elif name == "alphabet":
                letters = sorted({t.full_name[0].upper() for t in self.services.teachers.values() if t.full_name})
                items = [(letter, {"name": "teachers", "letter": letter}) for letter in letters]
                text = "Выберите первую букву фамилии"
            else:
                teachers = sorted((t for t in self.services.teachers.values() if t.full_name.upper().startswith(screen["letter"])), key=lambda t: t.full_name)
                items = [(t.short_name, {"name": "entity", "kind": "teacher", "id": t.id}) for t in teachers]
                text = "Выберите преподавателя"
            selected, page, pages = paginate(items, screen.get("page", 0))
            screen["page"] = page
            if pages > 1:
                text += f" · {page + 1}/{pages}"
            if not selected:
                text += "\nСписок пуст"
            choices = [button(label, "open", screen=target) for label, target in selected]
            rows = [choices[i:i + 2] for i in range(0, len(choices), 2)]
            nav = []
            if page:
                nav.append(button("←", "open", screen={**screen, "page": page - 1}))
            if page + 1 < pages:
                nav.append(button("→", "open", screen={**screen, "page": page + 1}))
            return text, rows + ([nav] if nav else []) + [home]
        if name in ("entity", "confirm", "schedule"):
            target = self.services.target(screen["kind"], screen["id"])
            if name == "confirm":
                return f"Заменить текущую подписку на «{target.display_name}»?\nПредыдущая подписка будет отменена.", [[button("Продолжить", "confirm")], home]
            if name == "entity":
                rows = [[open_button("🗓 Расписание", "schedule", kind=screen["kind"],
                                     id=target.id, mode="1day", shift=0)]]
                sub = await self.services.subscriptions.get_subscription_by_target(target)
                rows.append([button("✖ Отписаться", "unsubscribe", id=sub.id) if sub else button("⭐ Подписаться", "subscribe")])
                if self.settings.show_entity_links and target.endpoint:
                    rows.append([link("🔗 Страница расписания", urljoin(self.settings.base_link.rstrip('/') + '/', target.endpoint.lstrip('/')))])
                return target.display_name, rows + [home]
            mode = screen.get("mode", "1day")
            span, shift, back, forward = date_span(mode, screen.get("shift", 0), self.settings.schedule_timezone)
            screen["shift"] = shift
            lessons = await self.services.lessons.get_lessons(target, span)
            pages = schedule_pages(target, lessons, span)
            page = max(0, min(int(screen.get("page", 0)), len(pages) - 1))
            screen["page"] = page
            nav = []
            if shift > -back:
                nav.append(button("⬅️", "open", screen={**screen, "shift": shift - 1, "page": 0}))
            if shift < forward:
                nav.append(button("➡️", "open", screen={**screen, "shift": shift + 1, "page": 0}))
            rows = [nav] if nav else []
            if len(pages) > 1:
                page_nav = []
                if page:
                    page_nav.append(button("← Страница", "open", screen={**screen, "page": page - 1}))
                if page + 1 < len(pages):
                    page_nav.append(button("Страница →", "open", screen={**screen, "page": page + 1}))
                rows.append(page_nav)
            rows += self.schedule_buttons(screen["kind"], target.id)
            rows += [[button("🔄 Обновить", "open", screen=screen)], [button("◀ Назад", "back")]]
            return pages[page] + (f"\n\nСтраница {page + 1}/{len(pages)}" if len(pages) > 1 else ""), rows
        raise ValueError("Unknown screen")

    @staticmethod
    def schedule_buttons(kind, entity_id):
        def schedule(label, mode, shift=0):
            return open_button(label, "schedule", kind=kind, id=entity_id, mode=mode, shift=shift)
        return [[schedule("Сегодня", "1day"), schedule("Неделя", "week")]]
