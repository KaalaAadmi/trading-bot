import asyncio
import logging
import logging.config
import os

import yaml

from agents.data_collector.agent import DataCollectorAgent
from agents.market_research.agent import MarketResearchAgent
from agents.ticker_updater.agent import TickerUpdaterAgent
from core.kafka_broker.kafka_producer import KafkaProducerWrapper
from core.scheduler.apscheduler_config import start_scheduler

# logging.basicConfig(level=logging.INFO)


# --- Setup Logging ---
def setup_logging(config_path="core/config/logging_config.yaml", default_level=logging.INFO):
    """Load logging configuration from a YAML file."""
    effective_config_path = os.path.join(os.path.dirname(__file__), config_path)  # Ensure path is relative to main.py if needed, or use absolute

    # If main.py is in the root, and core is a subdirectory:
    effective_config_path = config_path

    if os.path.exists(effective_config_path):
        with open(effective_config_path, "rt") as f:
            try:
                config = yaml.safe_load(f.read())
                # Ensure logs directory exists if a FileHandler is defined
                for handler_config in config.get("handlers", {}).values():
                    if handler_config.get("class") == "logging.FileHandler":
                        log_filename = handler_config.get("filename")
                        if log_filename:
                            log_dir = os.path.dirname(log_filename)
                            if log_dir and not os.path.exists(log_dir):
                                os.makedirs(log_dir)
                                print(f"Created logging directory: {log_dir}")  # For verification
                logging.config.dictConfig(config)
                logging.getLogger(__name__).info("Logging configured successfully from YAML file.")
            except Exception as e:
                logging.basicConfig(level=default_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
                logging.getLogger(__name__).error(f"Error loading logging config from {effective_config_path}: {e}. Falling back to basicConfig.")
    else:
        logging.basicConfig(level=default_level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        logging.getLogger(__name__).warning(f"Logging config file {effective_config_path} not found. Using basicConfig.")


setup_logging()

logger = logging.getLogger(__name__)
# --- End Setup Logging ---


async def start_agents():
    """
    Start all agents in the system.
    """

    logging.info("Initializing the Agentic Trading Bot...")

    ticker_updater_agent = None
    market_research_agent = None
    data_collector_agent = None

    consumer_tasks = []
    try:
        # Initialize all agents
        ticker_updater_agent = TickerUpdaterAgent()
        market_research_agent = MarketResearchAgent()
        data_collector_agent = DataCollectorAgent()

        # Initialize the TickerUpdaterAgent
        logging.info("Running the TickerUpdaterAgent to fetch and update tickers.")
        await ticker_updater_agent.update_tickers()
        logging.info("TickerUpdaterAgent has completed its task.")

        # Initialize the MarketResearchAgent
        logging.info("Running the MarketResearchAgent to perform market research.")
        # await market_research_agent.subscribe_to_ticker_updates()
        market_research_task = asyncio.create_task(market_research_agent.subscribe_to_ticker_updates())
        consumer_tasks.append(market_research_task)
        logging.info("MarketResearchAgent has completed its task.")

        # Initialize the DataCollectorAgent
        logging.info("Running the DataCollectorAgent to collect data.")
        # await data_collector_agent.subscribe_to_market_research()
        data_collector_task = asyncio.create_task(data_collector_agent.subscribe_to_market_research())
        consumer_tasks.append(data_collector_task)
        logging.info("DataCollectorAgent has completed its task.")

        logging.info("All agents started successfully.")
        logging.info("[main.py] Loop ID: %s", id(asyncio.get_running_loop()))

        # Configure and start the scheduler, passing the correct agent instances
        try:
            logger.info("Configuring and starting scheduler...")
            # start_scheduler is synchronous and AsyncIOScheduler.start() is non-blocking
            start_scheduler(data_collector_agent=data_collector_agent, ticker_updater_agent=ticker_updater_agent)
            logger.info("Scheduler configured and started.")
        except Exception as e:
            logger.exception("Failed to configure or start scheduler: %s", e)

        logger.info("All agent consumers and scheduler started successfully. Bot is running.")

        while True:
            await asyncio.sleep(1)  # Prevent the script from exiting
    except asyncio.CancelledError:
        logger.info("Main bot operation was cancelled(typically due to shutdown).")
    except Exception as e:
        logger.exception("An unexpected error occurred in the main bot operations: %s", e)
    finally:
        logger.info("Initializing shutdown sequence for all resources...")
        # Stop consumer tasks (optional, as they might be cancelled by asyncio.run() shutdown)
        # for task in consumer_tasks:
        #     if not task.done():
        #         task.cancel()
        # if consumer_tasks:
        #     await asyncio.gather(*consumer_tasks, return_exceptions=True)
        # logger.info("Consumer tasks processing complete.")

        # Stop Kafka producers associated with each agent
        if ticker_updater_agent and hasattr(ticker_updater_agent, "kafka_producer"):
            logger.info("Stopping TickerUpdaterAgent's Kafka producer...")
            await ticker_updater_agent.kafka_producer.stop()

        if market_research_agent and hasattr(market_research_agent, "market_research"):  # This is its producer
            logger.info("Stopping MarketResearchAgent's Kafka producer...")
            await market_research_agent.market_research.stop()
        # Also stop its consumer's DLQ producer if it was started
        if market_research_agent and hasattr(market_research_agent, "ticker_updates") and market_research_agent.ticker_updates._dlq_producer_started:
            logger.info("Stopping MarketResearchAgent's consumer DLQ producer...")
            await market_research_agent.ticker_updates.dlq_producer.stop()

        if data_collector_agent and hasattr(data_collector_agent, "data_collector"):  # This is its producer
            logger.info("Stopping DataCollectorAgent's Kafka producer...")
            await data_collector_agent.data_collector.stop()
        # Also stop its consumer's DLQ producer
        if data_collector_agent and hasattr(data_collector_agent, "market_research") and data_collector_agent.market_research._dlq_producer_started:  # This is its consumer
            logger.info("Stopping DataCollectorAgent's consumer DLQ producer...")
            await data_collector_agent.market_research.dlq_producer.stop()

        logger.info("Resource cleanup complete.")


if __name__ == "__main__":
    try:
        asyncio.run(start_agents())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Shutting down the Agentic Trading Bot...")
        KafkaProducerWrapper.stop()
        logging.info("Kafka producer stopped successfully.")
        logging.info("Agentic Trading Bot has been shut down.")
        logging.info("Exiting the application.")
        exit(0)
