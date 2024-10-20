# web_host/main.py

import asyncio
import numpy as np
import cv2
from flask import Flask, render_template, Response, request, jsonify
import logging
import os
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import queue

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

    # Configure Flask's logger
    flask_logger = logging.getLogger('werkzeug')
    flask_logger.handlers = []
    flask_logger.addHandler(console_handler)
    if enable_logging:
        flask_logger.addHandler(file_handler)
    flask_logger.setLevel(logging.INFO)

    # Disable Flask's default logging
    logging.getLogger('werkzeug').disabled = True

    return root_logger


class Backend:
    def __init__(self, message_broker: MessageBroker):
        self.message_broker = message_broker

        self.gaze_enabled_state = False
        self.mission_state = None
        self.robot_connected_state = False

        self.available_video_topics = []
        self.latest_frame = None

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
        if isinstance(message, dict) and 'topic' in message:
            self.selected_video_topic = message['topic']
            # Publish to ModuleController
            await self.message_broker.publish("Backend/selected_video_topic_update", {"topic": self.selected_video_topic})
        else:
            self.logger.warning(f"Received invalid message format for video topic selected: {message}")

    async def handle_latest_frame_of_selected_topic(self, topic, message):
        try:
            frame = message['frame']
            await self.message_broker.publish("Backend/selected_topic_latest_frame", {"frame": frame})
            self.logger.debug("Published frame to Frontend")
        except Exception as e:
            self.logger.error(f"Error handling latest frame: {str(e)}")

    async def handle_available_video_topics(self, topic, message):

        if 'topics' in message:
            self.available_video_topics = message['topics']
            await self.message_broker.publish("Backend/available_video_topics_update", {"topics": self.available_video_topics})

    async def handle_mission_state_update(self, topic, message):
        if 'state' in message:
            self.mission_state = message['state']
            await self.message_broker.publish("Backend/mission_state_update", {"mission_state": self.mission_state})
    

class Frontend:
    def __init__(self, message_broker):
        self.message_broker = message_broker
        self.current_frame = None
        self.frame_lock = threading.Lock()
        self.state = {}
        self.logger = logging.getLogger(__name__)
        self.task_queue = queue.Queue()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_event_loop, daemon=True).start()

    def _run_event_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._process_queue())

    async def _process_queue(self):
        while True:
            try:
                task = await self.loop.run_in_executor(None, self.task_queue.get, True, 0.1)
                await task
            except queue.Empty:
                await asyncio.sleep(0.1)

    async def start(self):
        self.logger.info("Frontend started")
        await self.message_broker.subscribe("Backend/mission_state_update", self.handle_mission_state_update)
        await self.message_broker.subscribe("Backend/available_video_topics_update", self.handle_available_video_topics)
        await self.message_broker.subscribe("Backend/selected_topic_latest_frame", self.handle_video_frame)

    def index(self):
        return render_template('index.html', **self.state)

    def button_press(self):
        self.logger.debug(f"Received POST request: {request.form}")
        self.task_queue.put(self.handle_post_request(request.form))
        return jsonify({"status": "success"})

    def video_feed(self):
        return Response(self.render_frames(),
                        mimetype='multipart/x-mixed-replace; boundary=frame')

    def get_state(self):
        return jsonify(self.state)

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

    async def handle_video_frame(self, topic, message):
        try:
            compressed_frame = message['frame']
            np_arr = np.frombuffer(compressed_frame, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            with self.frame_lock:
                self.current_frame = frame
            self.logger.debug(f"Processed new frame, shape: {frame.shape}")
        except Exception as e:
            self.logger.error(f"Error handling video frame: {str(e)}")

    async def handle_mission_state_update(self, topic, message):
        if 'mission_state' in message:
            self.state['mission_state'] = message['mission_state']

    async def handle_available_video_topics(self, topic, message):
        if 'topics' in message:
            self.state['available_video_topics'] = message['topics']


    def render_frames(self):
        while True:
            with self.frame_lock:
                if self.current_frame is not None:
                    ret, buffer = cv2.imencode('.jpg', self.current_frame)
                    frame = buffer.tobytes()
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                else:
                    time.sleep(0.1)  # Short delay if no frame is available




class WebHost:
    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = port
        self.message_broker = MessageBroker(max_queue_size=10)
        self.backend = Backend(self.message_broker)
        self.frontend = Frontend(self.message_broker)
        self.app = Flask(__name__)
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("Web host starting")
        await self.backend.start()
        await self.frontend.start()
        self.setup_routes()

    def setup_routes(self):
        self.app.add_url_rule('/', 'index', self.frontend.index)
        self.app.add_url_rule('/video_feed', 'video_feed', self.frontend.video_feed)
        self.app.add_url_rule('/state', 'get_state', self.frontend.get_state)
        self.app.add_url_rule('/button_press', 'button_press', self.frontend.button_press, methods=['POST'])

    def run(self):
        self.app.run(host=self.ip, port=self.port, debug=False, use_reloader=False)



async def main(enable_logging):
    setup_logging(enable_logging)
    logger = logging.getLogger(__name__)
    logger.info("Web host starting")

    web_host = WebHost(ip='0.0.0.0', port=5000)
    await web_host.start()

    # Run Flask in a separate thread
    loop = asyncio.get_event_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    flask_future = loop.run_in_executor(executor, web_host.run)

    try:
        # Keep the main coroutine running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await web_host.message_broker.stop()
        flask_future.cancel()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true', help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))