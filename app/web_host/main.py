# web_host/main.py

import logging
import os
import argparse
from datetime import datetime
from quart import Quart, render_template, request, jsonify, Response, make_response
from quart_cors import cors
import asyncio
import cv2
import numpy as np

from enum_definitions import MissionStates, MissionCommandSignals
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

    # Configure Quart's logger
    quart_logger = logging.getLogger('quart.app')
    quart_logger.setLevel(logging.DEBUG if enable_logging else logging.INFO)
    quart_logger.propagate = True

    # Disable Quart's default access log
    logging.getLogger('quart.serving').setLevel(logging.WARNING)

    return root_logger


    return root_logger


class Backend:
    def __init__(self, message_broker: MessageBroker):
        self.message_broker = message_broker

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

        # Video Renderer subscriptions
        await self.message_broker.subscribe("VideoRender/latest_rendered_frame", self.handle_latest_frame_of_selected_topic)

        # ModuleDatapath subscriptions
        await self.message_broker.subscribe("ModuleDatapath/available_video_topics", self.handle_available_video_topics)

        # MissionManager subscriptions
        await self.message_broker.subscribe("MissionManager/mission_state_update", self.handle_mission_state_update)

    async def handle_gaze_enabled_button_pressed(self, topic, message):
        self.logger.info("Gaze enabled button pressed")

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

    async def handle_latest_frame_of_selected_topic(self, topic, message):
        frame = message['frame']

        await self.message_broker.publish("Backend/selected_topic_latest_frame", {"frame": frame})

    async def handle_available_video_topics(self, topic, message):

        if 'topics' in message:
            self.available_video_topics = message['topics']
            await self.message_broker.publish("Backend/available_video_topics_update", {"topics": self.available_video_topics})

    async def handle_mission_state_update(self, topic, message):
        if 'state' in message:
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
        await self.message_broker.subscribe("Backend/mission_state_update", self.handle_mission_state_update)
        await self.message_broker.subscribe("Backend/available_video_topics_update", self.handle_available_video_topics)
        await self.message_broker.subscribe("Backend/selected_topic_latest_frame", self.handle_video_frame)

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

    async def video_feed(self):
        self.logger.info("Video feed requested")

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
                self.logger.debug(f"Video topic selected: {selected_topic}")
                await self.publish_message("Frontend/video_topic_selected", {'topic': selected_topic})

            else:
                self.logger.warning(f"Unknown POST request: {form}")
        except Exception as e:
            self.logger.error(f"Error handling POST request: {str(e)}")

    async def handle_mission_state_update(self, topic, message):
        if 'mission_state' in message:
            self.state['mission_state'] = message['mission_state']

    async def handle_available_video_topics(self, topic, message):
        if 'topics' in message:
            self.state['available_video_topics'] = message['topics']


class WebHost:
    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = port
        self.front_end_message_broker = MessageBroker(1024)
        self.backend_message_broker = MessageBroker(1024)

        self.backend = Backend(self.backend_message_broker)
        self.frontend = Frontend(self.front_end_message_broker)
        self.app = Quart(__name__)
        self.app = cors(self.app)
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("Web host starting")
        await self.backend.start()
        await self.frontend.start()
        self.setup_routes()

    def setup_routes(self):
        self.app.route('/')(self.frontend.index)
        self.app.route('/video_feed')(self.frontend.video_feed)
        self.app.route('/state')(self.frontend.get_state)
        self.app.route('/button_press', methods=['POST'])(self.frontend.button_press)

    def run(self):
        self.app.run(host=self.ip, port=self.port, debug=False, use_reloader=False)

    def stop(self):
        self.front_end.stop()
        self.backend.stop()


async def main(enable_logging):
    setup_logging(enable_logging)
    logger = logging.getLogger(__name__)
    logger.info("Web host starting")

    web_host = WebHost(ip='0.0.0.0', port=5000)
    await web_host.start()

    try:
        await web_host.app.run_task(host=web_host.ip, port=web_host.port)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await web_host.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true', help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))