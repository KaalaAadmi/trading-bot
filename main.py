import asyncio
import logging
import logging.config
import os

import yaml

from agents.market_research.agent import MarketResearchAgent
from agents.ticker_updater.agent import TickerUpdaterAgent
from core.kafka_broker.kafka_producer import KafkaProducerWrapper

# from core.scheduler.apscheduler_config import start_scheduler

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

    logging.info("Starting the Agentic Trading Bot...")

    # Initialize the TickerUpdaterAgent
    ticker_updater_agent = TickerUpdaterAgent()
    logging.info("Running the TickerUpdaterAgent to fetch and update tickers.")
    await ticker_updater_agent.update_tickers()
    logging.info("TickerUpdaterAgent has completed its task.")

    # Initialize the MarketResearchAgent
    market_research_agent = MarketResearchAgent()
    logging.info("Running the MarketResearchAgent to perform market research.")
    await market_research_agent.subscribe_to_ticker_updates()
    logging.info("MarketResearchAgent has completed its task.")

    logging.info("All agents started successfully.")
    logging.info("[main.py] Loop ID: %s", id(asyncio.get_running_loop()))

    # start_scheduler()  # Start the scheduler to run tasks periodically
    # logging.info("Scheduler started successfully.")
    while True:
        await asyncio.sleep(1)  # Prevent the script from exiting


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
