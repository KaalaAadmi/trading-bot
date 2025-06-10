import asyncio
import json
import logging

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaConnectionError

from agents.common.utils import serialize

logger = logging.getLogger(__name__)


class KafkaProducerWrapper:
    def __init__(self, bootstrap_servers: str):
        self.producer = AIOKafkaProducer(bootstrap_servers=bootstrap_servers, value_serializer=lambda v: json.dumps(serialize(v)).encode("utf-8"), acks="all")  # Ensure messages are fully received by brokers
        self._is_started = False

    async def start(self):
        """
        Starts the Kafka producer with a retry mechanism.
        """
        if self._is_started:
            return

        logger.info("Starting Kafka producer...")
        retries = 5
        for i in range(retries):
            try:
                await self.producer.start()
                self._is_started = True
                logger.info("Kafka producer started successfully.")
                return  # Exit on successful connection
            except KafkaConnectionError as e:
                wait_time = 2**i
                logger.error(f"Failed to connect to Kafka (attempt {i+1}/{retries}). " f"Retrying in {wait_time} seconds... Error: {e}")
                if i < retries - 1:
                    await asyncio.sleep(wait_time)  # Exponential backoff
                else:
                    logger.critical("Could not start Kafka producer after several retries. Aborting.")
                    raise  # Re-raise the final exception

    async def stop(self):
        if self._is_started:
            logger.info("Stopping Kafka producer...")
            await self.producer.stop()
            self._is_started = False
            logger.info("Kafka producer stopped.")

    async def publish(self, topic: str, message: dict, key: str = None):
        """
        Publishes a message to a Kafka topic. Retries on connection error during publish.
        """
        if not self._is_started:
            await self.start()

        key_bytes = key.encode("utf-8") if key else None
        try:
            logger.debug(f"Publishing message to topic '{topic}': {message}")
            await self.producer.send_and_wait(topic, value=message, key=key_bytes)
        except KafkaConnectionError:
            logger.error(f"Kafka connection lost during publish to '{topic}'. Attempting to reconnect and retry...")
            self._is_started = False  # Mark as not started to trigger reconnect
            await self.start()  # Attempt to reconnect
            # Retry publishing once after successful reconnection
            await self.producer.send_and_wait(topic, value=message, key=key_bytes)
            logger.info(f"Successfully re-published message to '{topic}' after reconnection.")
        except Exception as e:
            logger.exception(f"An unexpected error occurred while publishing to topic '{topic}': {e}")
            raise
