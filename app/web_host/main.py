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
    # Set up root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if enable_logging else logging.INFO)

    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create a StreamHandler for console output
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if enable_logging else logging.INFO)

    # Create a formatter and set it for the console handler
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)

    # Add the console handler to the root logger
    root_logger.addHandler(console_handler)

    if enable_logging:
        # Set up file logging if enabled
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"app_log_{timestamp}.log")

        # Create a RotatingFileHandler
        file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)

        # Add the file handler to the root logger
        root_logger.addHandler(file_handler)

    return root_logger


class Backend:
    def __init__(self, message_broker: MessageBroker):
        self.message_broker = message_broker

        self.processing_mode = None
        self.gaze_enabled_state = False
        self.mission_state = None
        self.robot_connected_state = False
        self.available_video_topics = []

        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("Backend started")
        
        # Front end subscriptions
        await self.message_broker.subscribe("Frontend/gaze_enabled_button_pressed", self.handle_gaze_enabled_button_pressed)
        await self.message_broker.subscribe("Frontend/mission_start_button_pressed", self.handle_mission_start_button_pressed)
        await self.message_broker.subscribe("Frontend/mission_pause_button_pressed", self.handle_mission_pause_button_pressed)
        await self.message_broker.subscribe("Frontend/mission_stop_button_pressed", self.handle_mission_stop_button_pressed)
        await self.message_broker.subscribe("Frontend/video_topic_selected", self.handle_video_topic_selected)
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
            await asyncio.sleep(1)

    async def request_mission_state(self):
        self.logger.debug("Requesting mission state")

        while self.mission_state is None:

            await self.message_broker.publish("Backend/mission_state_request", {})

            await asyncio.sleep(1)

    async def handle_gaze_enabled_button_pressed(self, topic, message):
        self.logger.debug("Gaze enabled button pressed")

        self.gaze_enabled_state = not self.gaze_enabled_state

        await self.message_broker.publish("Backend/gaze_enabled_state_update", {"gaze_enabled": self.gaze_enabled_state})

    async def handle_mission_start_button_pressed(self, topic, message):
        self.logger.debug("Mission start button pressed")
        if not self.robot_connected_state or self.mission_state is None:
            self.logger.warning(
                "Attempted to stop mission without robot connection")
            await self.message_broker.publish("Backend/error_banner", {"message": "Robot not connected or mission package not launched"})
            return

        if self.mission_state == MissionStates.IDLE:
            # Publish mission start command
            await self.message_broker.publish("MissionManager/mission_command", {"command": MissionCommandSignals.MISSION_START})

        elif self.mission_state == MissionStates.PAUSED:
            # Publish mission resume command
            await self.message_broker.publish("MissionManager/mission_command", {"command": MissionCommandSignals.MISSION_RESUME})

    async def handle_mission_pause_button_pressed(self, topic, message):
        self.logger.debug("Mission pause button pressed")
        if not self.robot_connected_state or self.mission_state is None:
            self.logger.warning(
                "Attempted to stop mission without robot connection")
            await self.message_broker.publish("Backend/error_banner", {"message": "Robot not connected or mission package not launched"})
            return

        # Mission paused can be sent from any state except Aborted or Paused
        if not self.mission_state == MissionStates.ABORTED and not self.mission_state == MissionStates.PAUSED:

            # Publish mission pause command
            await self.message_broker.publish("MissionManager/mission_command", {"command": MissionCommandSignals.MISSION_PAUSE})

    async def handle_mission_stop_button_pressed(self, topic, message):
        self.logger.debug("Mission stop button pressed")
        if not self.robot_connected_state or self.mission_state is None:
            self.logger.warning(
                "Attempted to stop mission without robot connection")

            await self.message_broker.publish("Backend/error_banner", {"message": "Robot not connected or mission package not launched"})

            return

        await self.message_broker.publish("MissionManager/mission_command", {"command": MissionCommandSignals.MISSION_ABORT})

    async def handle_video_topic_selected(self, topic, message):
        self.logger.debug(f"Received video topic selected: {message}")

        if 'topic' in message:
            self.selected_video_topic = message['topic']
            # Publish to ModuleController
            await self.message_broker.publish("Backend/selected_video_topic_update", {"topic": self.selected_video_topic})

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
        self.logger = logging.getLogger(__name__)

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

            elif "mission_start_button_pressed" in form:
                await self.publish_message("Frontend/mission_start_button_pressed", {'type': 'mission_start_button_pressed'})

            elif "mission_pause_button_pressed" in form:
                await self.publish_message("Frontend/mission_pause_button_pressed", {'type': 'mission_pause_button_pressed'})

            elif "mission_stop_button_pressed" in form:
                await self.publish_message("Frontend/mission_stop_button_pressed", {'type': 'mission_stop_button_pressed'})

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
        self.app = Quart(__name__)
        self.app.logger.setLevel(logging.WARNING)  # Set main app logger to WARNING
        
        # Disable all Quart loggers
        logging.getLogger('quart').setLevel(logging.WARNING)
        logging.getLogger('quart.app').disabled = True
        logging.getLogger('quart.serving').disabled = True
        
        # Disable access log completely
        self.app.config['LOGGER_HANDLER_POLICY'] = 'never'
        self.app.config['LOGGER_NAME'] = None
        
        self.app = cors(self.app)
        self.logger = logging.getLogger(__name__)

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
        # Replace the run_task call with this
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