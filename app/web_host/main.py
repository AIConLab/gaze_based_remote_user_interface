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

# enum_definitiosn defined in /app/app_shared_data/enum_definitions.py
from enum_definitions import MissionStates, MissionCommandSignals


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


class MessageBroker:
    def __init__(self):
        self.context = zmq.asyncio.Context()
        self.publisher = self.context.socket(zmq.PUB)
        self.publisher.bind("tcp://*:5555")
        self.subscriber = self.context.socket(zmq.SUB)
        self.subscriber.connect("tcp://localhost:5555")
        self.logger = logging.getLogger(__name__)

    async def publish(self, topic, message):
        try:
            full_topic = topic.encode()
            await self.publisher.send_multipart([full_topic, msgpack.packb(message)])
        except Exception as e:
            pass

    async def subscribe(self, topic, callback):
        self.subscriber.setsockopt(zmq.SUBSCRIBE, topic.encode())
        while True:

            try:
                [topic, msg] = await self.subscriber.recv_multipart()
                await callback(topic.decode(), msgpack.unpackb(msg))
            except Exception as e:
                pass

class Backend:
    def __init__(self, message_broker: MessageBroker, video_buffer_length: int = 10, video_frame_rate: int = 15):
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

        # Continous Publisher
        asyncio.create_task(self.publish_latest_frame())

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

        # Video Manager subscriptions
        asyncio.create_task(self.message_broker.subscribe(
            "VideoManager/latest_frame_of_selected_topic", self.handle_latest_frame_of_selected_topic))
        asyncio.create_task(self.message_broker.subscribe(
            "VideoManager/available_video_topics", self.handle_available_video_topics))

        # MissionManager subscriptions
        asyncio.create_task(self.message_broker.subscribe(
            "MissionManager/mission_state_update", self.handle_mission_state_update))

    async def publish_latest_frame(self):
        while True:
            if self.latest_frame is not None:
                # Convert numpy array to list before publishing
                frame_list = self.latest_frame.tolist()
                await self.message_broker.publish("Backend/latest_frame_from_selected_topic", {'frame': frame_list})
            else:
                # Create a blank frame with text when no frame is available
                blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(blank_frame, "Nothing to show", (50, 240),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                frame_list = blank_frame.tolist()
                await self.message_broker.publish("Backend/latest_frame_from_selected_topic", {'frame': frame_list})

            if self.video_frame_rate > 0:
                await asyncio.sleep(1/self.video_frame_rate)
            else:
                await asyncio.sleep(1/15)  # Default to 15 fps

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
        self.selected_video_topic = message['topic']
        await self.message_broker.publish("Backend/selected_video_topic_update", {"topic": self.selected_video_topic})

    async def handle_latest_frame_of_selected_topic(self, topic, message):
        try:
            if 'topic' in message and 'frame' in message and message['topic'] == self.selected_video_topic:
                self.latest_frame = np.array(message['frame'], dtype=np.uint8)
                self.video_buffer.append(self.latest_frame)
            else:
                self.logger.warning(f"Received invalid frame message: {message}")
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
    def __init__(self, message_broker, ip: str, port: int, network_debug:bool=False, network_reloader:bool=False):
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
        """
        Entry point for the frontend
        """
        self.logger.info("Frontend started")

        asyncio.create_task(self.message_broker.subscribe(
            "Backend/mission_state_update", self.handle_mission_state_update))
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/available_video_topics_update", self.handle_available_video_topics_update))
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/selected_video_topic_update", self.handle_selected_video_topic_update))
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/latest_frame_from_selected_topic", self.handle_video_frame))
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/error_banner", self.handle_error_banner))
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/gaze_enabled_state_update", self.handle_gaze_enabled_state_update))

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

    def render_frames(self):
        while True:
            if self.current_frame is None:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(frame, "Nothing to show", (50, 240),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            else:
                frame = self.current_frame

            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')

    async def handle_error_banner(self, topic, message):
        pass

    async def handle_mission_state_update(self, topic, message):
        if 'mission_state' in message:
            self.state['mission_state'] = message['mission_state']
        else:
            pass

    async def handle_available_video_topics_update(self, topic, message):
        self.state['available_video_topics'] = message['topics']

    async def handle_selected_video_topic_update(self, topic, message):
        self.state['selected_video_topic'] = message['topic']

    async def handle_video_frame(self, topic, message):
        # Update the current frame for display
        self.current_frame = np.array(message['frame'], dtype=np.uint8)

    async def handle_gaze_enabled_state_update(self, topic, message):
        if 'gaze_enabled' in message:
            self.state['gaze_enabled_state'] = message['gaze_enabled']
        else:
            pass

async def main(enable_logging):
    setup_logging(enable_logging)
    logger = logging.getLogger(__name__)
    logger.info("Application starting")

    message_broker = MessageBroker()
    backend = Backend(message_broker, video_buffer_length=10,
                      video_frame_rate=15)
    frontend = Frontend(message_broker, ip='0.0.0.0', port=5000,
                        network_debug=False, network_reloader=False)

    await asyncio.gather(
        backend.start(),
        frontend.start()
    )

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true',
                        help='Enable logging to app/logs directory')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))
