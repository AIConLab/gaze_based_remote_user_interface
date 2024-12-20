import asyncio
import numpy as np
import zmq.asyncio
import cv2
import logging
import os
import argparse
from datetime import datetime
import time

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
    for logger_name in ['WebcamModule', 'VideoRenderer', 'ModuleController', 'ModuleDatapath', 'UserInteractionRenderer']:
        logging.getLogger(logger_name).setLevel(log_level)


class UserInteractionRenderer:
    def __init__(self,
                  video_fps=15,
                  output_width=1280,
                  output_height=720,
                  message_broker: MessageBroker = None
    ):
        self.message_broker = message_broker

        # Video rendering parameters
        self.output_width = output_width
        self.output_height = output_height
        self.video_fps = video_fps

        # State variables
        self.processing_mode = None

        # Segmentation results data structure
        self.segmentation_results = {"frame" : None,
                                    "masks" : [],
                                    "scores" : [],
                                    "logits" : [],
                                    "point" : None,
                                    "normalized_point" : None}
        self.current_mask_index = 0
        self.latest_rendered_segmentation_frames = None

        # Fixation data
        self.fixation_data = {"frame": None,
                              "x": None, 
                              "y": None,
                                "norm_x": None,
                                "norm_y": None}
        self.latest_rendered_fixation_frame = None

        # Loading animation setup
        self.loading_frames = []
        self.current_loading_frame = 0
        self.loading_frame_count = 8  # Number of rotation steps
        self.setup_loading_animation()

        self.logger = logging.getLogger(self.__class__.__name__)

    async def start(self):
        self.logger.info("UserInteractionRenderer started")

        # Start the publishing task
        asyncio.create_task(self.publish_latest_frame())

        # ModuleController subscriptions
        await self.message_broker.subscribe("ModuleController/processing_mode_update", self.handle_processing_mode_update)
        await self.message_broker.subscribe("ModuleController/fixation_target", self.handle_fixation_target)
        await self.message_broker.subscribe("ModuleController/cycle_segmentation_result", self.handle_cycle_segmentation_result)
        await self.message_broker.subscribe("ModuleController/segmentation_result_selected", self.handle_segmentation_result_selected)

        # ModuleDatapath subscriptions
        await self.message_broker.subscribe("ModuleDatapath/segmentation_results", self.handle_segmentation_results)

    async def handle_processing_mode_update(self, topic, message):
        self.logger.debug(f"Received processing mode update: {message}")

        self.processing_mode = message.get("mode", None)

    async def handle_fixation_target(self, topic, message): 
        self.logger.debug(f"Received fixation target with point")

        self.fixation_data["frame"] = message.get("frame", None)
        self.fixation_data["x"] = message.get("point", {}).get("x", None)
        self.fixation_data["y"] = message.get("point", {}).get("y", None)
        self.fixation_data["norm_x"] = message.get("normalized_point", {}).get("x", None)
        self.fixation_data["norm_y"] = message.get("normalized_point", {}).get("y", None)

        # DEBUG:
        self.logger.info(f"Received fixation target at ({self.fixation_data['x']}, {self.fixation_data['y']}, {self.fixation_data['norm_x']}, {self.fixation_data['norm_y']})")

        await self.process_fixation_frame()

    async def handle_segmentation_results(self, topic, message):
        try:
            self.logger.debug(f"Received segmentation results")
            
            # Store the frame
            self.segmentation_results["frame"] = message["frame"]
            
            # Reconstruct masks from packed bytes
            self.logger.debug(f"Processing {len(message['masks_info'])} masks")
            masks = []
            for mask_info in message["masks_info"]:
                try:
                    mask_bytes = mask_info['data']
                    mask_shape = mask_info['shape']
                    # Unpack the bytes back into uint8 array
                    mask_packed = np.frombuffer(mask_bytes, dtype=np.uint8)
                    mask_unpacked = np.unpackbits(mask_packed)
                    # Reshape and convert to boolean
                    mask = mask_unpacked[:np.prod(mask_shape)].reshape(mask_shape).astype(bool)
                    masks.append(mask)
                except Exception as e:
                    self.logger.error(f"Error processing individual mask: {str(e)}")
                    self.logger.error(f"Mask info: {mask_info}")
                    raise e
            
            self.segmentation_results["masks"] = np.array(masks)
            self.segmentation_results["scores"] = np.array(message["scores"])
            self.segmentation_results["logits"] = np.array(message["logits"])

            # Store points as dictionaries with x,y keys
            point = message["point"]
            normalized_point = message["normalized_point"]
            
            # Ensure points are in dict format with x,y keys
            if isinstance(point, (list, tuple)):
                point = {"x": point[0], "y": point[1]}
            if isinstance(normalized_point, (list, tuple)):
                normalized_point = {"x": normalized_point[0], "y": normalized_point[1]}

            self.segmentation_results["point"] = point
            self.segmentation_results["normalized_point"] = normalized_point

            self.logger.debug(f"Successfully reconstructed {len(masks)} masks")
            await self.process_segmentation_results()

        except Exception as e:
            self.logger.error(f"Error handling segmentation results: {str(e)}")
            self.logger.error(f"Message content: {message.keys()}")
            raise e

    async def handle_segmentation_result_selected(self, topic, message):
        self.logger.debug("Segmentation result selected")

        try:
            # Get the selected mask
            frame = self.segmentation_results["frame"]
            mask = self.segmentation_results["masks"][self.current_mask_index]
            mask_info = await self.encode_mask(mask)

            # Get points - ensure they're in dict format
            point = self.segmentation_results["point"]
            normalized_point = self.segmentation_results["normalized_point"]

            # Debug log the point data
            self.logger.debug(f"Point data being sent: {point}")
            self.logger.debug(f"Normalized point data being sent: {normalized_point}")

            self.logger.info(f"Selected mask {self.current_mask_index + 1}/{len(self.segmentation_results['masks'])}")

            # Create output message
            output_message = {
                "frame": frame,
                "mask": mask_info,
                "point": [point["x"], point["y"]],  # Convert to list format
                "normalized_point": [normalized_point["x"], normalized_point["y"]]  # Convert to list format
            }

            await self.message_broker.publish("UserInteractionRenderer/segmentation_selection", output_message)

        except Exception as e:
            self.logger.error(f"Error in handle_segmentation_result_selected: {str(e)}")
            raise

    async def handle_cycle_segmentation_result(self, topic, message):
        self.logger.debug(f"Received cycle segmentation result: {message}")

        direction = message.get("direction", None)

        if direction == "left":
            self.current_mask_index = (self.current_mask_index - 1) % len(self.segmentation_results["masks"])
        elif direction == "right":
            self.current_mask_index = (self.current_mask_index + 1) % len(self.segmentation_results["masks"])

    async def publish_latest_frame(self):
        while True:
            try:
                if self.processing_mode == ProcessingModes.INITIAL_FIXATION.name:
                    if self.latest_rendered_fixation_frame is not None:
                        # Encode frame before publishing
                        _, encoded_frame = cv2.imencode('.jpeg', self.latest_rendered_fixation_frame, 
                                                      [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                        
                        await self.message_broker.publish("UserInteractionRenderer/latest_rendered_frame", 
                                                        {'frame': encoded_frame.tobytes()})

                elif self.processing_mode == ProcessingModes.PROCESSING.name:

                    # Show animated loading frame
                    loading_frame = self.loading_frames[self.current_loading_frame].copy()
                    self.current_loading_frame = (self.current_loading_frame + 1) % self.loading_frame_count
                    
                    _, encoded_frame = cv2.imencode('.jpeg', loading_frame, 
                                                  [int(cv2.IMWRITE_JPEG_QUALITY), 70])

                    await self.message_broker.publish("UserInteractionRenderer/latest_rendered_frame", {'frame': encoded_frame.tobytes()})

                elif self.processing_mode == ProcessingModes.SEGMENTATION_RESULTS.name:
                    if self.latest_rendered_segmentation_frames:
                        # Encode frame before publishing
                        _, encoded_frame = cv2.imencode('.jpeg', self.latest_rendered_segmentation_frames[self.current_mask_index], 
                                                      [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                        
                        await self.message_broker.publish("UserInteractionRenderer/latest_rendered_frame", 
                                                        {'frame': encoded_frame.tobytes()})
                    

                await asyncio.sleep(1/self.video_fps)

            except Exception as e:
                self.logger.error(f"Error in publish_latest_frame: {str(e)}")
                raise e
    
    async def process_fixation_frame(self):
        if self.fixation_data["frame"] is not None:
            # Decode the bytes back into an image
            np_arr = np.frombuffer(self.fixation_data["frame"], np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            
            self.latest_rendered_fixation_frame = frame.copy()
            
            if self.fixation_data["x"] is not None and self.fixation_data["y"] is not None:
                # Draw fixation point on frame
                point_x, point_y = self.fixation_data["x"], self.fixation_data["y"]
                cv2.circle(self.latest_rendered_fixation_frame, (point_x, point_y), 10, (0, 0, 255), -1)
        else:
            raise ValueError("Invalid fixation frame")

    async def process_segmentation_results(self):
        """
        Process segmentation results by drawing masks and points on the frame.
        Creates a rendered frame for each mask result.
        """
        if self.segmentation_results["frame"] is not None:
            try:
                # Decode the bytes back into an image
                np_arr = np.frombuffer(self.segmentation_results["frame"], np.uint8)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                
                # Initialize list to store rendered frames
                rendered_frames = []
                
                masks = self.segmentation_results["masks"]
                scores = self.segmentation_results["scores"]
                
                for mask_idx, (mask, score) in enumerate(zip(masks, scores)):
                    # Create a copy of the frame for this mask
                    rendered_frame = frame.copy()
                    
                    # Convert mask to proper format and color
                    mask_colored = np.zeros_like(rendered_frame, dtype=np.uint8)
                    mask_colored[mask] = [30, 144, 255]  # Light blue color
                    
                    # Add transparency to the mask
                    alpha = 0.6
                    rendered_frame = cv2.addWeighted(rendered_frame, 1, mask_colored, alpha, 0)
                    
                    # Draw mask border
                    contours, _ = cv2.findContours(mask.astype(np.uint8), 
                                                cv2.RETR_EXTERNAL, 
                                                cv2.CHAIN_APPROX_NONE)
                    cv2.drawContours(rendered_frame, contours, -1, (255, 255, 255), 2)
                    
                    # Add score text in top-right corner
                    score_text = f"Score: {score:.3f}"
                    text_size = cv2.getTextSize(score_text, cv2.FONT_HERSHEY_SIMPLEX, 1, 2)[0]
                    text_x = rendered_frame.shape[1] - text_size[0] - 10
                    text_y = text_size[1] + 10
                    cv2.putText(rendered_frame, score_text, (text_x, text_y),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                    
                    # Add mask number indicator
                    mask_text = f"Mask {mask_idx + 1}/{len(masks)}"
                    cv2.putText(rendered_frame, mask_text, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)


                    # Add  point as a bright red circle on the frame
                    point = self.segmentation_results["point"]
                    cv2.circle(rendered_frame, (point["x"], point["y"]), 5, (0, 0, 255), -1)
                    
                    rendered_frames.append(rendered_frame)
                
                # Store the rendered frames
                self.latest_rendered_segmentation_frames = rendered_frames
                
            except Exception as e:
                self.logger.error(f"Error processing segmentation results: {str(e)}")
                raise e

        else:
            
            raise ValueError("Invalid segmentation frame")

    async def decode_masks(self, masks_info):
        masks = []
        for mask_info in masks_info:
            mask_bytes = mask_info['data']
            mask_shape = mask_info['shape']
            # Unpack the bytes back into uint8 array
            mask_packed = np.frombuffer(mask_bytes, dtype=np.uint8)
            mask_unpacked = np.unpackbits(mask_packed)
            # Reshape and convert to boolean
            mask = mask_unpacked[:np.prod(mask_shape)].reshape(mask_shape).astype(bool)
            masks.append(mask)

        # DEBUG print unpacking info
        self.logger.info(f"Unpacked {len(masks)} masks")
        self.logger.info(f"First mask shape: {masks[0].shape}")


        return masks

    async def encode_mask(self, mask):
        # Ensure mask is boolean type
        bool_mask = mask.astype(bool)
        # Convert to uint8 for packbits
        uint8_mask = bool_mask.astype(np.uint8)
        # Pack the bits
        mask_bytes = np.packbits(uint8_mask).tobytes()
        mask_shape = bool_mask.shape

        mask_info = {
            'data': mask_bytes,
            'shape': mask_shape
        }

        # DEBUG print packing info
        self.logger.info(f"Mask shape: {mask_shape}, packed size: {len(mask_bytes)} bytes")
        # First 10 bytes of the mask
        self.logger.debug(f"First 10 bytes: {mask_bytes[:10]}")

        return mask_info
    
    def setup_loading_animation(self):
        """Creates a sequence of rotated loading frames"""
        # Create base loading frame with correct dimensions (height, width)
        base_frame = np.zeros((self.output_height, self.output_width, 3), dtype=np.uint8)
        
        # Calculate center based on frame dimensions
        center = (self.output_width // 2, self.output_height // 2)
        radius = min(self.output_width, self.output_height) // 8
        
        # Create loading indicator segments
        for i in range(self.loading_frame_count):
            frame = base_frame.copy()
            angle = i * (360 // self.loading_frame_count)
            end_angle = angle + 30  # Size of arc segment
            
            # Draw arc with varying intensity
            for j in range(self.loading_frame_count):
                curr_angle = (angle + j * (360 // self.loading_frame_count)) % 360
                curr_end = curr_angle + 30
                intensity = 255 - (j * (255 // self.loading_frame_count))
                cv2.ellipse(frame, center, (radius, radius), 0, curr_angle, curr_end, 
                        (intensity, intensity, intensity), thickness=3)

            # Add text centered below the loading circle
            text = "Processing"
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = 1
            thickness = 2
            
            # Get text size
            (text_width, text_height), baseline = cv2.getTextSize(text, font, font_scale, thickness)
            
            # Calculate text position to center it
            text_x = (self.output_width - text_width) // 2
            text_y = center[1] + radius + text_height + 20  # 20 pixels below the circle
            
            cv2.putText(frame, text, (text_x, text_y), 
                        font, font_scale, (255, 255, 255), thickness)

            self.loading_frames.append(frame)

    async def stop(self):
        self.message_broker.stop()


class VideoRenderer:
    def __init__(self,
                  video_fps=15, 
                  output_width=1280, 
                  output_height=720, 
                  max_queue_size=5,
                  gaze_trail_length=30,
                  message_broker: MessageBroker = None,
                  image_quality=70
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
        self.image_quality = image_quality
        self.video_fps = video_fps
        self.output_width = output_width
        self.output_height = output_height
        
        # Frame synchronization
        self.frame_lock = asyncio.Lock()
        self.frame_queue = asyncio.Queue(maxsize=max_queue_size)

         # Add last valid frame cache
        self.last_valid_frame = None
        self.last_valid_frame_lock = asyncio.Lock()
        self.frame_timeout = 2.0  # Time in seconds before considering connection lost
        self.last_frame_time = 0

        # Topic change event
        self.topic_change_event = asyncio.Event()
        self.current_topic = ""

        # Fixation data
        self.fixation_lock = asyncio.Lock()
        self.fixation_queue = asyncio.Queue(maxsize=1)

        self.gaze_lock = asyncio.Lock()
        self.gaze_queue = asyncio.Queue(maxsize=max_queue_size)

        self.latest_frame = None
        self.latest_frame_lock = asyncio.Lock()


        # Gaze visualization parameters
        self.render_gaze = False

        self.gaze_trail_length = gaze_trail_length
        self.gaze_points = []  # List of (timestamp, x, y, alpha) tuples
        self.gaze_fade_duration = 1  # seconds
        self.gaze_point_size = 10
        self.gaze_color = (0, 255, 0)  # Green color for gaze
        self.last_gaze_cleanup = 0
        self.gaze_cleanup_interval = 0.5  # seconds
        
        self.logger = logging.getLogger(self.__class__.__name__)

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
        await self.message_broker.subscribe("ModuleController/gaze_enabled_state_update", self.handle_gaze_enabled_state_update)
        await self.message_broker.subscribe("ModuleController/processing_mode_update", self.handle_processing_mode_update)

    async def handle_selected_topic_latest_frame(self, topic, message):
        try:
            if not self.topic_change_event.is_set():
                compressed_frame = message["frame"]
                np_arr = np.frombuffer(compressed_frame, np.uint8)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

                # Update last valid frame and timestamp
                async with self.last_valid_frame_lock:
                    self.last_valid_frame = frame
                    self.last_frame_time = time.time()

                # Store the latest frame with lock protection
                async with self.latest_frame_lock:
                    self.latest_frame = frame

                # Maintain frame queue
                try:
                    if self.frame_queue.full():
                        try:
                            self.frame_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    self.frame_queue.put_nowait(frame)
                except asyncio.QueueFull:
                    pass

        except Exception as e:
            self.logger.error(f"Error handling incoming frame: {str(e)}")

    async def handle_topic_update(self, topic, message):
            self.logger.debug(f"Topic update received: {message}")
            
            async with self.frame_lock:
                self.topic_change_event.set()  # Signal topic change
                new_topic = message.get("topic", "")
                
                if new_topic != self.current_topic:
                    self.current_topic = new_topic
                    # Clear existing frames and latest frame
                    async with self.latest_frame_lock:
                        self.latest_frame = None
                    while not self.frame_queue.empty():
                        try:
                            self.frame_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    
                    await asyncio.sleep(0.1)  # Small delay for synchronization
                    self.topic_change_event.clear()  # Clear topic change flag

    async def handle_gaze_enabled_state_update(self, topic, message):
        self.logger.debug(f"Received gaze enabled state update: {message}")
        gaze_enabled = message.get("gaze_enabled", False)
        
        # Update both rendering flags
        self.render_apriltags = gaze_enabled
        self.render_gaze = gaze_enabled
        
        if not gaze_enabled:
            # Clear gaze points when disabled
            self.gaze_points = []

    async def handle_processing_mode_update(self, topic, message):
        self.logger.debug(f"Received processing mode update: {message}")

        self.processing_mode = message.get("mode", None)

    async def handle_gaze_data(self, topic, message):
        if not self.render_gaze:
            return
            
        try:
            timestamp = message.get("timestamp", time.time())
            gaze_x = message.get("x", 0)
            gaze_y = message.get("y", 0)
            
            # Convert normalized gaze coordinates to pixel coordinates
            async with self.latest_frame_lock:
                if self.latest_frame is not None:
                    h, w = self.latest_frame.shape[:2]
                    pixel_x = int(gaze_x * w)
                    pixel_y = int((1 - gaze_y) * h)  # Flip Y coordinate
                    
                    # Add new gaze point with full opacity
                    self.gaze_points.append((timestamp, pixel_x, pixel_y, 1.0))
                    
                    # Maintain fixed buffer size
                    if len(self.gaze_points) > self.gaze_trail_length:
                        self.gaze_points.pop(0)

        except Exception as e:
            self.logger.error(f"Error handling gaze data: {str(e)}")

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

    async def publish_latest_frame(self):
        """Publishes frames to the message broker with frame caching"""
        try:
            while True:
                if self.processing_mode == ProcessingModes.VIDEO_RENDERING.name:
                    current_time = time.time()
                    frame_to_publish = None
                    
                    # Check if we have a recent valid frame
                    async with self.last_valid_frame_lock:
                        if (self.last_valid_frame is not None and 
                            current_time - self.last_frame_time < self.frame_timeout):
                            frame_to_publish = self.last_valid_frame.copy()
                            
                            # Add "Delayed Feed" indicator if frame is old
                            if current_time - self.last_frame_time > 0.5:  # Add warning after 500ms
                                cv2.putText(frame_to_publish, "Delayed Feed", 
                                          (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                                          1, (0, 0, 255), 2)

                    # If no valid cached frame, show blank frame
                    if frame_to_publish is None:
                        frame_to_publish = np.zeros((self.output_height, self.output_width, 3), np.uint8)
                        cv2.putText(frame_to_publish, "No Signal", 
                                  (self.output_width//2 - 100, self.output_height//2),
                                  cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)

                    # Process frame with AprilTags if enabled
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



        except Exception as e:
            self.logger.error(f"Error in publish_latest_frame: {str(e)}")
            raise e

    async def process_frame(self, frame):
        """Process frame with AprilTags and gaze visualization if enabled"""
        if frame is None:
            return frame
            
        # Create a copy of the frame for modification
        processed_frame = frame.copy()
        
        # Draw gaze trail if enabled and we have points
        if self.render_gaze and self.gaze_points:
            await self.cleanup_gaze_points()  # Regularly cleanup old points
            
            # Create a separate overlay for all gaze points
            overlay = np.zeros_like(processed_frame, dtype=np.uint8)
            
            # Draw all points on the overlay
            for timestamp, x, y, alpha in self.gaze_points:
                # Draw the gaze point
                cv2.circle(overlay, (x, y), 
                          self.gaze_point_size, 
                          self.gaze_color, 
                          -1)  # Filled circle
                
                # Draw a connecting line to the previous point if it exists
                if len(self.gaze_points) > 1:
                    points = np.array([[p[1], p[2]] for p in self.gaze_points], np.int32)
                    cv2.polylines(overlay, [points], False, self.gaze_color, 2)
            
            # Apply the overlay with transparency
            cv2.addWeighted(processed_frame, 1, overlay, 0.6, 0, processed_frame)

        # Apply AprilTags if enabled
        if self.render_apriltags:
            self.apriltag_renderer.set_latest_frame(processed_frame)
            processed_frame = self.apriltag_renderer.get_latest_frame()

        return processed_frame

    async def syncronize_fixation_and_frame(self):
        """Synchronize the latest frame with fixation data"""
        try:
            # Try to get fixation data non-blocking
            try:
                latest_fixation = self.fixation_queue.get_nowait()
            except asyncio.QueueEmpty:
                self.logger.debug("No fixation data available")
                return

            # Get the latest frame using the lock
            async with self.latest_frame_lock and self.fixation_lock:
                if self.latest_frame is None:
                    self.logger.debug("No frame available for fixation processing")
                    return
                frame_to_use = self.latest_frame.copy()

            if latest_fixation is not None:
                # Convert normalized fixation to pixel coordinates
                fixation_x, fixation_y = latest_fixation["x"], latest_fixation["y"]
                point_x, point_y = self.get_pixel_transformation(fixation_x, fixation_y, frame_to_use)
                
                # Add fixation point visualization if needed
                cv2.circle(frame_to_use, (point_x, point_y), 5, (0, 0, 255), -1)
                
                # Encode frame before publishing
                _, encoded_frame = cv2.imencode('.jpeg', frame_to_use, 
                                              [int(cv2.IMWRITE_JPEG_QUALITY), self.image_quality])
                
                
                # Publish the frame with normalized fixation points and converted pixel fixation points
                await self.message_broker.publish(
                    "VideoRenderer/fixation_target", 
                    {
                        "frame": encoded_frame.tobytes(),
                        "point": {"x": point_x, "y": point_y},
                        "normalized_point": {"x": fixation_x, "y": fixation_y}
                    }
                )

        except Exception as e:
            self.logger.error(f"Error in synchronize_fixation_and_frame: {str(e)}")

    async def cleanup_gaze_points(self):
        """Cleanup old gaze points based on timestamp"""
        current_time = time.time()
        
        if current_time - self.last_gaze_cleanup > self.gaze_cleanup_interval:
            # Remove points older than fade duration
            self.gaze_points = [
                (ts, x, y, 1.0 - (current_time - ts) / self.gaze_fade_duration)
                for ts, x, y, _ in self.gaze_points
                if current_time - ts < self.gaze_fade_duration
            ]
            self.last_gaze_cleanup = current_time

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

        self.logger = logging.getLogger(self.__class__.__name__)

    async def start(self):

        self.logger.info("ModuleController started")

        # Backend subs
        await self.message_broker.subscribe("Backend/selected_video_topic_update", self.handle_selected_video_topic_update)
        await self.message_broker.subscribe("Backend/gaze_enabled_state_update", self.handle_gaze_enabled_state_update)
        await self.message_broker.subscribe("Backend/processing_mode_action", self.handle_processing_mode_action)

        # Segmentation subs
        await self.message_broker.subscribe("SegmentationMessenger/segmentation_complete", self.handle_segmentation_complete)

        # User render subs
        await self.message_broker.subscribe("UserInteractionRenderer/segmentation_selection", self.handle_segmentation_result_selected)

        # Video render subs
        await self.message_broker.subscribe("VideoRenderer/fixation_target", self.handle_fixation_target)

    async def handle_segmentation_complete(self, topic, message):
        if message["success"]:
            await self.message_broker.publish("ModuleController/processing_mode_update", {"mode": ProcessingModes.SEGMENTATION_RESULTS.name})

        else:
            await self.message_broker.publish("ModuleController/processing_mode_update", {"mode": ProcessingModes.VIDEO_RENDERING.name})

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
        await self.message_broker.publish("ModuleController/gaze_enabled_state_update", {"gaze_enabled": self.gaze_enabled})

    async def handle_processing_mode_action(self, topic, message):
        action = message.get("action", None)

        self.logger.debug(f"Received action command: {action}")

        if action == ProcessingModeActions.CANCEL.name:
            await self.message_broker.publish("ModuleController/processing_mode_update", {"mode": ProcessingModes.VIDEO_RENDERING.name})

        elif action == ProcessingModeActions.MAKE_WAYPOINT.name:
            await self.message_broker.publish("ModuleController/processing_mode_update", {"mode": ProcessingModes.PROCESSING.name})

        elif action == ProcessingModeActions.SEGMENT.name:
            # Send mode update
            await self.message_broker.publish("ModuleController/processing_mode_update", {"mode": ProcessingModes.PROCESSING.name})

            # Send segmentation command
            """
            Note message input is like:
                await self.message_broker.publish(
                    "VideoRenderer/fixation_target", 
                    {
                        "frame": encoded_frame.tobytes(),
                        "point": {"x": point_x, "y": point_y},
                        "normalized_point": {"x": fixation_x, "y": fixation_y}
                    }
                )
            """
            frame = self.latest_fixation_target["frame"]
            point = self.latest_fixation_target["point"]
            normalized_point = self.latest_fixation_target["normalized_point"]

            segmentation_message = {"frame": frame, "point": point, "normalized_point": normalized_point}

            # DEBUG
            self.logger.info(f"Sending segmentation request with point {point} and normalized point {normalized_point}")

            await self.message_broker.publish("ModuleController/segment_frame", segmentation_message)


        elif action == ProcessingModeActions.ACCEPT.name:
            await self.message_broker.publish("ModuleController/segmentation_result_selected", {})

        elif action == ProcessingModeActions.CYCLE_LEFT.name:
            await self.message_broker.publish("ModuleController/cycle_segmentation_result", {"direction": "left"})

        elif action == ProcessingModeActions.CYCLE_RIGHT.name:
            await self.message_broker.publish("ModuleController/cycle_segmentation_result", {"direction": "right"})

        else:
            self.logger.warning(f"Received invalid action command {action}")

    async def handle_fixation_target(self, topic, message):
        self.latest_fixation_target = message

        # Update processing mode to initial fixation
        await self.message_broker.publish("ModuleController/processing_mode_update", {"mode": ProcessingModes.INITIAL_FIXATION.name})

        # Pulish data to UserInteractionRenderer
        await self.message_broker.publish("ModuleController/fixation_target", message)

    async def handle_segmentation_result_selected(self, topic, message):
        self.logger.debug("Segmentation result selected")

        # Go to default video rendering mode
        await self.message_broker.publish("ModuleController/processing_mode_update", {"mode": ProcessingModes.VIDEO_RENDERING.name})

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
        self.logger = logging.getLogger(self.__class__.__name__)

    async def start(self):
        self.logger.info("ModuleDatapath started")

        # Webcam subs
        await self.message_broker.subscribe("WebcamModule/latest_frame", self.handle_incoming_video_streams)

        # Pupil subs
        await self.message_broker.subscribe("PupilMessageParser/normalized_gaze_data", self.publish_pupil_normalized_gaze_data)
        await self.message_broker.subscribe("PupilMessageParser/normalized_fixation_data", self.publish_pupil_normalized_fixation_data)
        await self.message_broker.subscribe("PupilMessageParser/world_frame", self.handle_incoming_video_streams)

        # Ros Sub handler subs
        await self.message_broker.subscribe("RosSubHandler/rear_realsense_frame", self.handle_incoming_video_streams)
        await self.message_broker.subscribe("RosSubHandler/front_realsense_frame", self.handle_incoming_video_streams)
        await self.message_broker.subscribe("RosSubHandler/axis_frame", self.handle_incoming_video_streams)
        
        # ModuleController subs
        await self.message_broker.subscribe("ModuleController/selected_video_topic_update", self.handle_selected_video_topic_update)
        asyncio.create_task(self.publish_available_topics())


        # Segmentation subs
        await self.message_broker.subscribe("SegmentationMessenger/segmentation_result", self.handle_segmentation_results)

    async def publish_available_topics(self):
        while True:
            await self.message_broker.publish("ModuleDatapath/available_video_topics", {"topics": list(self.video_topics)})
            await asyncio.sleep(1)
        
    async def handle_incoming_video_streams(self, topic, message):
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

    async def handle_segmentation_results(self, topic, message):
        """
        Handle incoming segmentation results
        """
        try:
            await self.message_broker.publish("ModuleDatapath/segmentation_results", message)

        except Exception as e:
            self.logger.error(f"Error handling segmentation results: {str(e)}")
            raise e

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
        video_renderer_message_broker = MessageBroker(1024*4)
        module_controller_message_broker = MessageBroker(1024)
        module_datapath_message_broker = MessageBroker(1024*4)
        webcam_module_message_broker = MessageBroker(1024)

        ui_renderer = UserInteractionRenderer(message_broker=ui_renderer_message_broker)

        video_renderer = VideoRenderer(video_fps=15, 
                                message_broker=video_renderer_message_broker,
                                output_width=1280, 
                                output_height=720,
                                gaze_trail_length=30,
                                image_quality=50)

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