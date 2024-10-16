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
    def __init__(self, message_broker:MessageBroker, video_buffer_length:int = 10):
        self.message_broker = message_broker


        self.gaze_enabled_state = False
        self.mission_states 
        self.robot_connected_state = False

        self.available_video_topics = []
        self.selected_video_topic = None
        self.video_buffer = deque(maxlen=video_buffer_length)


        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("Backend started")

        # Front end subscriptions
        asyncio.create_task(self.message_broker.subscribe("gaze_enabled_button_pressed", self.handle_gaze_enabled_button_pressed))
        asyncio.create_task(self.message_broker.subscribe("mission_start_button_pressed", self.handle_mission_start_button_pressed))
        asyncio.create_task(self.message_broker.subscribe("mission_stop_button_pressed", self.handle_mission_stop_button_pressed))
        asyncio.create_task(self.message_broker.subscribe("video_topic_selected", self.handle_video_topic_selected))


        # Video Manager subscriptions
        asyncio.create_task(self.message_broker.subscribe("selected_video_topic_latest_frame", self.handle_selected_video_topic_latest_frame))
        asyncio.create_task(self.message_broker.subscribe("available_video_topics", self.handle_available_video_topics))


    """
    Frontend Callbacks
    ---------------------------------------------------------------------------
    """
    async def handle_gaze_enabled_button_pressed(self, message):
        """
        Toggle the gaze enabled state and publish the new state
        """
        self.gaze_enabled_state = not self.gaze_enabled_state
        self.logger.info(f"Gaze enabled state changed to: {self.gaze_enabled_state}")

        await self.message_broker.publish("gaze_state_update", {"gaze_enabled": self.gaze_enabled_state})

    async def handle_mission_start_button_pressed(self, message):
        """
        Start the mission and publish the new state 
        """
        if not self.robot_connected_state:
            self.logger.warning("Attempted to start mission without robot connection")
            await self.message_broker.publish("error_banner", {"message": "Robot not connected"})
            return

        if not self.mission_active_state:
            self.mission_active_state = True
            self.logger.info("Mission start command")
            await self.message_broker.publish("mission_command", {"mission_active": self.mission_active_state})

        else:
            self.logger.info("Attempted to start already active mission")

    async def handle_mission_stop_button_pressed(self, message):
        if not self.robot_connected_state:
            self.logger.warning("Attempted to stop mission without robot connection")
            await self.message_broker.publish("error_banner", {"message": "Robot not connected"})
            return

        if self.mission_active_state:
            self.mission_active_state = False
            self.logger.info("Mission stopped")
            await self.message_broker.publish("mission_state_update", {"mission_active": self.mission_active_state})
        else:
            self.logger.info("Attempted to stop inactive mission")

    async def handle_video_topic_selected(self, message):
        self.selected_video_topic = message['topic']
        await self.message_broker.publish("selected_video_topic_update", {"topic": self.selected_video_topic})
        # Publish to video manager
        await self.message_broker.publish("video_manager_topic_request", {"requested_topic": self.selected_video_topic})


    """
    Video Manager Callbacks
    ---------------------------------------------------------------------------
    """
    async def handle_available_video_topics(self, message):
        self.available_video_topics = message['topics']
        await self.message_broker.publish("available_video_topics_update", {"topics": self.available_video_topics})

    async def handle_selected_video_topic_latest_frame(self, message):
        if message['topic'] == self.selected_video_topic:
            self.video_buffer.append(message['frame'])
            await self.message_broker.publish("frontend_video_frame", {'frame': message['frame']})

    def get_state(self):
        return {
            "gaze_enabled_state": self.gaze_enabled_state,
            "mission_active_state": self.mission_active_state,
            "robot_connected_state": self.robot_connected_state,
            "available_video_topics": self.available_video_topics,
            "selected_video_topic": self.selected_video_topic
        }


class Frontend:
    def __init__(self, message_broker, ip:str, port:int, network_debug:bool, network_reloader:bool):
        self.ip = ip
        self.port = port
        self.network_debug = network_debug
        self.network_reloader = network_reloader
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
        """
        Entry point for the frontend
        """
        self.logger.info("Frontend started")

        asyncio.create_task(self.message_broker.subscribe("gaze_state_update", self.handle_gaze_state_update))
        asyncio.create_task(self.message_broker.subscribe("mission_state_update", self.handle_mission_state_update))
        asyncio.create_task(self.message_broker.subscribe("available_video_topics_update", self.handle_available_video_topics_update))
        asyncio.create_task(self.message_broker.subscribe("selected_video_topic_update", self.handle_selected_video_topic_update))
        asyncio.create_task(self.message_broker.subscribe("frontend_video_frame", self.handle_video_frame))
        asyncio.create_task(self.message_broker.subscribe("error_banner", self.handle_error_banner))
        
        # Run Flask app in a separate thread
        self.executor.submit(self.run_flask)
        
        # Start the event loop in the current thread
        await self.run_event_loop()

    def run_flask(self):
        """
        Entry point for the Flask app
        """
        self.app.run(host=self.ip, port=self.port, debug=self.network_debug, use_reloader=self.network_reloader)

    async def run_event_loop(self):
        """
        Event loop for the frontend zmq subscriber
        """
        asyncio.set_event_loop(self.loop)

        await asyncio.gather(
            self.message_broker.subscribe("backend_state_update", self.update_state),
            self.message_broker.subscribe("error_banner", self.handle_error_banner)
        )

    async def handle_post_request(self, form):
        """
        Handle User POST requests (Buttons, etc)
        """
        if "gaze_button_pressed" in form:
            self.logger.info("Gaze button pressed")
            await self.message_broker.publish("gaze_enabled_button_pressed", {'type': 'gaze_enabled_button_pressed'})

        elif "mission_start_button_pressed" in form:
            self.logger.info("Mission start button pressed")
            await self.message_broker.publish("mission_start_button_pressed", {'type': 'mission_start_button_pressed'})

        elif "mission_stop_button_pressed" in form:
            self.logger.info("Mission stop button pressed")
            await self.message_broker.publish("mission_stop_button_pressed", {'type': 'mission_stop_button_pressed'})


    async def update_state(self, new_state):
        """
        Updates the front end display based on the received states of backend data
        """
        self.state.update(new_state)
        self.logger.info(f"State updated: {new_state}")


    async def handle_error_banner(self, message):
        # Implement error banner handling here
        pass


    async def handle_mission_state_update(self, message):
        self.state['mission_active_state'] = message['mission_active']
        self.logger.info(f"Mission state updated: {message['mission_active']}")

    async def handle_available_video_topics_update(self, message):
        self.state['available_video_topics'] = message['topics']
        self.logger.info(f"Available video topics updated: {message['topics']}")

    async def handle_selected_video_topic_update(self, message):
        self.state['selected_video_topic'] = message['topic']
        self.logger.info(f"Selected video topic updated: {message['topic']}")

    async def handle_video_frame(self, message):
        # Update the current frame for display
        self.current_frame = message['frame']

    async def handle_error_banner(self, message):
        # Implement error banner handling here
        self.logger.error(f"Error banner: {message['message']}")
        # You might want to add this error message to the state for display in the frontend
        self.state['error_message'] = message['message']

    def show_latest_frame(self):
        while True:
            if not hasattr(self, 'current_frame') or self.current_frame is None:
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(frame, "Nothing to show. Frames dropping or no topic selected.", (50, 240),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            else:
                frame = self.current_frame

            ret, buffer = cv2.imencode('.jpg', frame)
            frame = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')



async def main(enable_logging):
    setup_logging(enable_logging)
    logger = logging.getLogger(__name__)
    logger.info("Application starting")
    
    message_broker = MessageBroker()
    backend = Backend(message_broker)
    frontend = Frontend(message_broker, ip='1.1.1.1', port=1111, network_debug=False, netowrk_reloader=True)

    await asyncio.gather(
        backend.start(),
        frontend.start()
    )

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true', help='Enable logging to app/logs directory')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))
