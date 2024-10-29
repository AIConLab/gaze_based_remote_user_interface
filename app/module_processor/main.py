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
from utilities import AprilTagRenderer
from enum_definitions import ProcessingModes, ProcessingModeActions


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


class UserInteractionRenderer:
    def __init__(self, message_broker: MessageBroker):
        self.message_broker = message_broker
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("UserInteractionRenderer started")

        # Subscriptions

    async def stop(self):
        self.message_broker.stop()


class VideoRenderer:
    def __init__(self,
                  video_fps=15, 
                  output_width=1280, 
                  output_height=720, 
                  message_broker: MessageBroker = None,
                  max_queue_size=5  
                  ):
        # Init classes
        self.message_broker = message_broker
        self.apriltag_renderer = AprilTagRenderer(
            upper_left_corner_tag_path="tags/tag41_12_00000.svg",
            lower_left_corner_tag_path="tags/tag41_12_00001.svg",
            upper_right_corner_tag_path="tags/tag41_12_00002.svg",
            lower_right_corner_tag_path="tags/tag41_12_00003.svg",
            scale=1/8
        )
        
        # Processing flags
        self.render_apriltags = False
        self.processing_mode = None

        # Video rendering parameters
        self.image_quality = 70
        self.video_fps = video_fps
        self.output_width = output_width
        self.output_height = output_height
        
        # Frame synchronization
        self.frame_lock = asyncio.Lock()
        self.frame_queue = asyncio.Queue(maxsize=max_queue_size)

        # Topic change event
        self.topic_change_event = asyncio.Event()
        self.current_topic = ""

        # Fixation data
        self.fixation_lock = asyncio.Lock()
        self.fixation_queue = asyncio.Queue(maxsize=1)

        self.gaze_lock = asyncio.Lock()
        self.gaze_queue = asyncio.Queue(maxsize=max_queue_size)
        
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("VideoRenderer started")

        # Start the publishing task
        asyncio.create_task(self.publish_latest_frame())

        # Datapath subscriptions
        await self.message_broker.subscribe("ModuleDatapath/selected_topic_latest_frame", self.handle_selected_topic_latest_frame)
        await self.message_broker.subscribe("ModuleDatapath/normalized_gaze_data", self.handle_gaze_data)
        await self.message_broker.subscribe("ModuleDatapath/normalized_fixation_data", self.handle_fixation_data)


        
        # ModuleController subscriptions
        await self.message_broker.subscribe("ModuleController/selected_video_topic_update", self.handle_topic_update)
        await self.message_broker.subscribe("ModuleController/render_apriltags", self.handle_render_apriltags)
        await self.message_broker.subscribe("ModuleController/processing_mode_update", self.handle_processing_mode_update)

    async def handle_selected_topic_latest_frame(self, topic, message):
        try:
            if not self.topic_change_event.is_set():
                compressed_frame = message["frame"]
                np_arr = np.frombuffer(compressed_frame, np.uint8)
                latest_frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

                # Non-blocking queue management
                try:
                    if self.frame_queue.full():
                        try:
                            self.frame_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    self.frame_queue.put_nowait(latest_frame)
                except asyncio.QueueFull:
                    pass  # Skip frame if queue is full

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

    async def handle_render_apriltags(self, topic, message):
        self.logger.debug(f"Received render_apriltags message: {message}")

        # Update render_apriltags flag, default to False if message is invalid
        self.render_apriltags = message.get("render_apriltags", False)

    async def handle_processing_mode_update(self, topic, message):
        self.logger.debug(f"Received processing mode update: {message}")

        self.processing_mode = message.get("mode", None)

    async def handle_gaze_data(self, topic, message):
        self.logger.debug(f"Received gaze data: {message}")

        # Add gaze data to queue
        async with self.gaze_lock:
            try:
                self.gaze_queue.put_nowait(message)
            except asyncio.QueueFull:
                self.gaze_queue.get_nowait()

    async def handle_fixation_data(self, topic, message):
        self.logger.debug(f"Received fixation data: {message}")
        try:
            # Use non-blocking put for fixation data
            try:
                self.fixation_queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    self.fixation_queue.get_nowait()
                    self.fixation_queue.put_nowait(message)
                except asyncio.QueueEmpty:
                    pass
            
            # Process fixation immediately
            await self.syncronize_fixation_and_frame()

        except Exception as e:
            self.logger.error(f"Error handling fixation data: {str(e)}")

    async def syncronize_fixation_and_frame(self):
        """Synchronize the latest frame with fixation data"""
        try:
            # Try to get frame non-blocking first
            try:
                latest_frame = self.frame_queue.get_nowait()
            except asyncio.QueueEmpty:
                self.logger.warning("No frame available for fixation processing")
                return

            # Try to get fixation data non-blocking
            try:
                latest_fixation = self.fixation_queue.get_nowait()
            except asyncio.QueueEmpty:
                self.logger.warning("No fixation data available")
                return

            if latest_frame is not None and latest_fixation is not None:
                # Convert normalized fixation to pixel coordinates
                fixation_x, fixation_y = latest_fixation["x"], latest_fixation["y"]
                point_x, point_y = self.get_pixel_transformation(fixation_x, fixation_y, latest_frame)
                
                # Create frame copy for publishing
                frame_to_publish = latest_frame.copy()
                
                # Publish synchronized data
                self.logger.debug(f"VideoRenderer: Publishing fixation target at ({point_x}, {point_y})")
                
                # Encode frame before publishing
                _, encoded_frame = cv2.imencode('.jpeg', frame_to_publish, 
                                              [int(cv2.IMWRITE_JPEG_QUALITY), self.image_quality])
                
                await self.message_broker.publish(
                    "VideoRenderer/fixation_target", 
                    {
                        "frame": encoded_frame.tobytes(),
                        "point": {"x": point_x, "y": point_y}
                    }
                )

        except Exception as e:
            self.logger.error(f"Error in synchronize_fixation_and_frame: {str(e)}")

    async def publish_latest_frame(self):
        """Publishes frames to the message broker"""
        blank_frame = None
        try:
            while True:
                if self.processing_mode == ProcessingModes.VIDEO_RENDERING.name:
                    if self.topic_change_event.is_set() or self.frame_queue.empty():
                        if blank_frame is None:
                            blank_frame = np.zeros((self.output_height, self.output_width, 3), np.uint8)
                            cv2.putText(blank_frame, "Nothing to display", 
                                    (self.output_width//2 - 100, self.output_height//2),
                                    cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                        
                        frame_to_publish = blank_frame
                        status = "blank"
                    else:
                        try:
                            frame_to_publish = self.frame_queue.get_nowait()
                            status = "latest"
                        except asyncio.QueueEmpty:
                            frame_to_publish = blank_frame
                            status = "blank"

                    # Process frame with AprilTags if enabled
                    if status == "latest":
                        frame_to_publish = await self.process_frame(frame_to_publish)

                    # Encode frame
                    _, encoded_out = cv2.imencode('.jpeg', frame_to_publish, 
                                                [int(cv2.IMWRITE_JPEG_QUALITY), self.image_quality])
                    
                    await self.message_broker.publish("VideoRender/latest_rendered_frame", 
                                                    {'frame': encoded_out.tobytes()})

                await asyncio.sleep(1/self.video_fps)

        except Exception as e:
            self.logger.error(f"Error in publish_latest_frame: {str(e)}")
            raise e

    async def process_frame(self, frame):
        """Process frame with AprilTags if enabled"""
        if frame is None:
            return frame
            
        if self.render_apriltags:
            # Set frame in apriltag renderer
            self.apriltag_renderer.set_latest_frame(frame)
            # Get processed frame with overlays
            return self.apriltag_renderer.get_latest_frame()

        return frame

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

    def get_pixel_transformation(self, norm_x, norm_y, frame):
        # Convert to pixel coordinates
        # Pupil fixation surface coordinate origin at bottom left corner
        # But OpenCV origin is at top left corner and goes down
        # For easier processing we send point in the same coordinate system as OpenCV
        h, w = frame.shape[:2]
        pixel_x = int(norm_x * w)
        pixel_y = int((1 - norm_y) * h)

        return pixel_x, pixel_y

    async def stop(self):
        self.topic_change_event.set()  # Signal stop
        self.message_broker.stop()


class ModuleController:
    def __init__(self, message_broker: MessageBroker):
        self.message_broker = message_broker

        self.selected_video_topic = ""
        self.gaze_enabled = False

        self.latest_fixation_target = None

        self.logger = logging.getLogger(__name__)

    async def start(self):

        self.logger.info("ModuleController started")

        # Backend subs
        await self.message_broker.subscribe("Backend/selected_video_topic_update", self.handle_selected_video_topic_update)
        await self.message_broker.subscribe("Backend/gaze_enabled_state_update", self.handle_gaze_enabled_state_update)
        await self.message_broker.subscribe("Backend/processing_mode_action", self.handle_processing_mode_action)

        # Video render subs
        await self.message_broker.subscribe("VideoRenderer/fixation_target", self.handle_fixation_target)


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

        # Publish render_apriltags msg
        await self.message_broker.publish("ModuleController/render_apriltags", {"render_apriltags": self.gaze_enabled})

    async def handle_processing_mode_action(self, topic, message):
        action = message.get("action", None)

        self.logger.debug(f"Received action command: {action}")

        if action == ProcessingModeActions.CANCEL.name:
            await self.message_broker.publish("ModuleController/processing_mode_update", {"mode": ProcessingModes.VIDEO_RENDERING.name})
        
    async def handle_fixation_target(self, topic, message):
        self.logger.debug(f"Received fixation target: {message}")

        self.latest_fixation_target = message

        # Update processing mode to initial fixation
        await self.message_broker.publish("ModuleController/processing_mode_update", {"mode": ProcessingModes.INITIAL_FIXATION.name})

        # Pulish data to UserInteractionRenderer
        await self.message_broker.publish("ModuleController/fixation_target", message)


    async def stop(self):
        self.message_broker.stop()


class ModuleDatapath:
    """
    Class to handle data path between modules. Should not do any processing, only pass data between modules.
    """
    def __init__(self, message_broker: MessageBroker):
        self.message_broker = message_broker
        self.video_topics = set() 
        self.selected_video_topic = ""
        self.logger = logging.getLogger(__name__)

    async def start(self):
        self.logger.info("ModuleDatapath started")

        # Webcam subs
        await self.message_broker.subscribe("WebcamModule/latest_frame", self.handle_incoming_stream)

        # Pupil subs
        await self.message_broker.subscribe("PupilMessageParser/normalized_gaze_data", self.publish_pupil_normalized_gaze_data)
        await self.message_broker.subscribe("PupilMessageParser/normalized_fixation_data", self.publish_pupil_normalized_fixation_data)
        
        # ModuleController subs
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
                self.logger.debug(f"New video topic detected: {topic}")
                self.video_topics.add(topic)

            if topic == self.selected_video_topic:
                await self.message_broker.publish("ModuleDatapath/selected_topic_latest_frame", message)

        except Exception as e:
            self.logger.error(f"Error handling incoming stream: {str(e)}")

    async def handle_selected_video_topic_update(self, topic, message):
        self.logger.debug(f"ModuleDatapath: Received selected video topic update: {message}")

        self.selected_video_topic = message["topic"]

    async def publish_pupil_normalized_gaze_data(self, topic, message):
        self.logger.debug(f"ModuleDatapath: Publishing normalized gaze data {message}")
        await self.message_broker.publish("ModuleDatapath/normalized_gaze_data", message)

    async def publish_pupil_normalized_fixation_data(self, topic, message):
        self.logger.debug(f"ModuleDatapath: Publishing normalized fixation data {message}")
        await self.message_broker.publish("ModuleDatapath/normalized_fixation_data", message)


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

        ui_renderer_message_broker = MessageBroker(1024)
        video_renderer_message_broker = MessageBroker(1024)
        module_controller_message_broker = MessageBroker(1024)
        module_datapath_message_broker = MessageBroker(1024*4)
        webcam_module_message_broker = MessageBroker(1024)

        ui_renderer = UserInteractionRenderer(message_broker=ui_renderer_message_broker)

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
            ui_renderer.start(),
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