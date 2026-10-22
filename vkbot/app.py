"""Composition root; importing this module never starts polling."""
import asyncio
import logging
from contextlib import AsyncExitStack

from redis.asyncio import Redis
from vkbottle import Bot
from vkbottle.exception_factory import ErrorHandler
from vkbottle.modules import logger as vk_logger

from vkbot.api_client import AsyncClientSession, models_as_jsonschema
from vkbot.api_client.client_patch import patch_jsonapi_client
from vkbot.config import settings
from vkbot.controller import Controller
from vkbot.services import Services
from vkbot.storage import RedisStorage
from vkbot.callback import CallbackServer, validate_callback_config


def register_handlers(bot, controller):
    bot.on.private_message()(controller.handle_message)
    bot.on.raw_event("message_event")(controller.handle_callback)


async def run(config=settings):
    mode = config.bot_mode.strip().lower()
    if mode not in ("polling", "callback"):
        raise ValueError(f"Unsupported BOT_MODE={config.bot_mode!r}. Set BOT_MODE=polling or callback.")
    if mode == "callback":
        validate_callback_config(config)
    patch_jsonapi_client(verbose=False)
    async with AsyncExitStack() as stack:
        redis = Redis.from_url(config.redis_storage_url, decode_responses=True)
        stack.push_async_callback(redis.aclose)
        await redis.ping()
        client = AsyncClientSession(
            server_url=config.api_base_url, platform=config.platform,
            hmac_secret=config.hmac_secret, schema=models_as_jsonschema,
        )
        stack.push_async_callback(client.close)
        errors = ErrorHandler()

        async def report_error(error):
            logging.getLogger(__name__).error(
                "VK event/transport error",
                exc_info=(type(error), error, error.__traceback__),
            )

        errors.register_undefined_error_handler(report_error)
        bot = Bot(token=config.bot_token, error_handler=errors)
        stack.push_async_callback(bot.api.http_client.close)
        group_response = await bot.api.request("groups.getById", {})
        group_id = group_response["response"]["groups"][0]["id"]
        bot.polling.group_id = group_id
        storage = RedisStorage(redis, f"vkbot:{group_id}", config.storage_state_ttl, config.auth_nonce_ttl)
        controller = Controller(bot.api, Services(client, config), storage, config)
        register_handlers(bot, controller)
        logging.getLogger(__name__).info("VK bot started; community %s", group_id)
        if mode == "callback":
            await CallbackServer(bot, redis, group_id, config).run()
            return
        async for batch in bot.polling.listen():
            # Bound lifecycle to this loop; all event handlers finish before shutdown.
            async with asyncio.TaskGroup() as tasks:
                for event in batch.get("updates", []):
                    tasks.create_task(bot.process_event(event))


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The SDK's debug logs can contain tokens, ref and full events.
    if isinstance(vk_logger, logging.LoggerAdapter):
        vk_logger.logger.disabled = True
    elif isinstance(vk_logger, logging.Logger):
        vk_logger.disabled = True
    else:
        # vkbottle optionally uses loguru when it is installed.
        vk_logger.disable("vkbottle")
    logging.getLogger("jsonapi_client").setLevel(logging.WARNING)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception:
        logging.getLogger(__name__).exception("Bot stopped")
        raise SystemExit(1) from None
