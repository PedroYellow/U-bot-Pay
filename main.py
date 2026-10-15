"""U-bot Pay — single process: FastAPI (uvicorn) + Telegram bot polling."""
import os, asyncio, logging
import uvicorn
import api, tg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main():
    config = uvicorn.Config(api.app, host="127.0.0.1", port=int(os.environ.get("UBOT_PORT", "8090")), log_level="info")
    server = uvicorn.Server(config)
    tasks = [asyncio.create_task(server.serve())]
    if os.environ.get("UBOT_BOT_TOKEN"):
        app = tg.build()
        await app.initialize(); await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logging.info("telegram polling started")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
