# from apscheduler.schedulers.background import BackgroundScheduler
# from agents.market_data_collector import fetch_ltf_data, fetch_htf_data
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from agents.ticker_updater.agent import TickerUpdaterAgent

# import asyncio

logger = logging.getLogger(__name__)


def start_scheduler(data_collector_agent):
    # def start_scheduler():
    """Start the APScheduler and align jobs to candle closes."""
    scheduler = AsyncIOScheduler()
    # scheduler = BackgroundScheduler()
    ticker_updater = TickerUpdaterAgent()

    # 🔁 Schedule the TickerUpdaterAgent to run daily at 23:59
    scheduler.add_job(
        ticker_updater.update_tickers,
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
        name="Fetch 5m data (LTF)",
        misfire_grace_time=120,
    )
    logger.info("Scheduled LTF data fetch every 5 minutes.")

    # ⏰ Schedule 1h HTF job exactly on the hour
    scheduler.add_job(
        data_collector_agent.fetch_htf_data,
        CronTrigger(minute=0),
        id="htf_fetch_job",
        name="Fetch 1h data (HTF)",
        misfire_grace_time=300,
    )
    logger.info("Scheduled HTF data fetch every hour.")

    scheduler.start()
