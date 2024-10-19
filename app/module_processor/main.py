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

class VideoRenderer:
    def __init__(self, video_frame_rate=15):
        self.message_broker = MessageBroker()
        self.render_apriltags = False
        self.video_frame_rate = video_frame_rate
        self.latest_frame = None
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("VideoRenderer started")
        asyncio.create_task(self.publish_latest_frame())
        asyncio.create_task(self.message_broker.subscribe(
            "ModuleDatapath/selected_topic_latest_frame", self.handle_selected_topic_latest_frame))

    async def handle_selected_topic_latest_frame(self, topic, message):
        compressed_frame = message["frame"]
        np_arr = np.frombuffer(compressed_frame, np.uint8)
        self.latest_frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        
        if self.render_apriltags:
            # Render apriltags here if needed
            pass

    async def publish_latest_frame(self):
        while True:
            if self.latest_frame is not None:
                _, jpeg = cv2.imencode('.jpg', self.latest_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                await self.message_broker.publish("VideoRender/latest_rendered_frame", {'frame': jpeg.tobytes()})
            else:
                blank_frame = np.zeros((480, 640, 3), np.uint8)
                cv2.putText(blank_frame, "Nothing to show", (25, 120),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                _, jpeg = cv2.imencode('.jpg', blank_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                await self.message_broker.publish("VideoRender/latest_rendered_frame", {'frame': jpeg.tobytes()})
            await asyncio.sleep(1/self.video_frame_rate)



class ModuleController:
    def __init__(self):
        self.message_broker = MessageBroker()
        self.selected_video_topic = None
        self.gaze_enabled = False
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("ModuleController started")
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/selected_video_topic_update", self.handle_selected_video_topic_update))
        asyncio.create_task(self.message_broker.subscribe(
            "Backend/gaze_enabled_state_update", self.handle_gaze_enabled_state_update))

    async def handle_selected_video_topic_update(self, topic, message):
        if isinstance(message, dict) and "topic" in message:
            self.selected_video_topic = message["topic"]
            await self.message_broker.publish("ModuleController/selected_video_topic_update", {"topic": self.selected_video_topic})
        else:
            self.logger.warning(f"Received invalid message format for selected video topic update: {message}")

    async def handle_gaze_enabled_state_update(self, topic, message):
        self.gaze_enabled = message["gaze_enabled"]
        # TODO: Implement gaze enabled update logic

class ModuleDatapath:
    def __init__(self):
        self.message_broker = MessageBroker()
        self.video_topics = set()
        self.selected_video_topic = None
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("ModuleDataPath started")
        asyncio.create_task(self.publish_available_topics())
        asyncio.create_task(self.message_broker.subscribe(
            "WebcamModule/latest_frame", self.handle_incoming_stream))
        asyncio.create_task(self.message_broker.subscribe(
            "ModuleController/selected_video_topic_update", self.handle_selected_video_topic_update))

    async def publish_available_topics(self):
        while True:
            await self.message_broker.publish("ModuleDatapath/available_video_topics", {"topics": list(self.video_topics)})
            await asyncio.sleep(1)

    async def handle_incoming_stream(self, topic, message):
        try:
            if topic == self.selected_video_topic:
                await self.message_broker.publish("ModuleDatapath/selected_topic_latest_frame", message)
            if topic not in self.video_topics:
                self.video_topics.add(topic)
        except Exception as e:
            self.logger.error(f"Error handling incoming stream: {str(e)}")

    async def handle_selected_video_topic_update(self, topic, message):
        self.selected_video_topic = message["topic"]

class WebcamModule:
    def __init__(self, video_frame_rate=15):
        self.message_broker = MessageBroker()
        self.video_frame_rate = video_frame_rate
        self.logger = logging.getLogger(__name__)
        self.cap = None

    async def start(self):
        self.logger.info("WebcamModule started")
        try:
            self.cap = cv2.VideoCapture(0)
            if not self.cap.isOpened():
                raise IOError("Cannot open webcam")
        except Exception as e:
            self.logger.error(f"Failed to open webcam: {str(e)}")
            return

        try:
            while True:
                ret, frame = self.cap.read()
                if ret:
                    frame = cv2.resize(frame, (640, 480))
                    _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    await self.message_broker.publish("WebcamModule/latest_frame", 
                                                      {"topic": "WebcamModule", 
                                                       "frame": jpeg.tobytes()})
                else:
                    self.logger.warning("Failed to capture frame from webcam")
                await asyncio.sleep(1/self.video_frame_rate)
        except Exception as e:
            self.logger.error(f"Error in WebcamModule: {str(e)}")
        finally:
            if self.cap:
                self.cap.release()

    def __del__(self):
        if self.cap:
            self.cap.release()



async def main(enable_logging):
    setup_logging(enable_logging)
    logger = logging.getLogger(__name__)
    logger.info("Module Processor starting")

    video_renderer = VideoRenderer(video_frame_rate=15)
    module_controller = ModuleController()
    module_datapath = ModuleDatapath()
    webcam_module = WebcamModule(video_frame_rate=15)

    await asyncio.gather(
        video_renderer.start(),
        module_controller.start(),
        module_datapath.start(),
        webcam_module.start()
    )

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true',
                        help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))