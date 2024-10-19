import zmq
import zmq.asyncio
import msgpack
import logging
import os
import asyncio

class MessageBroker:
    def __init__(self, max_queue_size=10):
        self.context = zmq.asyncio.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.subscriber = self.context.socket(zmq.SUB)
        self.logger = logging.getLogger(__name__)

        self.zmq_pub_address = os.environ.get('ZMQ_PUB_ADDRESS', 'tcp://zmq_proxy:5559')
        self.zmq_sub_address = os.environ.get('ZMQ_SUB_ADDRESS', 'tcp://zmq_proxy:5560')


        self.logger.info(f"Connecting to ZMQ_PUB_ADDRESS: {self.zmq_pub_address}")
        self.logger.info(f"Connecting to ZMQ_SUB_ADDRESS: {self.zmq_sub_address}")

        self.publisher.connect(self.zmq_pub_address)
        self.subscriber.connect(self.zmq_sub_address)

        # Set high water mark for the publisher and subscriber
        self.publisher.set_hwm(max_queue_size)
        self.subscriber.set_hwm(max_queue_size)

        self.subscriptions = {}
        self._running = False

    async def subscribe(self, topic, callback):
        self.logger.info(f"Subscribing to topic: {topic}")
        if topic not in self.subscriptions:
            self.subscriptions[topic] = []
            self.subscriber.setsockopt(zmq.SUBSCRIBE, topic.encode())
        self.subscriptions[topic].append(callback)

        if not self._running:
            self._running = True
            asyncio.create_task(self._message_loop())

    async def publish(self, topic, message):
        self.logger.debug(f"Publishing message to topic '{topic}': {message}")
        full_topic = topic.encode()
        try:
            await self.publisher.send_multipart([full_topic, msgpack.packb(message)])
            self.logger.debug(f"Successfully published message to topic '{topic}'")
        except Exception as e:
            self.logger.error(f"Error publishing message: {str(e)}")

    async def _message_loop(self):
        self.logger.info("Starting message loop")
        while self._running:
            try:
                [topic, msg] = await self.subscriber.recv_multipart()
                topic = topic.decode()
                message = msgpack.unpackb(msg)
                self.logger.debug(f"Received message on topic '{topic}': {message}")
                if topic in self.subscriptions:
                    for callback in self.subscriptions[topic]:
                        try:
                            await callback(topic, message)
                        except Exception as e:
                            self.logger.error(f"Error in subscription callback for topic '{topic}': {str(e)}")
                else:
                    self.logger.warning(f"Received message for unsubscribed topic: {topic}")
            except Exception as e:
                self.logger.error(f"Error in message loop: {str(e)}")
            await asyncio.sleep(0)
    async def stop(self):
        self._running = False
        self.publisher.close()
        self.subscriber.close()
        self.context.term()
        self.logger.info("MessageBroker stopped")