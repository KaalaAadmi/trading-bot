import json
import logging
import os
from time import sleep

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.common.by import By

from core.config.config_loader import load_settings
from core.kafka_broker.kafka_producer import KafkaProducerWrapper
from core.zerodha.client import ZerodhaClient

logger = logging.getLogger(__name__)


class TickerUpdaterAgent:
    """
    Agent to update the tickers for the initial universe
    """

    def __init__(self, output_path: str = None):
        """
        Initialize the TickerUpdaterAgent
        """
        self.settings = load_settings()
        self.output_path = output_path or self.settings["tickers"]["file_path"]
        kafka_cfg = self.settings["kafka"]
        self.kafka_producer = KafkaProducerWrapper(bootstrap_servers=kafka_cfg["bootstrap_servers"])
        self.kafka_topic = kafka_cfg["topics"]["ticker_updater"]
        logger.info(f"TickerUpdaterAgent initialized with output path: {self.output_path} and Kafka topic: {self.kafka_topic}")

    def fetch_nifty50_tickers(self):
        """
        Fetch the Nifty 50 tickers from website
        """
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")  # Run in headless mode
        options.add_argument("--no-sandbox")  # Bypass OS security

        driver = webdriver.Chrome(options=options)
        logger.info("Loading Website for Nifty 50 tickers...")
        driver.get("https://www.equitypandit.com/list/nifty-50-companies")
        sleep(5)
        logger.info("Fetching Nifty 50 tickers...")
        tickers = []
        for i in range(1, 51):
            try:
                ticker = driver.find_element(by=By.XPATH, value=f"/html/body/section[6]/div/div/div[2]/div/div[2]/table/tbody/tr[{i}]/td[1]/a[1]")
                tickers.append(ticker.text)
            except Exception as e:
                logger.info(f"Error retrieving ticker for row {i}: {e}")
        driver.quit()
        logger.info(f"Fetched {len(tickers)} Nifty 50 tickers.")
        return tickers

    def fetch_bank_nifty_tickers(self):
        """
        Fetch the tickers for Bank Nifty from website
        """
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")  # Run in headless mode
        options.add_argument("--no-sandbox")  # Bypass OS security

        driver = webdriver.Chrome(options=options)
        logger.info("Loading Website for Bank Nifty tickers...")
        driver.get("https://www.equitypandit.com/list/banknifty-companies")
        sleep(5)
        logger.info("Fetching Bank Nifty tickers...")
        tickers = []
        for i in range(1, 13):
            try:
                ticker = driver.find_element(by=By.XPATH, value=f"/html/body/section[6]/div/div/div[2]/div/div[2]/table/tbody/tr[{i}]/td[1]/a[1]")
                tickers.append(ticker.text)
            except NoSuchElementException as e:
                logger.info(f"Error retrieving ticker for row {i}: {e}")
                break
            except Exception as e:
                logger.info(f"Error retrieving ticker for row {i}: {e}")
        driver.quit()
        logger.info(f"Fetched {len(tickers)} Bank Nifty tickers.")
        return tickers

    async def update_tickers(self):
        """
        Fetch and Update tickers and store them in the output file after only keeping the unique tickers
        """
        try:
            nifty50_tickers = self.fetch_nifty50_tickers()
            bank_nifty_tickers = self.fetch_bank_nifty_tickers()

            if not nifty50_tickers or not bank_nifty_tickers:
                logger.error("Failed to fetch tickers for Nifty 50 or Bank Nifty.")
                return

            # Combine the tickers and remove duplicates
            combined_tickers = list(set(nifty50_tickers + bank_nifty_tickers))
            logger.info(f"Combined tickers count: {len(combined_tickers)}")
            logger.info("Fetching instrument tokens for combined tickers...")
            instruments = ZerodhaClient.get_instance().instruments("NSE")
            final_tickers = {}
            for inst in instruments:
                if inst["tradingsymbol"] in combined_tickers:
                    final_tickers[inst["tradingsymbol"]] = inst["instrument_token"]

            logger.info(f"Final tickers count: {len(final_tickers)}")
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)  # Ensure directory exists
            with open(self.output_path, "w") as file:
                json.dump(final_tickers, file, indent=4)
            logger.info("Updated tickers and saved to %s.", self.output_path)

            # Publish the updated tickers to Kafka
            await self.kafka_producer.publish(self.kafka_topic, {"message": "Ticker update completed successfully.", "tickers": final_tickers})
            logger.info("Published updated tickers to Kafka topic '%s'.", self.kafka_topic)

        except Exception as e:
            logger.error("Failed to update tickers: %s", str(e))
