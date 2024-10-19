# web_host/main.py

import asyncio
import numpy as np
import zmq
import zmq.asyncio
import msgpack
from flask import Flask, render_template, Response, request, jsonify
import cv2
import logging
import os
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from collections import deque

# Following defined in app/app_shared_data
from enum_definitions import MissionStates, MissionCommandSignals
from message_broker import MessageBroker


def setup_logging(enable_logging):
    if enable_logging:
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"app_log_{timestamp}.log")

        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
    else:
        logging.basicConfig(level=logging.ERROR)


class Backend:
    def __init__(self, video_buffer_length: int = 10, video_frame_rate: int = 15, message_broker: MessageBroker = None):
        self.message_broker = message_broker

        self.gaze_enabled_state = False
        self.mission_state = None
        self.robot_connected_state = False

        self.available_video_topics = []
        self.selected_video_topic = None
        self.video_frame_rate = video_frame_rate
        self.video_buffer = deque(maxlen=video_buffer_length)
        self.latest_frame = None

        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("Backend started")


        # Front end subscriptions
        asyncio.create_task(self.message_broker.subscribe(
            "Frontend/gaze_enabled_button_pressed", self.handle_gaze_enabled_button_pressed))
        asyncio.create_task(self.message_broker.subscribe(
            "Frontend/mission_start_button_pressed", self.handle_mission_start_button_pressed))

        asyncio.create_task(self.message_broker.subscribe(
            "Frontend/mission_pause_button_pressed", self.handle_mission_pause_button_pressed))

        asyncio.create_task(self.message_broker.subscribe(
            "Frontend/mission_stop_button_pressed", self.handle_mission_stop_button_pressed))

        asyncio.create_task(self.message_broker.subscribe(
            "Frontend/video_topic_selected", self.handle_video_topic_selected))

        # Video Renderer subscriptions
        asyncio.create_task(self.message_broker.subscribe(
            "VideoRender/latest_rendered_frame", self.handle_latest_frame_of_selected_topic))

        # ModuleDatapath subscriptions
        asyncio.create_task(self.message_broker.subscribe(
            "ModuleDatapath/available_video_topics", self.handle_available_video_topics))

        # MissionManager subscriptions
        asyncio.create_task(self.message_broker.subscribe(
            "MissionManager/mission_state_update", self.handle_mission_state_update))

    async def handle_gaze_enabled_button_pressed(self, topic, message):
        self.gaze_enabled_state = not self.gaze_enabled_state

        await self.message_broker.publish("Backend/gaze_enabled_state_update", {"gaze_enabled": self.gaze_enabled_state})

    async def handle_mission_start_button_pressed(self, topic, message):
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
        if not self.robot_connected_state or self.mission_state is None:
            self.logger.warning(
                "Attempted to stop mission without robot connection")

            await self.message_broker.publish("Backend/error_banner", {"message": "Robot not connected or mission package not launched"})

            return

        await self.message_broker.publish("MissionManager/mission_command", {"command": MissionCommandSignals.MISSION_ABORT})

    async def handle_video_topic_selected(self, topic, message):
        """
        Callback for when a video topic is selected by the user, publishes the selected topic to VideoManager and Frontend
        """
        if isinstance(message, dict) and 'topic' in message:
            self.selected_video_topic = message['topic']
            # Publish to ModuleController
            await self.message_broker.publish("Backend/selected_video_topic_update", {"topic": self.selected_video_topic})
        else:
            self.logger.warning(f"Received invalid message format for video topic selected: {message}")

    async def handle_latest_frame_of_selected_topic(self, topic, message):
        try:
            await self.message_broker.publish("Backend/selected_topic_latest_frame", {"frame": message['frame']})

        except Exception as e:
            pass

    async def handle_available_video_topics(self, topic, message):
        if 'topics' in message:
            self.available_video_topics = message['topics']
            await self.message_broker.publish("Backend/available_video_topics_update", {"topics": self.available_video_topics})
        else:
            pass

    async def handle_mission_state_update(self, topic, message):
        if 'state' in message:
            self.mission_state = message['state']
            await self.message_broker.publish("Backend/mission_state_update", {"mission_state": self.mission_state})
        else:
            pass
    
class Frontend:
    def __init__(self, ip: str, port: int, network_debug:bool=False, network_reloader:bool=False, message_broker: MessageBroker = None):
        self.ip = ip
        self.port = port
        self.network_debug = network_debug
        self.network_reloader = network_reloader
        self.app = Flask(__name__)

        self.current_frame = None

        self.message_broker = message_broker
        self.state = {}
        self.logger = logging.getLogger(__name__)
        self.loop = asyncio.new_event_loop()
        self.executor = ThreadPoolExecutor()

        @self.app.route('/', methods=['GET', 'POST'])
        def index():
            if request.method == 'POST':
                asyncio.run_coroutine_threadsafe(
                    self.handle_post_request(request.form), self.loop)
            return render_template('index.html', **self.state)

        @self.app.route('/video_feed')
        def video_feed():
            return Response(self.render_frames(),
                            mimetype='multipart/x-mixed-replace; boundary=frame')

        @self.app.route('/state')
        def get_state():
            return jsonify(self.state)

    async def start(self):
        self.logger.info("Frontend started")

        
        # Backend subscriptions
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/mission_state_update", self.handle_mission_state_update))
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/available_video_topics_update", self.handle_available_video_topics_update))
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/selected_topic_latest_frame", self.handle_video_frame))
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/error_banner", self.handle_error_banner))

        # Run Flask app in a separate thread
        self.executor.submit(self.run_flask)

        # Start the event loop in the current thread
        await self.run_event_loop()

    def run_flask(self):
        """
        Entry point for the Flask app
        """
        self.app.run(host=self.ip, port=self.port,
                     debug=self.network_debug, use_reloader=self.network_reloader)

    async def run_event_loop(self):
        """
        Event loop for the frontend zmq subscriber
        """
        asyncio.set_event_loop(self.loop)
        # Keep this loop running to handle incoming messages
        while True:
            await asyncio.sleep(1)

    async def handle_post_request(self, form):
        """
        Handle User POST requests (Buttons, etc)
        """
        if "gaze_button_pressed" in form:
            await self.message_broker.publish("Frontend/gaze_enabled_button_pressed", {'type': 'gaze_enabled_button_pressed'})
        elif "mission_start_button_pressed" in form:
            await self.message_broker.publish("Frontend/mission_start_button_pressed", {'type': 'mission_start_button_pressed'})
        elif "mission_pause_button_pressed" in form:
            await self.message_broker.publish("Frontend/mission_pause_button_pressed", {'type': 'mission_pause_button_pressed'})
        elif "mission_stop_button_pressed" in form:
            await self.message_broker.publish("Frontend/mission_stop_button_pressed", {'type': 'mission_stop_button_pressed'})
        elif "video_topic_selected" in form:
            selected_topic = form.get("video_topic_selected")
            await self.message_broker.publish("Frontend/video_topic_selected", {'topic': selected_topic})


    def render_frames(self):
        while True:
            if self.current_frame is None:
                frame = np.zeros((240, 320, 3), dtype=np.uint8)
                cv2.putText(frame, "Nothing to show", (25, 120),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            else:
                frame = self.current_frame

            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

    async def handle_video_frame(self, topic, message):
        if isinstance(message, dict) and "frame" in message:
            compressed_frame = message['frame']
            np_arr = np.frombuffer(compressed_frame, np.uint8)
            self.current_frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        else:
            self.logger.warning(f"Received invalid message format for video frame: {message}")

    async def handle_error_banner(self, topic, message):
        pass

    async def handle_mission_state_update(self, topic, message):
        if 'mission_state' in message:
            self.state['mission_state'] = message['mission_state']
        else:
            pass

    async def handle_available_video_topics_update(self, topic, message):
        if isinstance(message, dict) and "topics" in message:
            self.state['available_video_topics'] = message['topics']
        else:
            self.logger.warning(f"Received invalid message format for available video topics: {message}")


    async def handle_video_topic_selected(self, topic, message):
        if isinstance(message, dict) and "topic" in message:
            self.selected_video_topic = message['topic']
            await self.message_broker.publish("Backend/selected_video_topic_update", {"topic": self.selected_video_topic})
        else:
            self.logger.warning(f"Received invalid message format for video topic selected: {message}")


async def main(enable_logging):
    setup_logging(enable_logging)
    logger = logging.getLogger(__name__)
    logger.info("Web host starting")

    message_broker = MessageBroker(max_queue_size=10)
    backend = Backend(video_buffer_length=10,
                      video_frame_rate=15, message_broker=message_broker)
    frontend = Frontend(ip='0.0.0.0', port=5000,
                        network_debug=False, network_reloader=False, message_broker=message_broker)

    await asyncio.gather(
        backend.start(),
        frontend.start()
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true', help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))
