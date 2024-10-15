import asyncio
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

def setup_logging(enable_logging):
    if enable_logging:
        log_dir = "app/logs"
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
            await self.publisher.send_multipart([topic.encode(), msgpack.packb(message)])
            self.logger.info(f"Published message on topic: {topic}")
        except Exception as e:
            self.logger.error(f"Error publishing message: {e}")
        
    async def subscribe(self, topic, callback):
        self.subscriber.setsockopt(zmq.SUBSCRIBE, topic.encode())
        self.logger.info(f"Subscribed to topic: {topic}")
        while True:
            try:
                [topic, msg] = await self.subscriber.recv_multipart()
                await callback(msgpack.unpackb(msg))
            except Exception as e:
                self.logger.error(f"Error in subscriber: {e}")


class Backend:
    def __init__(self, message_broker, video_buffer_length: int = 10):
        self.message_broker = message_broker
        self.gaze_enabled_state = False
        self.mission_active_state = False
        self.robot_connected_state = False

        self.available_video_topics = []
        self.video_buffer = deque(maxlen=video_buffer_length)

        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("Backend started")
        asyncio.create_task(self.message_broker.subscribe("gaze_toggle_button", self.handle_gaze_toggle_button))
        asyncio.create_task(self.message_broker.subscribe("mission_start_button", self.handle_mission_start_button))
        asyncio.create_task(self.message_broker.subscribe("mission_stop_button", self.handle_mission_stop_button))

    async def handle_gaze_toggle_button(self, message):
        self.gaze_enabled_state = not self.gaze_enabled_state
        self.logger.info(f"Gaze enabled state changed to: {self.gaze_enabled_state}")
        await self.message_broker.publish("backend_state_update", self.get_state())

    async def handle_mission_start_button(self, message):
        if not self.robot_connected_state:
            self.logger.warning("Attempted to start mission without robot connection")
            await self.message_broker.publish("error_banner", {"message": "Robot not connected"})
            return

        if not self.mission_active_state:
            self.mission_active_state = True
            self.logger.info("Mission started")
            await self.message_broker.publish("backend_state_update", self.get_state())
        else:
            self.logger.info("Attempted to start already active mission")

    async def handle_mission_stop_button(self, message):
        if not self.robot_connected_state:
            self.logger.warning("Attempted to stop mission without robot connection")
            await self.message_broker.publish("error_banner", {"message": "Robot not connected"})
            return

        if self.mission_active_state:
            self.mission_active_state = False
            self.logger.info("Mission stopped")
            await self.message_broker.publish("backend_state_update", self.get_state())
        else:
            self.logger.info("Attempted to stop inactive mission")
        
    def get_state(self):
        return {
            "gaze_enabled_state": self.gaze_enabled_state,
            "mission_active_state": self.mission_active_state,
            "robot_connected_state": self.robot_connected_state,
            "available_video_topics": self.available_video_topics,
            "video_buffer": self.video_buffer
        }


class Frontend:
    def __init__(self, message_broker, ip:str, port:int, network_debug:bool):
        self.ip = ip
        self.port = port
        self.network_debug = network_debug
        self.app = Flask(__name__)
        self.message_broker = message_broker
        self.state = {}
        self.logger = logging.getLogger(__name__)
        self.loop = asyncio.new_event_loop()
        self.executor = ThreadPoolExecutor()

        @self.app.route('/', methods=['GET', 'POST'])
        def index():
            if request.method == 'POST':
                asyncio.run_coroutine_threadsafe(self.handle_post_request(request.form), self.loop)
            return render_template('index.html', **self.state)

        @self.app.route('/video_feed')
        def video_feed():
            return Response(self.show_latest_frame(),
                            mimetype='multipart/x-mixed-replace; boundary=frame')

        @self.app.route('/state')
        def get_state():
            return jsonify(self.state)

    async def start(self):
        self.logger.info("Frontend started")
        asyncio.create_task(self.message_broker.subscribe("backend_state_update", self.update_state))
        asyncio.create_task(self.message_broker.subscribe("error_banner", self.handle_error_banner))
        
        # Run Flask app in a separate thread
        self.executor.submit(self.run_flask)
        
        # Start the event loop in the current thread
        await self.run_event_loop()

    def run_flask(self):
        self.app.run(host=self.ip, port=self.port, debug=False, use_reloader=False)

    async def run_event_loop(self):
        asyncio.set_event_loop(self.loop)
        await asyncio.gather(
            self.message_broker.subscribe("backend_state_update", self.update_state),
            self.message_broker.subscribe("error_banner", self.handle_error_banner)
        )

    async def handle_post_request(self, form):
        if "gaze_button_pressed" in form:
            self.logger.info("Gaze button pressed")
            await self.message_broker.publish("gaze_toggle_button", {'type': 'gaze_toggle_button'})
        elif "mission_start_button_pressed" in form:
            self.logger.info("Mission start button pressed")
            await self.message_broker.publish("mission_start_button", {'type': 'mission_start_button'})
        elif "mission_stop_button_pressed" in form:
            self.logger.info("Mission stop button pressed")
            await self.message_broker.publish("mission_stop_button", {'type': 'mission_stop_button'})

    async def handle_error_banner(self, message):
        # Implement error banner handling here
        pass

    async def update_state(self, new_state):
        self.state.update(new_state)
        self.logger.info(f"State updated: {new_state}")

    def show_latest_frame(self):
        # Implement video frame generation here
        pass

# The rest of your code (Backend, MessageBroker, main function) remains the same

async def main(enable_logging):
    setup_logging(enable_logging)
    logger = logging.getLogger(__name__)
    logger.info("Application starting")
    
    message_broker = MessageBroker()
    backend = Backend(message_broker)
    frontend = Frontend(message_broker, ip='0.0.0.0', port=5000, network_debug=False)

    await asyncio.gather(
        backend.start(),
        frontend.start()
    )

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true', help='Enable logging to app/logs directory')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))
