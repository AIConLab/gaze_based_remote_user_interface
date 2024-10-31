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
                 message_broker: MessageBroker = None):

        self.message_broker = message_broker
        self.logger = logging.getLogger(__name__)

    async def start(self):
        pass

    async def stop(self):
        pass


class RosConnectionMonitor:
    def __init__(self, message_broker: MessageBroker = None):
        self.message_broker = message_broker
        self.logger = logging.getLogger(__name__)
        self._shutdown_flag = False
        self.robot_connected = False
        
    async def check_robot_connection(self):
        while not self._shutdown_flag:
            try:
                # Get list of topics
                topics = rospy.get_published_topics()
                self.logger.debug(f"Available topics: {topics}")
                
                # Check if diagnostic topic exists
                diagnostic_exists = any(topic[0] == '/diagnostics' for topic in topics)
                
                if diagnostic_exists and not self.robot_connected:
                    self.robot_connected = True
                    await self.message_broker.publish(
                        "RosConnectionMonitor/connection_status",
                        {"connected": True}
                    )
                    self.logger.info("Robot connected")
                elif not diagnostic_exists and self.robot_connected:
                    self.robot_connected = False
                    await self.message_broker.publish(
                        "RosConnectionMonitor/connection_status",
                        {"connected": False}
                    )
                    self.logger.warning("Robot disconnected")
                    
            except Exception as e:
                if self.robot_connected:
                    self.robot_connected = False
                    await self.message_broker.publish(
                        "RosConnectionMonitor/connection_status",
                        {"connected": False}
                    )
                self.logger.warning(f"Error checking robot connection: {str(e)}")
                
            await asyncio.sleep(1)

    async def start(self):
        self.logger.info("Starting ROS Connection Monitor")
        # Create monitoring task without node initialization
        asyncio.create_task(self.check_robot_connection())

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
        ros_sub_handler = RosSubHandler(ros_sub_message_broker)
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