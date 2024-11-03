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
from rospy import ServiceProxy
from diagnostic_msgs.msg import DiagnosticArray
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import UInt8
from ugv_mission_pkg.srv import mission_commands, mission_states  # Import the actua


from message_broker import MessageBroker
from enum_definitions import ProcessingModes, ProcessingModeActions, MissionStates

def setup_logging(enable_logging):
    # Import for thread safety
    import threading
    import sys
    
    # Force immediate flushing of stdout/stderr
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    
    # Set base log level
    base_level = logging.DEBUG if enable_logging else logging.INFO
    
    # Configure root logger first
    root_logger = logging.getLogger()
    root_logger.setLevel(base_level)
    
    # Clear any existing handlers
    root_logger.handlers.clear()
    
    # Create thread-safe formatter
    class ThreadFormatter(logging.Formatter):
        def format(self, record):
            record.thread_name = threading.current_thread().name
            return super().format(record)
    
    formatter = ThreadFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - (%(thread_name)s) - %(message)s'
    )
    
    # Create and configure stream handler with immediate flush
    class ImmediateStreamHandler(logging.StreamHandler):
        def emit(self, record):
            super().emit(record)
            self.flush()
    
    stream_handler = ImmediateStreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(base_level)
    root_logger.addHandler(stream_handler)

    # Add file handler if logging is enabled
    if enable_logging:
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"app_log_{timestamp}.log")
        
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        root_logger.addHandler(file_handler)

    # Configure specific loggers to ensure thread safety
    loggers_to_configure = [
        'RosServiceHandler',
        'RosSubHandler', 
        'RosConnectionMonitor'
    ]
    
    for logger_name in loggers_to_configure:
        logger = logging.getLogger(logger_name)
        logger.setLevel(base_level)
        # Force thread safety
        logger.handlers = []  # Clear any existing handlers
        # Add our thread-safe handlers
        logger.addHandler(stream_handler)
        if enable_logging:
            logger.addHandler(file_handler)
    
    # Create a logger for the main module
    main_logger = logging.getLogger(__name__)
    main_logger.setLevel(base_level)
    
    return main_logger


class RosServiceHandler:
    def __init__(self, message_broker: MessageBroker = None):
        self.message_broker = message_broker
        self.logger = logging.getLogger(self.__class__.__name__)
        # Store proxies to avoid recreating them for each call
        self.mission_command_proxy = ServiceProxy('mission_command', mission_commands)
        self.mission_state_proxy = ServiceProxy('mission_state_request', mission_states)

    async def start(self):
        self.logger.info("Starting ROS Service Handler")
        try:
            # Subscribe to messages that will trigger service calls
            self.logger.debug("Setting up subscriptions...")
            
            await self.message_broker.subscribe("Backend/mission_command", self.handle_mission_command)
            await self.message_broker.subscribe("Backend/mission_state_request", self.handle_mission_state_request)

            self.logger.debug("Subscriptions established")
        except Exception as e:
            self.logger.error(f"Failed to set up message subscriptions: {str(e)}", exc_info=True)

    async def handle_mission_command(self, topic, message):
        """Handle mission command messages by calling ROS service"""
        self.logger.debug(f"Received mission command message - Topic: {topic}, Message: {message}")

        try:
            command_value = message["command"]
            if hasattr(command_value, 'value'):
                command_value = command_value.value
            
            self.logger.debug(f"Calling mission_command service with command value: {command_value}")
            
            # Execute service call in executor to prevent blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: self.mission_command_proxy(command=int(command_value))
            )
            
            self.logger.debug(f"Mission command service response: {response}")
            
        except Exception as e:
            self.logger.error(f"Error calling mission_command service: {str(e)}", exc_info=True)

    async def handle_mission_state_request(self, topic, message):
        """Handle mission state request by calling ROS service"""
        self.logger.debug(f"Received state request on topic: {topic}")
        
        try:
            # Execute service call in executor to prevent blocking
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(None, self.mission_state_proxy)
            
            self.logger.debug(f"Raw state response: {response}")
            
            # Convert response to enum and publish
            state_enum = MissionStates(response.current_state)
            
            await self.message_broker.publish(
                "RosServiceHandler/current_state", 
                {"state": state_enum.name}
            )
            
            self.logger.debug(f"Published state: {state_enum.name}")
            
        except Exception as e:
            self.logger.error(f"Error calling mission_state_request service: {str(e)}", exc_info=True)

    async def stop(self):
        self.message_broker.stop()


class RosSubHandler:
    def __init__(self,
                 image_quality: int = 50,
                 message_broker: MessageBroker = None):
        
        self.image_quality = image_quality

        self.front_realsense_sub = None
        self.rear_realsense_sub = None
        self.axis_sub = None
        self.mission_state_update_sub = None

        self.message_broker = message_broker
        self.logger = logging.getLogger(self.__class__.__name__)
        self.loop = None
        self._shutdown_requested = False
        self._node_name = f'camera_subscriber_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

    async def start(self):
        # Store reference to the event loop
        self.loop = asyncio.get_running_loop()
        self._shutdown_requested = False
        
        # Initialize ROS node with unique name and anonymous=False
        try:
            if not rospy.core.is_initialized():
                rospy.init_node(self._node_name, anonymous=False)
            elif not rospy.core.get_node_uri():
                # If initialized but not registered (previous shutdown), reinitialize
                rospy.core._shutdown_flag = False
                rospy.core.set_node_uri('')
                rospy.init_node(self._node_name, anonymous=False)
        except Exception as e:
            self.logger.error(f"Error initializing ROS node: {str(e)}")
            await asyncio.sleep(1)  # Brief delay before retry
            return await self.start()
        
        self.logger.info(f"Starting ROS subscribers with node name: {self._node_name}")
        
        # Initialize subscribers with explicit queue_size and TCP_NODELAY
        self.front_realsense_sub = rospy.Subscriber(
            "/front_realsense/color/image_raw/compressed", 
            CompressedImage, 
            self.handle_front_realsense_frame,
            queue_size=1,
            tcp_nodelay=True
        )
        self.logger.info("Subscribed to front realsense topic")

        self.rear_realsense_sub = rospy.Subscriber(
            "/rear_realsense/color/image_raw/compressed", 
            CompressedImage, 
            self.handle_rear_realsense_frame,
            queue_size=1,
            tcp_nodelay=True
        )
        self.logger.info("Subscribed to rear realsense topic")

        self.axis_sub = rospy.Subscriber(
            "/axis/image_raw/compressed", 
            CompressedImage, 
            self.handle_axis_frame,
            queue_size=1,
            tcp_nodelay=True
        )
        self.logger.info("Subscribed to axis topic")

        self.mission_state_updates_sub = rospy.Subscriber("/mission_state_updates", UInt8, self.handle_mission_state_updates)
        self.logger.info("Subscribed to mission state updates topic")

        # Start monitoring task
        asyncio.create_task(self._monitor_connections())

    def handle_front_realsense_frame(self, msg):
        try:
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

    def handle_mission_state_updates(self, msg):    
        try:
            self.logger.debug("Handling mission state updates")
            state = MissionStates(msg.data)

            message = {"state": state.name}
            future = asyncio.run_coroutine_threadsafe(
                self.message_broker.publish("RosSubHandler/mission_state_updates", message),
                self.loop
            )
            future.result(timeout=1)

        except Exception as e:
            self.logger.error(f"Error in mission state updates handler: {str(e)}")

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

            return encoded_out.tobytes()

        except Exception as e:
            self.logger.error(f"Error converting ROS image to compressed jpeg: {str(e)}")
            return None

    async def _monitor_connections(self):
            while not self._shutdown_requested:
                if not rospy.core.is_initialized() or not rospy.core.get_node_uri():
                    self.logger.warning("ROS node connection lost, attempting to reconnect...")
                    await self.stop()
                    await asyncio.sleep(1)
                    await self.start()
                await asyncio.sleep(5)

    async def stop(self):
        self._shutdown_requested = True
        
        # Unregister all subscribers
        if self.front_realsense_sub:
            self.front_realsense_sub.unregister()
            self.front_realsense_sub = None
        if self.rear_realsense_sub:
            self.rear_realsense_sub.unregister()
            self.rear_realsense_sub = None
        if self.axis_sub:
            self.axis_sub.unregister()
            self.axis_sub = None
            
        # Proper ROS node shutdown
        if rospy.core.is_initialized():
            try:
                rospy.signal_shutdown(f"Stopping {self._node_name}")
                # Wait briefly for shutdown to complete
                await asyncio.sleep(1)
            except Exception as e:
                self.logger.error(f"Error during ROS shutdown: {str(e)}")
        
        self.logger.info("ROS handler stopped")


class RosConnectionMonitor:
    def __init__(self, 
                 message_broker: MessageBroker = None):

        self.robot_connected = False

        self.message_broker = message_broker
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.debug("Creating ROS Connection Monitor")
        
    async def start(self):
        self.logger.info("Starting ROS Connection Monitor")

        # Create monitoring task without node initialization
        asyncio.create_task(self.robot_connection_heartbeat())

    async def robot_connection_heartbeat(self):
        while True:
            try:
                # Get list of topics
                topics = rospy.get_published_topics()
                
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
                    self.logger.debug("Robot disconnected")
                    
            except Exception as e:

                self.logger.warning(f"Error checking robot connection: {str(e)}")
                
            await asyncio.sleep(5)

    async def stop(self):
        self.message_broker.stop()



async def main(enable_logging):
    try:
        logger = setup_logging(enable_logging)


        # Make message brokers
        logger.debug("Creating message brokers...")
        ros_service_message_broker = MessageBroker(1024)
        ros_sub_message_broker = MessageBroker(1024 * 8)
        ros_connection_monitor_message_broker = MessageBroker(1024)
        logger.debug("Message brokers created")

        # Make handlers
        logger.debug("Creating handlers...")
        ros_service_handler = RosServiceHandler(message_broker=ros_service_message_broker)
        ros_sub_handler = RosSubHandler(message_broker=ros_sub_message_broker,
                                      image_quality=50)
        ros_connection_monitor = RosConnectionMonitor(message_broker=ros_connection_monitor_message_broker)
        logger.debug("Handlers created")

        try:
            await asyncio.gather(
                ros_service_handler.start(),
                ros_sub_handler.start(),
                ros_connection_monitor.start()
            )
        except Exception as e:
            logger.error(f"Error in handler startup: {str(e)}", exc_info=True)
            raise

        logger.info("All handlers started, entering main loop")
        
        # Keep the main coroutine running
        while True:
            try:
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error in main loop: {str(e)}", exc_info=True)
                raise

    except Exception as e:
        logger.error(f"Error in main: {str(e)}", exc_info=True)
        raise e

    finally:
        logger.info("Shutting down")
        await ros_service_handler.stop()
        await ros_sub_handler.stop()
        await ros_connection_monitor.stop()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true',
                        help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))