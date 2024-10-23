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
        log_level = logging.DEBUG
    else:
        log_level = logging.INFO

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler()
        ]
    )

    if enable_logging:
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"app_log_{timestamp}.log")
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
        logging.getLogger().addHandler(file_handler)

    # Set the level for specific loggers
    for logger_name in ['WebcamModule', 'VideoRenderer', 'ModuleController', 'ModuleDatapath']:
        logging.getLogger(logger_name).setLevel(log_level)


class VideoRenderer:
    def __init__(self,
                  video_fps=15, 
                  output_width=1280, 
                  output_height=720, 
                  message_broker: MessageBroker = None,
                  max_queue_size=3  # Buffer up to 3 frames
                  ):
        self.message_broker = message_broker
        self.render_apriltags = False

        self.image_quality = 70
        self.video_fps = video_fps
        self.output_width = output_width
        self.output_height = output_height
        
        # Frame synchronization
        self.frame_lock = asyncio.Lock()
        self.frame_queue = asyncio.Queue(maxsize=max_queue_size)
        self.topic_change_event = asyncio.Event()
        self.current_topic = ""
        
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("VideoRenderer started")

        await self.message_broker.subscribe("ModuleDatapath/selected_topic_latest_frame", 
                                          self.handle_selected_topic_latest_frame)
        await self.message_broker.subscribe("ModuleController/selected_video_topic_update", 
                                          self.handle_topic_update)

        # Start the publishing task
        asyncio.create_task(self.publish_latest_frame())

    async def handle_selected_topic_latest_frame(self, topic, message):
        try:
            if not self.topic_change_event.is_set():
                compressed_frame = message["frame"]
                np_arr = np.frombuffer(compressed_frame, np.uint8)
                
                # Decode frame for processing
                latest_frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

                # Try to add to queue, drop oldest frame if full
                try:
                    self.frame_queue.put_nowait(latest_frame)
                except asyncio.QueueFull:
                    try:
                        self.frame_queue.get_nowait()  # Remove oldest frame
                        await self.frame_queue.put(latest_frame)  # Add new frame
                    except asyncio.QueueEmpty:
                        pass  # Queue was emptied by another task

        except Exception as e:
            self.logger.error(f"Error handling incoming frame: {str(e)}")

    async def handle_topic_update(self, topic, message):
        self.logger.debug(f"Topic update received: {message}")
        
        async with self.frame_lock:
            self.topic_change_event.set()  # Signal topic change
            new_topic = message.get("topic", "")
            
            if new_topic != self.current_topic:
                self.current_topic = new_topic
                # Clear existing frames
                while not self.frame_queue.empty():
                    try:
                        self.frame_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                
                await asyncio.sleep(0.1)  # Small delay for synchronization
                self.topic_change_event.clear()  # Clear topic change flag

    async def publish_latest_frame(self):
        """Publishes frames to the message broker"""
        blank_frame = None  # Cache blank frame
        switching_frame = None  # Cache switching frame
        
        try:
            while True:
                async with self.frame_lock:
                    if self.topic_change_event.is_set():
                        # Create switching frame if not cached
                        if switching_frame is None:
                            switching_frame = np.zeros((self.output_height, self.output_width, 3), np.uint8)
                            cv2.putText(switching_frame, "Switching video feed...", 
                                      (self.output_width//2 - 100, self.output_height//2),
                                      cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                        
                        frame_to_publish = switching_frame
                        status = "switching"
                        
                    elif self.frame_queue.empty():
                        # Create blank frame if not cached
                        if blank_frame is None:
                            blank_frame = np.zeros((self.output_height, self.output_width, 3), np.uint8)
                            cv2.putText(blank_frame, "No video feed selected", 
                                      (self.output_width//2 - 100, self.output_height//2),
                                      cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                        
                        frame_to_publish = blank_frame
                        status = "blank"
                        
                    else:
                        frame_to_publish = await self.frame_queue.get()
                        status = "latest"

                    # Encode frame
                    _, encoded_out = cv2.imencode('.jpeg', frame_to_publish, 
                                                [int(cv2.IMWRITE_JPEG_QUALITY), self.image_quality])
                    
                    await self.message_broker.publish("VideoRender/latest_rendered_frame", 
                                                    {'frame': encoded_out.tobytes()})
                    

                await asyncio.sleep(1/self.video_fps)

        except Exception as e:
            self.logger.error(f"Error in publish_latest_frame: {str(e)}")
            raise e

    def frame_reshape(self, frame):
        h, w = frame.shape[:2]
        aspect = w/h
        if aspect > (self.output_width/self.output_height):
            new_width = self.output_width
            new_height = int(self.output_width/aspect)
        else:
            new_height = self.output_height
            new_width = int(self.output_height*aspect)
        
        return cv2.resize(frame, (new_width, new_height))

    async def stop(self):
        self.topic_change_event.set()  # Signal stop
        self.message_broker.stop()


class ModuleController:
    def __init__(self, message_broker: MessageBroker):
        self.message_broker = message_broker
        self.selected_video_topic = ""
        self.gaze_enabled = False
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("ModuleController started")
        await self.message_broker.subscribe("Backend/selected_video_topic_update", self.handle_selected_video_topic_update)
        await self.message_broker.subscribe("Backend/gaze_enabled_state_update", self.handle_gaze_enabled_state_update)

    async def handle_selected_video_topic_update(self, topic, message):
        self.logger.debug(f"ModuleController: Received selected video topic update: {message}")

        if "topic" in message:
            self.selected_video_topic = message["topic"]

            await self.message_broker.publish("ModuleController/selected_video_topic_update", {"topic": self.selected_video_topic})
            
        else:
            self.logger.warning(f"Received invalid message format for selected video topic update: {message}")

    async def handle_gaze_enabled_state_update(self, topic, message):
        self.logger.debug(f"Received gaze enabled state update: {message}")
        self.gaze_enabled = message["gaze_enabled"]
        # TODO: Implement gaze enabled update logic

    async def stop(self):
        self.message_broker.stop()


class ModuleDatapath:
    def __init__(self, message_broker: MessageBroker):
        self.message_broker = message_broker
        self.video_topics = set() 
        self.selected_video_topic = ""
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("ModuleDatapath started")
        await self.message_broker.subscribe("WebcamModule/latest_frame", self.handle_incoming_stream)
        await self.message_broker.subscribe("ModuleController/selected_video_topic_update", self.handle_selected_video_topic_update)
        asyncio.create_task(self.publish_available_topics())

    async def publish_available_topics(self):
        while True:
            await self.message_broker.publish("ModuleDatapath/available_video_topics", {"topics": list(self.video_topics)})
            await asyncio.sleep(1)

    async def handle_incoming_stream(self, topic, message):
        """
        MUX incoming video streams based on selected video topic
        """
        try:
            if topic not in self.video_topics:
                self.logger.info(f"New video topic detected: {topic}")
                self.video_topics.add(topic)

            if topic == self.selected_video_topic:
                await self.message_broker.publish("ModuleDatapath/selected_topic_latest_frame", message)

        except Exception as e:
            self.logger.error(f"Error handling incoming stream: {str(e)}")

    async def handle_selected_video_topic_update(self, topic, message):
        self.logger.debug(f"ModuleDatapath: Received selected video topic update: {message}")

        self.selected_video_topic = message["topic"]

    async def stop(self):
        self.message_broker.stop()


class WebcamModule:
    def __init__(self,
                 video_fps=15, 
                 message_broker: MessageBroker = None, 
                 output_width=1280, 
                 output_height=720, 
                 image_quality=70
                 ):
        self.message_broker = message_broker
        self.logger = logging.getLogger('WebcamModule')

        self.image_quality = image_quality
        self.cap = None
        self.video_fps = video_fps
        self.output_width = output_width
        self.output_height = output_height

    async def start(self):
        self.logger.info("WebcamModule started")
        try:
            self.cap = cv2.VideoCapture(0)
            if not self.cap.isOpened():
                raise IOError("Cannot open webcam")
            
            # Set camera properties
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.output_width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.output_height)
            self.cap.set(cv2.CAP_PROP_FPS, self.video_fps)
            
        except Exception as e:
            self.logger.error(f"Failed to open webcam: {str(e)}")
            return

        try:
            asyncio.create_task(self.capture_and_publish_frames())

        except Exception as e:
            self.logger.error(f"Error in WebcamModule: {str(e)}")
            raise e

    async def capture_and_publish_frames(self):
        try:
            while True:
                ret, frame = self.cap.read()
                if ret:
                    _, encoded_out = cv2.imencode('.jpeg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.image_quality])
                    
                    await self.message_broker.publish("WebcamModule/latest_frame", 
                                                  {"topic": "WebcamModule", 
                                                   "frame": encoded_out.tobytes()})
                else:
                    self.logger.warning("Failed to capture frame from webcam")

                await asyncio.sleep(1/self.video_fps)

        except Exception as e:
            self.logger.error(f"Error in WebcamModule: {str(e)}")
            raise e  # Add raise to help with debugging

        finally:
            self.stop()

    async def stop(self):
        self.logger.info("WebcamModule stopping")
        if self.cap:
            self.cap.release()

        self.message_broker.stop()


async def main(enable_logging):
    try:
        setup_logging(enable_logging)
        logger = logging.getLogger(__name__)
        logger.info("Module Processor starting")

        video_renderer_message_broker = MessageBroker(1024)
        module_controller_message_broker = MessageBroker(1024)
        module_datapath_message_broker = MessageBroker(1024*4)
        webcam_module_message_broker = MessageBroker(1024)

        video_renderer = VideoRenderer(video_fps=30, 
                                message_broker=video_renderer_message_broker,
                                output_width=1280, 
                                output_height=720)

        module_controller = ModuleController(message_broker=module_controller_message_broker)

        module_datapath = ModuleDatapath(message_broker=module_datapath_message_broker)

        webcam_module = WebcamModule(video_fps=30,
                                message_broker=webcam_module_message_broker, 
                                output_width=1280, 
                                output_height=720)

        await asyncio.gather(
            video_renderer.start(),
            module_controller.start(),
            module_datapath.start(),
            webcam_module.start()
        )

        # Keep the main coroutine running
        while True:
            await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"Error in main: {str(e)}")
        raise e

    finally:
        webcam_module.stop()
        video_renderer.stop()
        module_controller.stop()
        module_datapath.stop()

    

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true',
                        help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))