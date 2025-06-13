# from apscheduler.schedulers.background import BackgroundScheduler
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agents.data_collector.agent import DataCollectorAgent
from agents.ticker_updater.agent import TickerUpdaterAgent

# import asyncio

logger = logging.getLogger(__name__)


def start_scheduler(data_collector_agent: DataCollectorAgent, ticker_updater_agent: TickerUpdaterAgent):
    """
    Start the APScheduler and schedule jobs using the provided agent instances.
    Jobs will align to candle closes based on CronTriggers.
    Initial data fetch for assets is handled by DataCollectorAgent upon receiving asset list.
    """
    scheduler = AsyncIOScheduler()

    # 🔁 Schedule the TickerUpdaterAgent to run daily at 23:59
    scheduler.add_job(
        ticker_updater_agent.update_tickers,
        CronTrigger(hour=23, minute=59),
        id="ticker_updater_job",
        name="Ticker Updater Agent",
        misfire_grace_time=300,
    )
    logger.info("Scheduled TickerUpdaterAgent to run daily at 23:59.")

    # 🕔 Schedule 5m LTF job on exact 5-min intervals
    scheduler.add_job(
        data_collector_agent.fetch_ltf_data,  # Directly pass the coroutine
        CronTrigger(minute="*/5"),
        id="ltf_fetch_job",
        name="Fetch 5m data (LTF) - Recurring",
        misfire_grace_time=120,
        replace_existing=True,
    )
    logger.info("Scheduled LTF data fetch every 5 minutes.")

    # ⏰ Schedule 1h HTF job exactly on the hour
    scheduler.add_job(
        data_collector_agent.fetch_htf_data,
        CronTrigger(minute=0),
        id="htf_fetch_job",
        name="Fetch 1h data (HTF) - Recurring",
        misfire_grace_time=300,
        replace_existing=True,
    )
    logger.info("Scheduled HTF data fetch every hour.")

    try:
        scheduler.start()  # AsyncIOScheduler.start() is non-blocking
        logger.info("APScheduler started successfully.")
    except Exception as e:
        logger.exception("Failed to start APScheduler: %s", e)
