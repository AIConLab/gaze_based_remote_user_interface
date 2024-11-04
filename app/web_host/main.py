# web_host/main.py

import logging
import os
import argparse
from datetime import datetime
from quart import Quart, render_template, request, jsonify, Response, make_response
from quart_cors import cors
import asyncio
import numpy as np
import hypercorn.asyncio
import hypercorn.config

from enum_definitions import MissionStates, MissionCommandSignals, ProcessingModes, ProcessingModeActions
from message_broker import MessageBroker

from logging.handlers import RotatingFileHandler


def setup_logging(enable_logging):
    """Configure logging with explicit handler removal and stream sync"""
    # Force flush on all handlers
    import sys
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    
    # Set base log level
    base_level = logging.DEBUG if enable_logging else logging.INFO
    
    # Configure root logger first
    root_logger = logging.getLogger()
    root_logger.setLevel(base_level)
    
    # Clear any existing handlers from all loggers
    loggers = [logging.getLogger(name) for name in logging.root.manager.loggerDict]
    for logger in loggers + [root_logger]:
        logger.handlers.clear()
    
    # Create formatters with more detailed information
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s'
    )
    
    # Create and configure stream handler with explicit buffering
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.setLevel(base_level)
    stream_handler.set_name('console_handler')  # Named handler for identification
    root_logger.addHandler(stream_handler)

    # Add file handler if logging is enabled
    if enable_logging:
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"app_log_{timestamp}.log")
        
        file_handler = logging.FileHandler(log_file, mode='a')
        file_handler.setFormatter(formatter)
        file_handler.setLevel(logging.DEBUG)
        file_handler.set_name('file_handler')  # Named handler for identification
        root_logger.addHandler(file_handler)

    # Prevent ROS double logging but keep our logs
    ros_logger = logging.getLogger('rosout')
    ros_logger.propagate = False
    ros_logger.addHandler(stream_handler)  # Ensure ROS logs still go to console
    
    # Configure specific loggers with explicit handlers
    loggers_to_configure = [
        'RosServiceHandler',
        'RosSubHandler', 
        'RosConnectionMonitor',
        'MessageBroker'  # Add message broker logging
    ]
    
    # Ensure each logger has the right configuration
    for logger_name in loggers_to_configure:
        logger = logging.getLogger(logger_name)
        logger.setLevel(base_level)
        logger.propagate = True  # Ensure propagation to root
        # Clear and re-add handlers to ensure clean state
        logger.handlers.clear()
        
    # Create a logger for the main module
    main_logger = logging.getLogger(__name__)
    main_logger.setLevel(base_level)
    
    # Log initial configuration to verify
    main_logger.debug("Logging configuration completed")
    main_logger.debug(f"Root logger level: {logging.getLevelName(root_logger.getEffectiveLevel())}")
    main_logger.debug(f"Handler count: {len(root_logger.handlers)}")
    
    return main_logger


class Backend:
    def __init__(self, message_broker: MessageBroker):
        self.message_broker = message_broker

        self.processing_mode = None
        self.gaze_enabled_state = False
        self.mission_state = None
        self.robot_connected_state = False
        self.available_video_topics = []

        self.logger = logging.getLogger(self.__class__.__name__)

    async def start(self):
        self.logger.info("Backend started")
        
        # Front end subscriptions
        await self.message_broker.subscribe("Frontend/gaze_enabled_button_pressed", self.handle_gaze_enabled_button_pressed)
        await self.message_broker.subscribe("Frontend/mission_start_button_pressed", self.handle_mission_start_button_pressed)
        await self.message_broker.subscribe("Frontend/mission_pause_button_pressed", self.handle_mission_pause_button_pressed)
        await self.message_broker.subscribe("Frontend/mission_abort_button_pressed", self.handle_mission_abort_button_pressed)
        await self.message_broker.subscribe("Frontend/video_topic_selected", self.handle_video_topic_selected)
        await self.message_broker.subscribe("Frontend/mission_resume_button_pressed", self.handle_mission_resume_button_pressed)
        await self.message_broker.subscribe("Frontend/teleop_twist_button_pressed", self.handle_teleop_twist_button_pressed)

        await self.message_broker.subscribe("Frontend/process_action_button_pressed", self.handle_processing_mode_action)

        """
        Note: We are subscribing to the latest frame from both UI Renderer and Video Renderer as only one of them will be active at a time.

        This is controlled by the Processing Mode in the ModuleProcessor.
        """
        # UI Renderer subscriptions
        await self.message_broker.subscribe("UserInteractionRenderer/latest_rendered_frame", self.handle_latest_frame)

        # Video Renderer subscriptions
        await self.message_broker.subscribe("VideoRender/latest_rendered_frame", self.handle_latest_frame)

        # ModuleController subscriptions
        await self.message_broker.subscribe("ModuleController/processing_mode_update", self.handle_processing_mode_update)

        # ModuleDatapath subscriptions
        await self.message_broker.subscribe("ModuleDatapath/available_video_topics", self.handle_available_video_topics)

        # MissionManager subscriptions
        await self.message_broker.subscribe("RosConnectionMonitor/connection_status_update", self.handle_robot_connection_status_update)
        await self.message_broker.subscribe("RosServiceHandler/current_state", self.handle_mission_state_update)
        await self.message_broker.subscribe("RosSubHandler/mission_state_update", self.handle_mission_state_update)

        # Initial publisher
        # Loop until processing mode is set, this is in case Backend initializes before ModuleProcessor
        asyncio.create_task(self.initialize_app_state())

        # Initialize the robot mission state by requesting service
        asyncio.create_task(self.request_mission_state())

    async def initialize_app_state(self): 

        while self.processing_mode is None:
            # Send just name so string can be serialized
            await self.message_broker.publish("Backend/processing_mode_action", {"action": ProcessingModeActions.CANCEL.name})

            # Add delay to prevent flooding message
            await asyncio.sleep(3)

    async def request_mission_state(self):
        self.logger.debug("Requesting mission state")

        while self.mission_state is None:
            await self.message_broker.publish("Backend/mission_state_request", {})

            await asyncio.sleep(3)

    async def handle_gaze_enabled_button_pressed(self, topic, message):
        self.logger.debug("Gaze enabled button pressed")

        self.gaze_enabled_state = not self.gaze_enabled_state

        await self.message_broker.publish("Backend/gaze_enabled_state_update", {"gaze_enabled": self.gaze_enabled_state})

    async def handle_mission_start_button_pressed(self, topic, message):
        self.logger.debug("Mission start button pressed")

        await self.message_broker.publish("Backend/mission_command", {"command": MissionCommandSignals.MISSION_START.value})

    async def handle_mission_pause_button_pressed(self, topic, message):
        self.logger.debug("Mission pause button pressed")

        await self.message_broker.publish("Backend/mission_command", {"command": MissionCommandSignals.MISSION_PAUSE.value})

    async def handle_mission_abort_button_pressed(self, topic, message):
        self.logger.debug("Mission abort button pressed")

        await self.message_broker.publish("Backend/mission_command", {"command": MissionCommandSignals.MISSION_ABORT.value})

    async def handle_mission_resume_button_pressed(self, topic, message):
        self.logger.debug("Mission resume button pressed")

        await self.message_broker.publish("Backend/mission_command", {"command": MissionCommandSignals.MISSION_RESUME.value})

    async def handle_video_topic_selected(self, topic, message):
        self.logger.debug(f"Received video topic selected: {message}")

        if 'topic' in message:
            self.selected_video_topic = message['topic']
            # Publish to ModuleController
            await self.message_broker.publish("Backend/selected_video_topic_update", {"topic": self.selected_video_topic})

    async def handle_teleop_twist_button_pressed(self, topic, message):
        self.logger.info(f"Received teleop twist button pressed: {message}")

        await self.message_broker.publish("Backend/teleop_twist_command", {"command": message['command']})

    async def handle_latest_frame(self, topic, message):
        frame = message['frame']

        await self.message_broker.publish("Backend/latest_frame", {"frame": frame})

    async def handle_available_video_topics(self, topic, message):
        if 'topics' in message:
            self.available_video_topics = message['topics']
            await self.message_broker.publish("Backend/available_video_topics_update", {"topics": self.available_video_topics})

    async def handle_processing_mode_update(self, topic, message):
        
        self.processing_mode = message['mode']

        await self.message_broker.publish("Backend/processing_mode_update", {"mode": self.processing_mode})
        
        self.logger.debug(f"Backend: Processing mode updated: {self.processing_mode}")

    async def handle_processing_mode_action(self, topic, message):
        action = message['action']

        await self.message_broker.publish("Backend/processing_mode_action", {"action": action})

        self.logger.debug(f"Backend: Processing mode action: {action}")

    async def handle_robot_connection_status_update(self, topic, message):
        self.logger.debug(f"Received robot connection status update: {message}")

        if message['connected'] != self.robot_connected_state:
            self.robot_connected_state = message['connected']

            await self.message_broker.publish("Backend/robot_connection_status", {"connected": self.robot_connected_state})

    async def handle_mission_state_update(self, topic, message):
        # Incoming message state is a string already
        self.logger.info(f"Received mission state update: {message}")
        self.mission_state = message['state']

        await self.message_broker.publish("Backend/mission_state_update", {"mission_state": self.mission_state})

    async def stop(self):
        self.logger.info("Backend stopping")
        self.message_broker.stop()


class Frontend:
    def __init__(self, message_broker):
        self.message_broker = message_broker
        self.current_frame = None
        self.frame_buffer = asyncio.Queue(maxsize=5)
        self.frame_lock = asyncio.Lock()
        self.frame_available = asyncio.Event()
        self.state = {}
        self.logger = logging.getLogger(self.__class__.__name__)

    async def start(self):
        self.logger.info("Frontend started")

        # Backend subscriptions
        await self.message_broker.subscribe("Backend/mission_state_update", self.handle_mission_state_update)
        await self.message_broker.subscribe("Backend/available_video_topics_update", self.handle_available_video_topics)
        await self.message_broker.subscribe("Backend/latest_frame", self.handle_video_frame)
        await self.message_broker.subscribe("Backend/processing_mode_update", self.handle_processing_mode_update)
        await self.message_broker.subscribe("Backend/robot_connection_status", self.handle_robot_connection_status)

    async def index(self):
        return await render_template('index.html', **self.state)

    async def button_press(self):
        form_data = await request.form
        self.logger.debug(f"Received POST request: {form_data}")
        await self.handle_post_request(form_data)
        return jsonify({"status": "success"})

    async def get_state(self):
        return jsonify(self.state)

    async def handle_video_frame(self, topic, message):
        try:
            # If buffer is full, remove oldest frame
            if self.frame_buffer.full():
                try:
                    self.frame_buffer.get_nowait()
                except asyncio.QueueEmpty:
                    pass

            await self.frame_buffer.put(message['frame'])

        except Exception as e:
            self.logger.error(f"Error handling video frame: {str(e)}")

    async def handle_mission_state_update(self, topic, message):
        if 'mission_state' in message:
            self.state['mission_state'] = message['mission_state']

    async def handle_available_video_topics(self, topic, message):
        if 'topics' in message:
            self.state['available_video_topics'] = message['topics']

    async def handle_processing_mode_update(self, topic, message):
        # Processing mode message is a string
        mode = message['mode']

        self.state['processing_mode'] = mode
        self.logger.debug(f"Frontend: Processing mode updated: {mode}")

    async def video_feed(self):
        async def generate():
            while True:
                try:
                    # Wait for frame with timeout
                    frame_data = await asyncio.wait_for(self.frame_buffer.get(), timeout=1.0)

                    
                    yield (b'--frame\r\n'
                            b'Content-Type: image/jpeg\r\n\r\n' + frame_data + b'\r\n')
                
                except asyncio.TimeoutError:
                    # Just continue waiting for next frame
                    continue
                    

        response = await make_response(
            generate(),
            {
                'Content-Type': 'multipart/x-mixed-replace; boundary=frame',
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0',
            }
        )
        response.timeout = None  # Disable timeout for streaming
        return response

    async def publish_message(self, topic, message):
        self.logger.debug(f"Publishing message to topic '{topic}': {message}")
        try:
            await self.message_broker.publish(topic, message)
            self.logger.debug(f"Successfully published message to topic '{topic}'")
        except Exception as e:
            self.logger.error(f"Error publishing message to topic '{topic}': {str(e)}")
            raise

    async def handle_post_request(self, form):
        self.logger.debug(f"Handling POST request: {form}")
        try:
            if "gaze_button_pressed" in form:
                await self.publish_message("Frontend/gaze_enabled_button_pressed", {'type': 'gaze_enabled_button_pressed'})

            elif "mission_resume_button_pressed" in form:
                await self.publish_message("Frontend/mission_resume_button_pressed", {'type': 'mission_resume_button_pressed'})

            elif "mission_start_button_pressed" in form:
                await self.publish_message("Frontend/mission_start_button_pressed", {'type': 'mission_start_button_pressed'})

            elif "mission_pause_button_pressed" in form:
                await self.publish_message("Frontend/mission_pause_button_pressed", {'type': 'mission_pause_button_pressed'})

            elif "mission_abort_button_pressed" in form:
                await self.publish_message("Frontend/mission_abort_button_pressed", {'type': 'mission_abort_button_pressed'})

            elif "video_topic_selected" in form:
                selected_topic = form.get("video_topic_selected")
                await self.publish_message("Frontend/video_topic_selected", {'topic': selected_topic})

            elif "process_action_button_pressed" in form:
                action = form.get("process_action_button_pressed")  # Match exactly
                action_enum = ProcessingModeActions[action]
                await self.publish_message(
                    "Frontend/process_action_button_pressed", 
                    {'action': action_enum.name}
                )

            elif "teleop_toggle_pressed" in form:
                # Dont need to do anything for now since this button just cause the teleop control to disapear from the front end ui
                pass

            elif "teleop_button_pressed" in form:
                button_id = form.get("teleop_button_pressed")
                # Map button IDs to movement commands [x, y, z, angular]
                teleop_commands = {
                    'teleop-forward': [1, 0, 0, 0],      # Forward
                    'teleop-forward-right': [1, 0, 0, -1],  # Forward-Right
                    'teleop-right': [0, 0, 0, -1],       # Right
                    'teleop-back-right': [-1, 0, 0, -1], # Back-Right
                    'teleop-back': [-1, 0, 0, 0],        # Back
                    'teleop-back-left': [-1, 0, 0, 1],   # Back-Left
                    'teleop-left': [0, 0, 0, 1],         # Left
                    'teleop-forward-left': [1, 0, 0, 1], # Forward-Left
                    'teleop-stop': [0, 0, 0, 0],         # Stop
                }
                
                command = teleop_commands.get(button_id, [0, 0, 0, 0])  # Default to stop if unknown button
                await self.publish_message("Frontend/teleop_twist_button_pressed", {'command': command})

            else:
                self.logger.warning(f"Unknown POST request: {form}")

        except Exception as e:
            self.logger.error(f"Error handling POST request: {str(e)}")

    async def handle_robot_connection_status(self, topic, message):
        self.state['robot_connected'] = message['connected']

    async def stop(self):
        self.logger.info("Frontend stopping")
        self.message_broker.stop()


class WebHost:
    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = port
        self.frontend_message_broker = MessageBroker(1024)
        self.backend_message_broker = MessageBroker(1024)

        self.backend = Backend(self.backend_message_broker)
        self.frontend = Frontend(self.frontend_message_broker)
        
        # Create Quart app with logging disabled
        self.app = Quart(self.__class__.__name__)
        self.app.logger.setLevel(logging.WARNING)  # Set main app logger to WARNING
        
        # Disable all Quart loggers
        logging.getLogger('quart').setLevel(logging.WARNING)
        logging.getLogger('quart.app').disabled = True
        logging.getLogger('quart.serving').disabled = True
        
        # Disable access log completely
        self.app.config['LOGGER_HANDLER_POLICY'] = 'never'
        self.app.config['LOGGER_NAME'] = None
        
        self.app = cors(self.app)
        self.logger = logging.getLogger(self.__class__.__name__)

    async def start(self):
        self.logger.info("Web host starting")
        await self.frontend.start()
        self.setup_routes()
        await self.backend.start()

    def setup_routes(self):
        self.app.route('/')(self.frontend.index)
        self.app.route('/video_feed')(self.frontend.video_feed)
        self.app.route('/state')(self.frontend.get_state)
        self.app.route('/button_press', methods=['POST'])(self.frontend.button_press)

    def run(self):
        self.app.run(host=self.ip, port=self.port, debug=False, use_reloader=False)

    def stop(self):
        self.frontend.stop()
        self.backend.stop()


async def main(enable_logging):
    setup_logging(enable_logging)
    logger = logging.getLogger(__name__)
    logger.info("Web host starting")

    web_host = WebHost(ip='0.0.0.0', port=5000)
    await web_host.start()

    try:
        config = hypercorn.Config()
        config.bind = [f"{web_host.ip}:{web_host.port}"]
        config.access_log_format = ""  # Disable access logs
        config.accesslog = None
        config.errorlog = None
        
        await hypercorn.asyncio.serve(web_host.app, config)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await web_host.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true', help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))