import json
import logging
import os

# from datetime import date, datetime, timezone
from datetime import date, timedelta

import pandas as pd

from core.config.config_loader import load_settings
from core.kafka_broker.kafka_consumer import KafkaConsumerWrapper
from core.kafka_broker.kafka_producer import KafkaProducerWrapper
from core.zerodha.client import ZerodhaClient

logger = logging.getLogger(__name__)


class MarketResearchAgent:
    """
    Agent to conduct market research based on volume and volatility of the asset on daily data for the past 30 days.
    """

    def __init__(self, settings_path=None):
        """
        Initialize the MarketResearchAgent with settings.
        :param settings_path: Path to the settings file.
        """
        # Load settings
        if settings_path:
            self.settings = load_settings(settings_path=settings_path)
        else:
            self.settings = load_settings()

        # Initialize Kafka producer
        kafka_cfg = self.settings["kafka"]
        self.ticker_updates = KafkaConsumerWrapper(bootstrap_servers=kafka_cfg["bootstrap_servers"], topic=kafka_cfg["topics"]["ticker_updater"], group_id="market_research_agent")
        self.market_research = KafkaProducerWrapper(bootstrap_servers=kafka_cfg["bootstrap_servers"])
        self.kafka_topic = kafka_cfg["topics"]["market_research"]
        logger.info("MarketResearchAgent initialized")

    async def subscribe_to_ticker_updates(self):
        """
        Subscribe to the ticker updates topic and once message is received, perform the market research.
        """
        await self.ticker_updates.consume(self.run_on_message)

    async def run_on_message(self, message):
        """
        Process the incoming message and perform market research.
        :param message: The incoming message from the ticker updates topic.
        """
        logger.info(f"Received message: {message}")
        await self.perform_market_research()

    def fetch_assets(self):
        """
        Fetch the assets from `data/tickers.json` file.
        """
        try:
            ticker_file_path = self.settings["tickers"]["file_path"]
            if not os.path.exists(ticker_file_path):
                logger.error("Ticker file not found at %s. Ensure TickerUpdaterAgent has run.", ticker_file_path)
                return {}

            with open(ticker_file_path, "r") as file:
                tickers = json.load(file)
                logger.info("Loaded tickers from %s: %s", ticker_file_path, tickers)
                return tickers
        except Exception as e:
            logger.error("Failed to load tickers from file: %s", str(e))
            return {}

    def fetch_ohlcv(self, asset, instrument_token):
        """
        Fetch the ohlcv data for the given asset using zerodha API.
        :param asset: The asset for which to fetch the ohlcv data.
        """
        # TODO
        kite = ZerodhaClient.get_instance()
        try:
            data = kite.historical_data(
                instrument_token=instrument_token,
                from_date=date.today() - timedelta(self.settings["agents"]["market_research"]["loopback_days"]),
                to_date=date.today(),
                interval="day",
            )
            logger.info("Fetched OHLCV data for asset: %s", asset)
            # Convert the data to a DataFrame
            df = pd.DataFrame(data)
            # Ensure the DataFrame has the correct columns
            if not all(col in df.columns for col in ["date", "open", "high", "low", "close", "volume"]):
                logger.error("OHLCV data for asset '%s' is missing required columns.", asset)
                return None
            # Convert 'date' to datetime and set it as index
            df["date"] = pd.to_datetime(df["date"], unit="ms", utc=True)
            df.set_index("date", inplace=True)
            # Calculate daily returns
            df["daily_returns"] = df["close"].pct_change().fillna(0)
            # Rename columns to match expected format
            df.rename(columns={"open": "price_open", "high": "price_high", "low": "price_low", "close": "price_close", "volume": "volume"}, inplace=True)
            logger.info("OHLCV dataframe for asset '%s' fetched successfully.", asset)
            return df
        except Exception as e:
            logger.error("Failed to fetch OHLCV data for asset '%s': %s", asset, str(e))
            return None

    def filter_assets(self, ohlcv_data):
        """Apply filters to identify assets with unmitigated FVGs and imbalance."""
        filtered_assets = []
        config = self.settings["agents"]["market_research"]
        volume_threshold = config["volume_threshold"]
        volatility_threshold = config["volatility_threshold"]
        price_change_threshold = config["price_change_threshold"]
        for asset, df in ohlcv_data.items():
            if len(df) < 5:  # Ensure there are enough data points for the rolling window
                logger.warning("Insufficient data for asset '%s'. Skipping...", asset)
                continue
            try:
                # Calculate average volume (Liquidity Filter)
                avg_volume = df["volume"].tail(5).mean()
                is_liquid = avg_volume > volume_threshold

                # Calculate daily returns and volatility (Volatility Filter)
                volatility = df["daily_returns"].tail(14).std()
                is_volatile = volatility > volatility_threshold
                # Calculate recent price movement (Momentum Filter) - Commented out for now
                recent_change = (df["price_close"].iloc[-1] - df["price_close"].iloc[-5]) / df["price_close"].iloc[-5]
                has_momentum = abs(recent_change) > price_change_threshold

                logger.info("Asset: %s, Avg Volume: %.0f, Volatility: %.4f", asset, avg_volume, volatility)

                # Apply only liquidity and volatility filters for now
                if is_liquid and is_volatile and has_momentum:
                    filtered_assets.append(asset)
            except Exception as e:
                logger.error("Error filtering asset '%s': %s", asset, str(e))

        logger.info("Filtered %d assets based on liquidity and volatility criteria.", len(filtered_assets))
        logger.info("Filtered assets: %s", filtered_assets)
        return filtered_assets

    async def perform_market_research(self):
        """Run the Market Research Agent."""
        logger.info("Starting Market Research Agent...")
        assets = self.fetch_assets()
        ohlcv_data = {asset: self.fetch_ohlcv(asset, instrument_token) for asset, instrument_token in assets.items() if asset is not None}  # Skip assets with missing data
        filtered_assets = self.filter_assets(ohlcv_data)

        # Publish the filtered assets as a list to the market_research_signals stream
        await self.market_research.publish(self.kafka_topic, {"filtered_assets": json.dumps(filtered_assets)})
        logger.info("Published filtered assets to topic '%s': %s", self.kafka_topic, filtered_assets)

        logger.info("Market Research Agent processing completed. Filtered %d assets for next steps.", len(filtered_assets))
