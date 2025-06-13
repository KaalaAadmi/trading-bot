import asyncio
import json
import logging
from datetime import date, datetime, timedelta

import pandas as pd
import redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.common import utils as common
from core.config.config_loader import load_settings
from core.kafka_broker.kafka_consumer import KafkaConsumerWrapper
from core.kafka_broker.kafka_producer import KafkaProducerWrapper
from core.zerodha.client import ZerodhaClient

logger = logging.getLogger(__name__)


class DataCollectorAgent:
    """
    Agent for collecting ohlcv data for all filtered assets for both of the timeframes.
    """

    def __init__(self, settings_path=None):
        if settings_path:
            self.settings = load_settings(settings_path=settings_path)
        else:
            self.settings = load_settings()
        # Initialize Kafka producer and consumer
        kafka_cfg = self.settings["kafka"]
        self.producer = KafkaProducerWrapper(bootstrap_servers=kafka_cfg["bootstrap_servers"])
        self.consumer = KafkaConsumerWrapper(topic=kafka_cfg["topics"]["market_research"], group_id="data_collector_group", bootstrap_servers=kafka_cfg["bootstrap_servers"])
        self.output_topic = kafka_cfg["topics"]["data_collector"]

        # Initialize database connection
        db_config = self.settings["database"]
        url = f"postgresql+asyncpg://{db_config['user']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['db']}"
        self.db_engine = create_async_engine(url, pool_size=10, max_overflow=5)
        logger.info("Database connection established.")

        # Initialize a direct Redis client for caching ---
        redis_cfg = self.settings.get("redis", {})
        redis_url = redis_cfg.get("url", "redis://localhost:6379")
        self.redis_client = redis.from_url(redis_url)
        logger.info(f"Connected to Redis for caching at {redis_url}")

        # Load timeframes(ltf and htf)
        self.timeframes = self.settings["timeframes"]
        self.history = self.settings["history"]

        # Track filtered assets
        self.filtered_assets = []

        # Track failed assets
        self.failed_assets = []
        self.semaphore = asyncio.Semaphore(5)  # Limit concurrent API calls
        logger.info("DataCollectorAgent initialized")

    async def subscribe_to_market_research(self):
        """
        Subscribe to the market research topic and once a message is received, start the data collection process.
        """
        await self.producer.start()
        await self.consumer.consume(self.process_filtered_assets)

    async def stop(self):
        await self.producer.stop()

    async def process_filtered_assets(self, message):
        """
        Process the incoming message and start data collection process.
        :param message: The message received from the market research topic.
        """
        try:
            logger.info(f"Received filtered assets message: {message}")
            self.filtered_assets = json.loads(message.get("filtered_assets", "[]"))
            if not self.filtered_assets:
                logger.warning("No filtered assets found in the message.")
                return

            logger.info(f"Updated internal list to track {len(self.filtered_assets)} assets.")

            # Perform an initial data fetch for both timeframes to populate history
            logger.info("Performing initial historical data backfill for new asset list...")
            await self.fetch_htf_data(is_initial_fetch=True)
            await self.fetch_ltf_data(is_initial_fetch=True)
            logger.info("Initial data backfill complete.")

        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON from message: {message}")
        except Exception as e:
            logger.exception(f"Error processing filtered assets message: {e}")
        except Exception as e:
            logger.exception(f"Error processing filtered assets message: {e}")

    async def fetch_ltf_data(self, is_initial_fetch=False):
        """Scheduled job to fetch Low-Timeframe data for all tracked assets."""
        await self._fetch_data_for_timeframe("ltf", is_initial_fetch)

    async def fetch_htf_data(self, is_initial_fetch=False):
        """Scheduled job to fetch High-Timeframe data for all tracked assets."""
        await self._fetch_data_for_timeframe("htf", is_initial_fetch)

    async def _fetch_data_for_timeframe(self, timeframe_key: str, is_initial_fetch: bool):
        """Generic method to fetch data for a given timeframe (LTF or HTF)."""
        if not self.filtered_assets:
            logger.info(f"[{timeframe_key.upper()}] No assets to track. Skipping fetch cycle.")
            return

        # Market open/closed logic from your previous agent
        if not is_initial_fetch and common.is_weekend_or_holiday():
            logger.info(f"[{timeframe_key.upper()}] Weekend or holiday. Skipping stock data fetch.")
            assets_to_fetch = self.filtered_assets
        elif not is_initial_fetch and not common.is_market_open():
            logger.info(f"[{timeframe_key.upper()}] Market is closed. Skipping stock data fetch.")
            assets_to_fetch = self.filtered_assets
        else:
            assets_to_fetch = self.filtered_assets

        if not assets_to_fetch:
            logger.info(f"[{timeframe_key.upper()}] No assets to fetch for the current time.")
            return

        logger.info(f"[{timeframe_key.upper()}] Starting fetch for {len(assets_to_fetch)} assets.")
        tasks = [self.process_ohlcv(asset_info, timeframe_key, is_initial_fetch) for asset_info in assets_to_fetch]
        await asyncio.gather(*tasks)
        logger.info(f"[{timeframe_key.upper()}] Fetch cycle complete.")

    async def process_ohlcv(self, asset_info: dict, timeframe_key: str, is_initial_fetch: bool):
        """Fetches, stores, and publishes OHLCV data for a single asset and timeframe."""
        asset = asset_info["asset"]
        instrument_token = asset_info["instrument_token"]
        timeframe = self.timeframes[timeframe_key]

        async with self.semaphore:
            try:
                # Use a long lookback for the initial fetch, then only fetch incrementally
                last_fetched = None if is_initial_fetch else await self.get_last_fetched_timestamp(asset, timeframe)
                lookback_days = self.history[f"{timeframe_key}_lookback_days"]

                df = await self.fetch_ohlcv(asset, instrument_token, timeframe, lookback_days, last_fetched)

                if df is None or df.empty:
                    logger.warning(f"[{asset}][{timeframe}] No data fetched.")
                    return

                await self._store_data(asset, timeframe, df)
                await self._publish_data_event(asset, timeframe, df)
                # Update the timestamp in Redis to mark success
                last_ts = pd.to_datetime(df["timestamp"].iloc[-1])
                await self.update_last_fetched_timestamp(asset, timeframe, last_ts)

            except Exception as e:
                logger.exception(f"[{asset}][{timeframe}] Critical error during OHLCV processing: {e}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def fetch_ohlcv(self, asset: str, token: int, timeframe: str, lookback_days: int, last_fetched: datetime = None):
        """Fetches data from Zerodha. Uses last_fetched timestamp to get incremental data."""
        kite = ZerodhaClient.get_instance()
        if not kite:
            return None

        try:
            to_date = date.today()
            # If we have a last fetched timestamp, start from that day. Otherwise, use full lookback.
            from_date = last_fetched.date() if last_fetched else to_date - timedelta(days=lookback_days)

            # Map your internal timeframe names to what Kite API expects
            interval_mapping = {"5m": "5minute", "1h": "hour", "1hour": "hour", "1d": "day"}
            kite_interval = interval_mapping.get(timeframe, timeframe)

            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, lambda: kite.historical_data(token, from_date, to_date, kite_interval))

            if not data:
                return None

            df = pd.DataFrame(data)
            df.rename(columns={"date": "timestamp"}, inplace=True)
            df["timestamp"] = pd.to_datetime(df["timestamp"])

            # If we are fetching incrementally, filter out rows we already have
            if last_fetched:
                df = df[df["timestamp"] > last_fetched]

            if df.empty:
                return None

            df["symbol"] = asset
            df["timeframe"] = timeframe
            return df[["timestamp", "open", "high", "low", "close", "volume", "symbol", "timeframe"]]

        except Exception as e:
            logger.exception(f"[{asset}] Error fetching data from Zerodha: {e}")
            return None

    async def _store_data(self, asset: str, timeframe: str, df: pd.DataFrame):
        """Stores a DataFrame of OHLCV data into the database using ON CONFLICT DO NOTHING."""
        records = df.to_dict("records")
        async with self.db_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO ohlcv_data (symbol, timeframe, timestamp, open, high, low, close, volume)
                    VALUES (:symbol, :timeframe, :timestamp, :open, :high, :low, :close, :volume)
                    ON CONFLICT (symbol, timestamp, timeframe) DO NOTHING
                """
                ),
                records,
            )
        logger.info(f"[{asset}][{timeframe}] Stored/updated {len(records)} rows in database.")

    async def _publish_data_event(self, asset: str, timeframe: str, df: pd.DataFrame):
        """Publishes an event to Kafka indicating new data is available."""
        message = {"ticker": asset, "timeframe": timeframe, "new_data": "true", "range_start": df["timestamp"].iloc[0].isoformat(), "range_end": df["timestamp"].iloc[-1].isoformat()}
        await self.producer.publish(self.output_topic, message, key=asset)
        logger.info(f"[{asset}][{timeframe}] Published data event to topic '{self.output_topic}'.")

    async def get_last_fetched_timestamp(self, asset: str, timeframe: str) -> datetime | None:
        """Retrieve the last fetched timestamp for a given asset and timeframe from Redis cache."""
        try:
            key = f"last_fetched:{asset}:{timeframe}"
            timestamp_str = self.redis_client.get(key)
            if timestamp_str:
                dt_obj = datetime.fromisoformat(timestamp_str.decode("utf-8"))
                logger.debug(f"[{asset}][{timeframe}] Found last fetched timestamp in Redis: {dt_obj}")
                return dt_obj
            return None
        except Exception as e:
            logger.error(f"[{asset}][{timeframe}] Error retrieving last timestamp from Redis: {e}")
            return None

    async def update_last_fetched_timestamp(self, asset: str, timeframe: str, timestamp: datetime):
        """Update the last fetched timestamp for a given asset and timeframe in Redis cache."""
        try:
            key = f"last_fetched:{asset}:{timeframe}"
            self.redis_client.set(key, timestamp.isoformat(), ex=172800)
            logger.info(f"[{asset}][{timeframe}] Updated last fetched timestamp in Redis: {timestamp.isoformat()}")
        except Exception as e:
            logger.error(f"[{asset}][{timeframe}] Error updating last timestamp in Redis: {e}")
