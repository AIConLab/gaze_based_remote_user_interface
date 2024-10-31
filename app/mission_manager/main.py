import asyncio
import numpy as np
import zmq
import zmq.asyncio
import msgpack
import cv2
import logging
import os
import argparse
from datetime import datetime

import rospy
from diagnostic_msgs.msg import DiagnosticArray
from sensor_msgs.msg import CompressedImage


from message_broker import MessageBroker
from enum_definitions import ProcessingModes, ProcessingModeActions


def setup_logging(enable_logging):
    if enable_logging:
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )

    if enable_logging:
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"app_log_{timestamp}.log")
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logging.getLogger().addHandler(file_handler)

    # Set the level for specific loggers
    for logger_name in ['RosPubHandler', 'RosSubHandler', 'RemoteConnectionHealthMonitor']:
        logging.getLogger(logger_name).setLevel(log_level)


class RosPubHandler:
    def __init__(self,
                 message_broker: MessageBroker = None):

        self.message_broker = message_broker
        self.logger = logging.getLogger(__name__)

    async def start(self):
        pass

    async def stop(self):
        pass


class RosSubHandler:
    def __init__(self,
                 image_quality: int = 50,
                 message_broker: MessageBroker = None):
        
        self.image_quality = image_quality
        self.front_realsense_sub = None
        self.rear_realsense_sub = None
        self.axis_sub = None
        self.message_broker = message_broker
        self.logger = logging.getLogger(__name__)
        self.loop = None

    async def start(self):
        # Store reference to the event loop
        self.loop = asyncio.get_running_loop()
        
        # Initialize ROS node
        try:
            rospy.init_node('camera_subscriber', anonymous=True)
        except rospy.ROSException:
            self.logger.info("ROS node already initialized")
        
        self.logger.info("Starting ROS subscribers...")
        
        # Initialize subscribers
        self.front_realsense_sub = rospy.Subscriber(
            "/front_realsense/color/image_raw/compressed", 
            CompressedImage, 
            self.handle_front_realsense_frame,
            queue_size=1
        )
        self.logger.info("Subscribed to front realsense topic")

        self.rear_realsense_sub = rospy.Subscriber(
            "/rear_realsense/color/image_raw/compressed", 
            CompressedImage, 
            self.handle_rear_realsense_frame,
            queue_size=1
        )
        self.logger.info("Subscribed to rear realsense topic")

        self.axis_sub = rospy.Subscriber(
            "/axis/image_raw/compressed", 
            CompressedImage, 
            self.handle_axis_frame,
            queue_size=1
        )
        self.logger.info("Subscribed to axis topic")

    def handle_front_realsense_frame(self, msg):
        try:
            self.logger.debug("Handling front realsense frame")
            compressed_jpeg = self.convert_ros_image_to_compressed_jpeg(msg)
            if compressed_jpeg:
                message = {"frame": compressed_jpeg}
                future = asyncio.run_coroutine_threadsafe(
                    self.message_broker.publish("RosSubHandler/front_realsense_frame", message),
                    self.loop
                )
                future.result()
        except Exception as e:
            self.logger.error(f"Error in front realsense handler: {str(e)}")

    def handle_rear_realsense_frame(self, msg):
        try:
            self.logger.debug("Handling rear realsense frame")
            compressed_jpeg = self.convert_ros_image_to_compressed_jpeg(msg)
            if compressed_jpeg:
                message = {"frame": compressed_jpeg}
                future = asyncio.run_coroutine_threadsafe(
                    self.message_broker.publish("RosSubHandler/rear_realsense_frame", message),
                    self.loop
                )
                future.result()
        except Exception as e:
            self.logger.error(f"Error in rear realsense handler: {str(e)}")

    def handle_axis_frame(self, msg):
        try:
            self.logger.debug("Handling axis frame")
            compressed_jpeg = self.convert_ros_image_to_compressed_jpeg(msg)
            if compressed_jpeg:
                message = {"frame": compressed_jpeg}
                future = asyncio.run_coroutine_threadsafe(
                    self.message_broker.publish("RosSubHandler/axis_frame", message),
                    self.loop
                )
                future.result()
        except Exception as e:
            self.logger.error(f"Error in axis handler: {str(e)}")

    def convert_ros_image_to_compressed_jpeg(self, msg):
        try:
            frame = cv2.imdecode(np.frombuffer(msg.data, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                self.logger.error("Failed to decode image data")
                return None

            _, encoded_out = cv2.imencode('.jpeg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.image_quality])
            if not _:
                self.logger.error("Failed to encode image to JPEG")
                return None

            self.logger.debug("Successfully converted ROS image to compressed JPEG")
            return encoded_out.tobytes()

        except Exception as e:
            self.logger.error(f"Error converting ROS image to compressed jpeg: {str(e)}")
            return None

    async def stop(self):
        if self.front_realsense_sub:
            self.front_realsense_sub.unregister()
        if self.rear_realsense_sub:
            self.rear_realsense_sub.unregister()
        if self.axis_sub:
            self.axis_sub.unregister()
        self.logger.info("Unregistered all subscribers")

class RosConnectionMonitor:
    def __init__(self, 
                 message_broker: MessageBroker = None):

        self.robot_connected = False


        self.message_broker = message_broker
        self.logger = logging.getLogger(__name__)
        
    async def start(self):
        self.logger.info("Starting ROS Connection Monitor")
        # Create monitoring task without node initialization
        asyncio.create_task(self.robot_connection_heartbeat())


    async def robot_connection_heartbeat(self):
        while True:
            try:
                # Get list of topics
                topics = rospy.get_published_topics()
                self.logger.debug(f"Available topics: {topics}")
                
                # Check if diagnostic topic exists
                diagnostic_exists = any(topic[0] == '/diagnostics' for topic in topics)
                
                if diagnostic_exists:
                    self.robot_connected = True
                    await self.message_broker.publish(
                        "RosConnectionMonitor/connection_status_update",
                        {"connected": True}
                    )
                    self.logger.debug("Robot connected")

                elif not diagnostic_exists:
                    self.robot_connected = False

                    await self.message_broker.publish(
                        "RosConnectionMonitor/connection_status_update",
                        {"connected": False}
                    )
                    self.logger.warning("Robot disconnected")
                    
            except Exception as e:

                self.logger.warning(f"Error checking robot connection: {str(e)}")
                
            await asyncio.sleep(5)

    async def stop(self):
        self._shutdown_flag = True



async def main(enable_logging):
    try:
        setup_logging(enable_logging)
        logger = logging.getLogger(__name__)

        # Make message brokers
        ros_pub_message_broker = MessageBroker(1024 * 6)
        ros_sub_message_broker = MessageBroker(1024)
        ros_connection_monitor_message_broker = MessageBroker(1024)

        # Make handlers
        ros_pub_handler = RosPubHandler(ros_pub_message_broker)
        ros_sub_handler = RosSubHandler(message_broker=ros_sub_message_broker,
                                        image_quality=50)
        ros_connection_monitor = RosConnectionMonitor(ros_connection_monitor_message_broker)

        await asyncio.gather(
            ros_pub_handler.start(),
            ros_sub_handler.start(),
            ros_connection_monitor.start()
        )

        # Keep the main coroutine running
        while True:
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        raise e

    finally:
        ros_pub_handler.stop()
        ros_sub_handler.stop()
        ros_connection_monitor.stop()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true',
                        help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))