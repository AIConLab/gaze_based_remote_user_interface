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
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import UInt8
from ugv_mission_pkg.srv import mission_commands, mission_states, mission_file_transfer
from ugv_mission_pkg.msg import MissionWaypoint, GPS


import shutil
import yaml
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path

from geometry_msgs.msg import Twist

from message_broker import MessageBroker
from enum_definitions import MissionStates, MissionCommandSignals

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
        'RosConnectionMonitor',
        'TeleopTwistHandler',
        'MissionFileHandler'
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

        self.mission_files_ready_flag = False
        self.mission_name = "mission_creation_test"
        self.ros_waypoints = []

    async def start(self):
        self.logger.info("Starting ROS Service Handler")
        try:
            # Subscribe to messages that will trigger service calls
            self.logger.debug("Setting up subscriptions...")
            
            await self.message_broker.subscribe("Backend/mission_command", self.handle_mission_command)
            await self.message_broker.subscribe("Backend/mission_state_request", self.handle_mission_state_request)
            await self.message_broker.subscribe("MissionFileHandler/mission_files_ready", self.handle_mission_files_ready)


            self.logger.debug("Subscriptions established")
        except Exception as e:
            self.logger.error(f"Failed to set up message subscriptions: {str(e)}", exc_info=True)

    async def handle_mission_command(self, topic, message):
        """Handle mission command messages by calling ROS service"""
        self.logger.debug(f"Received mission command message - Topic: {topic}, Message: {message}")
        # Received mission command message - Topic: Backend/mission_command, Message: {'command': 0}

        try:
            command_value = message["command"]

            """
            Start has special handling:
            - Check if mission file exists
            - Tell mission file handler to send mission files to the robot and start the mission
            """
            if command_value == MissionCommandSignals.MISSION_START.value:
                if not self.mission_files_ready_flag:
                    self.logger.error("Mission files not ready, cannot start mission")
                    return

                else:
                    # Create service proxy for mission file transfer
                    loop = asyncio.get_event_loop()
                    mission_file_service = rospy.ServiceProxy(
                        'mission_file_transfer',
                        mission_file_transfer
                    )

                    request = mission_file_transfer._request_class()

                    # Prepare request
                    request.mission_name = self.mission_name
                    request.waypoints = self.ros_waypoints

                    response = await loop.run_in_executor(
                        None,
                        lambda: mission_file_service(request)
                    )
                    
                    if not response.success:
                        self.logger.error(f"Mission file transfer failed: {response.message}")
                        return
                        
                    self.logger.info("Mission files transferred successfully, sending start command")

                    # Send start command
                    response = await loop.run_in_executor(
                        None, 
                        lambda: self.mission_command_proxy(command=int(MissionCommandSignals.MISSION_START.value))
                    )
                    
                    self.logger.debug(f"Mission command service response: {response}")

            else:
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

    async def handle_mission_files_ready(self, topic, message):
        try:
            if message["success"] is True:
                # Handle data, send to robot, and start mission
                self.logger.debug(f"Received mission files ready message - Topic: {topic}, Message: {message}")

                mission_data = message["mission_data"]

                self.ros_waypoints = []
                for wp_data in mission_data:
                    wp_msg = MissionWaypoint()
                    
                    # Create GPS message
                    gps_msg = GPS()
                    gps_msg.latitude = wp_data['gps']['latitude']
                    gps_msg.longitude = wp_data['gps']['longitude']
                    wp_msg.gps = gps_msg
                    
                    # Set image data
                    wp_msg.image_data = wp_data['image_data']
                    
                    self.ros_waypoints.append(wp_msg)


                self.mission_files_ready_flag = True

            else:
                self.logger.debug("Mission files not ready")
                self.mission_files_ready_flag = False
                
        except Exception as e:
            self.logger.error(f"Failed to create service proxy: {str(e)}")
            return

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
        self._node_name = f'remote_subscriber_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

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

            # Notice the "s" is removed for publishing to the message broker
            future = asyncio.run_coroutine_threadsafe(
                self.message_broker.publish("RosSubHandler/mission_state_update", message),
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


class TeleopTwistHandler:
    def __init__(self, message_broker: MessageBroker = None):
        self.message_broker = message_broker
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # Constants for velocity scaling
        self.speed = 0.5
        
        # Initialize the publisher as None - will be created in start()
        self.twist_pub = None
        self._node_name = f'teleop_twist_{datetime.now().strftime("%Y%m%d_%H%M%S")}'

    async def start(self):
        self.logger.info("Starting Teleop Twist Handler")
        
        try:
            # Initialize ROS node if needed
            if not rospy.core.is_initialized():
                rospy.init_node(self._node_name, anonymous=False)
            
            # Create the publisher for Twist messages
            self.twist_pub = rospy.Publisher('/cmd_vel', Twist, queue_size=1)
            
            # Subscribe to teleop commands from the backend
            await self.message_broker.subscribe("Backend/teleop_twist_command", self.handle_teleop_twist_command)
            
            self.logger.info("Teleop Twist Handler started successfully")
            
        except Exception as e:
            self.logger.error(f"Error starting Teleop Twist Handler: {str(e)}")
            raise

    async def handle_teleop_twist_command(self, topic, message):
        """
        Handle incoming teleop commands and publish corresponding Twist messages.
        Command format is [x, y, z, angular] where:
        - x: forward/backward velocity
        - y: left/right velocity (not used for differential drive)
        - z: up/down velocity (not used for ground robots)
        - angular: rotational velocity
        """
        self.logger.debug(f"Received teleop twist command - Topic: {topic}, Message: {message}")

        try:
            # Extract command array from message
            command = message.get('command', [0, 0, 0, 0])
            
            # Create and populate Twist message
            twist = Twist()
            
            # Linear velocities
            twist.linear.x = command[0] * self.speed  # Forward/backward
            twist.linear.y = 0.0  # Not used for differential drive
            twist.linear.z = 0.0  # Not used for ground robot
            
            # Angular velocities
            twist.angular.x = 0.0  # Not used
            twist.angular.y = 0.0  # Not used
            twist.angular.z = command[3] * self.speed  # Rotation
            
            # Publish the twist message
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.twist_pub.publish, twist)
            
            self.logger.debug(f"Published Twist message: linear.x={twist.linear.x}, angular.z={twist.angular.z}")

        except Exception as e:
            self.logger.error(f"Error handling teleop twist command: {str(e)}")

    async def stop(self):
        """Cleanup when stopping the handler"""
        try:
            if self.twist_pub:
                # Publish zero velocity before shutting down
                zero_twist = Twist()
                self.twist_pub.publish(zero_twist)
                # Unregister the publisher
                self.twist_pub.unregister()
                self.twist_pub = None
                
            self.logger.info("Teleop Twist Handler stopped")
            
        except Exception as e:
            self.logger.error(f"Error stopping Teleop Twist Handler: {str(e)}")


@dataclass
class Waypoint:
    """Data structure to hold waypoint information"""
    index: int
    latitude: float
    longitude: float
    image_path: Optional[str] = None
    
@dataclass
class Mission:
    """Data structure to hold mission information"""
    waypoints: List[Waypoint]
    
class MissionFileHandler:
    def __init__(self, message_broker=None):
        self.message_broker = message_broker
        self.logger = logging.getLogger(self.__class__.__name__)
        self.current_mission: Optional[Mission] = None
        
        # Define standard paths
        self.base_path = Path("/app_shared_data/mission_files")
        self.input_path = self.base_path / "files_to_process"
        self.output_path = self.base_path / "output"

        self.mission_data = []
        
    async def start(self):
        """Initialize the handler and create necessary directories"""
        self.logger.info("Starting Mission File Handler")
        
        # Ensure directories exist
        self.input_path.mkdir(parents=True, exist_ok=True)
        self.output_path.mkdir(parents=True, exist_ok=True)

        # Subscribe to process mission files requests
        await self.message_broker.subscribe("Backend/make_mission_files", self.handle_make_mission_files_request)

        await self.message_broker.subscribe("RosServiceHandler/prepare_mission_files", self.handle_prepare_mission_files_request)
        
    async def handle_make_mission_files_request(self, topic, message):
        self.logger.info(f"Received process mission files request - Topic: {topic}, Message: {message}")
        
        try:
            await self.process_pending_missions()

            await self.message_broker.publish("MissionFileHandler/mission_files_ready", 
                {"success": True, "mission_data": self.mission_data}
            )
            
        except Exception as e:
            self.logger.error(f"Error processing mission files: {str(e)}")

    async def handle_prepare_mission_files_request(self, topic, message):
        """Handle request to prepare mission data and send to robot
            @output: {success: bool, mission_data: dict}
        """
        self.logger.info("Handling start mission request")

        try:
            if not self.current_mission or not self.current_mission.waypoints:
                self.logger.error("No mission loaded or mission has no waypoints")
                await self.message_broker.publish(
                    "MissionFileHandler/mission_files_ready", 
                    {"success": False, "mission_data": []}
                )
                return
                
            if not self.mission_data:
                self.logger.error("No valid waypoints with images to transfer")
                await self.message_broker.publish(
                    "MissionFileHandler/mission_files_ready", 
                    {"success": False, "mission_data": []}
                )
                return

            # If we get here, we have valid waypoints with images
            self.logger.info(f"Successfully prepared {len(self.mission_data)} waypoints for transfer")
            await self.message_broker.publish(
                "MissionFileHandler/mission_files_ready", 
                {"success": True, "mission_data": self.mission_data}
            )
                
        except Exception as e:
            self.logger.error(f"Error handling start mission request: {str(e)}")
            await self.message_broker.publish(
                "MissionFileHandler/mission_files_ready", 
                {"success": False, "mission_data": []}
            )

    async def process_pending_missions(self):
        """Check for and process any pending mission files"""
        try:
            kmz_path = self.input_path / "mission.kmz"
            if kmz_path.exists():
                self.logger.info("Found mission.kmz file, processing...")
                
                # Extract waypoints from KMZ
                waypoints = await self.extract_waypoints_from_kmz(kmz_path)
                
                # Associate images with waypoints
                await self.associate_images_with_waypoints(waypoints)
                
                # Create mission structure
                self.current_mission = Mission(waypoints=waypoints)
                
                # Generate output files
                await self.generate_output_files()

                # Prepare mission data
                await self.prepare_mission_data()
                
                self.logger.info("Mission processing complete")
                
        except Exception as e:
            self.logger.error(f"Error processing mission files: {str(e)}")
            
    async def extract_waypoints_from_kmz(self, kmz_path: Path) -> List[Waypoint]:
        """Extract waypoints from KMZ file"""
        self.logger.debug(f"Extracting waypoints from {kmz_path}")
        waypoints = []
        
        try:
            # Create temporary directory for extraction
            temp_dir = self.input_path / "temp_kmz"
            temp_dir.mkdir(exist_ok=True)
            
            # Extract KMZ file
            with zipfile.ZipFile(kmz_path, 'r') as zip_ref:
                zip_ref.extractall(temp_dir)
            
            # Parse waylines.wpml
            # Use glob to find the waylines.wpml file regardless of intermediate directory name
            wpml_paths = list(temp_dir.glob("*/waylines.wpml"))
            if not wpml_paths:
                raise FileNotFoundError("waylines.wpml not found in KMZ file")
            wpml_path = wpml_paths[0]  # Take the first match
                
            tree = ET.parse(wpml_path)
            root = tree.getroot()
            
            # Find all Placemark elements (waypoints)
            ns = {'kml': 'http://www.opengis.net/kml/2.2',
                  'wpml': 'http://www.dji.com/wpmz/1.0.6'}
                  
            for placemark in root.findall('.//kml:Placemark', ns):
                # Extract index
                index = int(placemark.find('wpml:index', ns).text)
                
                # Extract coordinates
                coords = placemark.find('.//kml:coordinates', ns).text.strip().split(',')
                longitude = float(coords[0])
                latitude = float(coords[1])
                
                waypoint = Waypoint(index=index, latitude=latitude, longitude=longitude)
                waypoints.append(waypoint)
                
            # Sort waypoints by index
            waypoints.sort(key=lambda w: w.index)
            
            self.logger.info(f"Extracted {len(waypoints)} waypoints")
            
            # Cleanup temp directory
            shutil.rmtree(temp_dir)
            
            return waypoints
            
        except Exception as e:
            self.logger.error(f"Error extracting waypoints: {str(e)}")
            raise
    async def associate_images_with_waypoints(self, waypoints: List[Waypoint]):
        """Associate images from waypoint folders with waypoint objects"""
        self.logger.debug("Associating images with waypoints")
        
        try:
            for waypoint in waypoints:
                # Check for waypoint folder
                waypoint_dir = self.input_path / f"waypoint_{waypoint.index}"
                if not waypoint_dir.exists():
                    self.logger.warning(f"No image folder found for waypoint {waypoint.index}")
                    continue
                    
                # Find JPEG image in waypoint folder
                jpeg_files = list(waypoint_dir.glob("*.jpg"))
                if not jpeg_files:
                    self.logger.warning(f"No JPEG image found for waypoint {waypoint.index}")
                    continue
                    
                # Use the first JPEG found
                waypoint.image_path = str(jpeg_files[0])
                self.logger.debug(f"Associated image {waypoint.image_path} with waypoint {waypoint.index}")
                
        except Exception as e:
            self.logger.error(f"Error associating images: {str(e)}")
            raise
            
    async def generate_output_files(self):
        """Generate output files for validation"""
        if not self.current_mission:
            self.logger.warning("No mission to generate output files for")
            return
            
        try:
            # Clear existing output directory
            if self.output_path.exists():
                shutil.rmtree(self.output_path)
            self.output_path.mkdir()
            
            for waypoint in self.current_mission.waypoints:
                # Create waypoint directory
                waypoint_dir = self.output_path / f"waypoint_{waypoint.index:02d}"
                waypoint_dir.mkdir()
                
                # Create waypoint YAML file
                waypoint_data = {
                    'index': waypoint.index,
                    'latitude': waypoint.latitude,
                    'longitude': waypoint.longitude,
                    'has_image': bool(waypoint.image_path)
                }
                
                yaml_path = waypoint_dir / "waypoint.yaml"
                with open(yaml_path, 'w') as f:
                    yaml.dump(waypoint_data, f)
                    
                # Copy image if it exists
                if waypoint.image_path:
                    image_dest = waypoint_dir / f"waypoint_image_{waypoint.index:02d}.jpeg"
                    shutil.copy2(waypoint.image_path, image_dest)
                    
            self.logger.info("Output files generated successfully")
            
        except Exception as e:
            self.logger.error(f"Error generating output files: {str(e)}")
            raise
            
    async def prepare_mission_data(self):
        # Prepare mission data for transfer to robot
        try:
            # Clear existing mission data
            self.mission_data = []

            if not self.current_mission or not self.current_mission.waypoints:
                self.logger.error("No mission loaded or mission has no waypoints")
                return

            for waypoint in self.current_mission.waypoints:
                if not waypoint.image_path:
                    self.logger.warning(f"No image found for waypoint {waypoint.index}")
                    continue
                
                # Read image data
                with open(waypoint.image_path, 'rb') as f:
                    image_data = f.read()
                    
                # Create dictionary with GPS and image data
                waypoint_data = {
                    'gps': {'latitude': waypoint.latitude, 'longitude': waypoint.longitude},
                    'image_data': image_data
                }
                
                self.mission_data.append(waypoint_data)
                
            self.logger.info(f"Prepared {len(self.mission_data)} waypoints for transfer")
            
        except Exception as e:
            self.logger.error(f"Error preparing mission data: {str(e)}")
            raise
    
    async def stop(self):
        """Cleanup when stopping the handler"""
        self.logger.info("Stopping Mission File Handler")


async def main(enable_logging):
    try:
        logger = setup_logging(enable_logging)


        # Make message brokers
        logger.debug("Creating message brokers...")
        ros_service_message_broker = MessageBroker(1024)
        ros_sub_message_broker = MessageBroker(1024 * 8)
        ros_connection_monitor_message_broker = MessageBroker(1024)
        teleop_twist_message_broker = MessageBroker(1024)
        mission_file_handler_message_broker = MessageBroker(1024)
        logger.debug("Message brokers created")

        # Make handlers
        logger.debug("Creating handlers...")
        ros_service_handler = RosServiceHandler(message_broker=ros_service_message_broker)
        ros_sub_handler = RosSubHandler(message_broker=ros_sub_message_broker,
                                      image_quality=50)
        ros_connection_monitor = RosConnectionMonitor(message_broker=ros_connection_monitor_message_broker)
        teleop_twist_handler = TeleopTwistHandler(message_broker=teleop_twist_message_broker)
        mission_file_handler = MissionFileHandler(message_broker=mission_file_handler_message_broker)
        logger.debug("Handlers created")

        try:
            await asyncio.gather(
                ros_service_handler.start(),
                ros_sub_handler.start(),
                ros_connection_monitor.start(),
                teleop_twist_handler.start(),
                mission_file_handler.start()
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
        await teleop_twist_handler.stop()
        await mission_file_handler.stop()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true',
                        help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))