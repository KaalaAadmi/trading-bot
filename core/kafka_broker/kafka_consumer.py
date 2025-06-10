import asyncio
import json
import logging
from datetime import datetime, timezone

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaConnectionError

from .kafka_producer import KafkaProducerWrapper

logger = logging.getLogger(__name__)


class KafkaConsumerWrapper:
    def __init__(self, topic: str, group_id: str, bootstrap_servers: str):
        self.topic = topic
        self.dlq_topic = f"{topic}_dlq"  # Convention for DLQ topic name
        self.group_id = group_id
        self.bootstrap_servers = bootstrap_servers
        self.consumer = AIOKafkaConsumer(self.topic, bootstrap_servers=self.bootstrap_servers, group_id=self.group_id, value_deserializer=lambda v: json.loads(v.decode("utf-8")), auto_offset_reset="earliest", enable_auto_commit=True, auto_commit_interval_ms=5000)  # Keep auto-commit for simplicity with DLQ pattern
        # Each consumer gets its own producer for publishing to the DLQ
        self.dlq_producer = KafkaProducerWrapper(bootstrap_servers=self.bootstrap_servers)
        self._is_started = False

    async def _start_dependencies(self):
        """Starts consumer and DLQ producer with retries."""
        # Start DLQ producer first
        await self.dlq_producer.start()

        # Start consumer with retries
        logger.info(f"Starting consumer for topic '{self.topic}' with group '{self.group_id}'...")
        retries = 5
        for i in range(retries):
            try:
                await self.consumer.start()
                self._is_started = True
                logger.info(f"Consumer for topic '{self.topic}' started successfully.")
                return
            except KafkaConnectionError as e:
                wait_time = 2**i
                logger.error(f"Consumer for '{self.topic}' failed to connect (attempt {i+1}/{retries}). " f"Retrying in {wait_time} seconds... Error: {e}")
                if i < retries - 1:
                    await asyncio.sleep(wait_time)
                else:
                    logger.critical(f"Could not start consumer for '{self.topic}'. Aborting.")
                    raise

    async def consume(self, callback_async):
        """
        Starts the consumer and runs the callback for each message.
        If callback fails, message is sent to a Dead-Letter Queue (DLQ).
        """
        await self._start_dependencies()

        try:
            async for msg in self.consumer:
                logger.debug(f"Message received on topic '{self.topic}': {msg.value}")
                try:
                    # The callback MUST be an async function
                    await callback_async(msg.value)
                except Exception as e:
                    # --- Dead-Letter Queue Logic ---
                    error_message = str(e)
                    logger.exception(f"Error processing message from topic '{self.topic}'. " f"Sending to DLQ topic '{self.dlq_topic}'. Error: {error_message}")

                    # Construct the dead-letter message
                    dead_letter = {"original_topic": self.topic, "group_id": self.group_id, "timestamp_utc": datetime.now(timezone.utc).isoformat(), "error": error_message, "original_message": msg.value}

                    # Publish the failed message to the DLQ for later analysis
                    try:
                        await self.dlq_producer.publish(self.dlq_topic, dead_letter)
                    except Exception as pub_e:
                        logger.critical(f"CRITICAL: Failed to publish to DLQ topic '{self.dlq_topic}'. " f"Potential data loss. Error: {pub_e}")

        finally:
            logger.info(f"Stopping consumer and DLQ producer for topic '{self.topic}'...")
            await self.consumer.stop()
            await self.dlq_producer.stop()
            self._is_started = False
