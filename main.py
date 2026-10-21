"""U-bot Pay — single process: FastAPI (uvicorn) + Telegram bot polling."""
import os, asyncio, logging
import uvicorn
import api, tg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def _pending_gc_loop():
    """Round 24 audit: api._pending_gc() existed but was never wired up, so the
    30-minute TTL and 1000-row cap on staged payments were dead. Without
    that, a user who staged N payments and never confirmed would in-memory
    leak until /confirm or /cancel, and a chatty client could push the
    dict unbounded. Schedule every minute."""
    import api, time as _time
    while True:
        try:
            await asyncio.sleep(60)
            api._pending_gc()
        except Exception:
            # Never let the gc die.
            await asyncio.sleep(60)


async def main():
    config = uvicorn.Config(api.app, host="127.0.0.1", port=int(os.environ.get("UBOT_PORT", "8090")), log_level="info")
    server = uvicorn.Server(config)
    tasks = [asyncio.create_task(server.serve())]
    tasks.append(asyncio.create_task(_pending_gc_loop()))
    if os.environ.get("UBOT_BOT_TOKEN"):
        app = tg.build()
        await app.initialize(); await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logging.info("telegram polling started")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
