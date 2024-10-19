import zmq
import zmq.asyncio
import msgpack
import logging
import os

class MessageBroker:
    def __init__(self, max_queue_size=10):
        self.context = zmq.asyncio.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.subscriber = self.context.socket(zmq.SUB)
        self.logger = logging.getLogger(__name__)

        self.zmq_pub_address = os.environ.get('ZMQ_PUB_ADDRESS', 'tcp://zmq_proxy:5559')
        self.zmq_sub_address = os.environ.get('ZMQ_SUB_ADDRESS', 'tcp://zmq_proxy:5560')

        self.publisher.connect(self.zmq_pub_address)
        self.subscriber.connect(self.zmq_sub_address)

        # Set high water mark for the publisher
        self.publisher.set_hwm(max_queue_size)

    async def publish(self, topic, message):
        full_topic = topic.encode()
        try:
            await self.publisher.send_multipart([full_topic, msgpack.packb(message)], zmq.NOBLOCK)
        except zmq.ZMQError as e:
            if e.errno == zmq.EAGAIN:
                self.logger.warning(f"High water mark reached. Dropping message for topic: {topic}")
            else:
                raise

    async def subscribe(self, topic, callback):
        self.subscriber.setsockopt(zmq.SUBSCRIBE, topic.encode())
        while True:
            try:
                [topic, msg] = await self.subscriber.recv_multipart()
                await callback(topic.decode(), msgpack.unpackb(msg))
            except Exception as e:
                self.logger.error(f"Error in subscription callback: {str(e)}")