import argparse


import numpy as np
import torch
from PIL import Image

from sam2.build_sam import build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor

import asyncio
import numpy as np
import zmq.asyncio
import logging
import os
import argparse
from datetime import datetime

import cv2

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
    for logger_name in ['SegmentationModel', 'SegmentationMessenger']:
        logging.getLogger(logger_name).setLevel(log_level)


class SegmentationModel:
    def __init__(self):
        try:
            self.logger = logging.getLogger(self.__class__.__name__)

            self.sam2_checkpoint = "/sam2/checkpoints/sam2.1_hiera_tiny.pt"
            self.model_cfg = "/configs/sam2.1/sam2.1_hiera_t.yaml"

            self.device = torch.device("cpu")
            self.sam2_model = None
            self.predictor = None

            self.input_label = np.array([1]) 

            self.sam2_model = build_sam2(self.model_cfg, 
                                    self.sam2_checkpoint, 
                                    device=self.device)
            self.predictor = SAM2ImagePredictor(self.sam2_model)

        except Exception as e:
            self.logger.error(f"Error initializing SegmentationModel: {e}")


    async def get_masks(self, image, input_point):
        try:
            self.logger.info("Getting masks")
            
            # Decode the bytes back into an image for SAM
            np_arr = np.frombuffer(image, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            
            # Convert input point from dict to numpy array if needed
            if isinstance(input_point, dict):
                point_array = np.array([[input_point['x'], input_point['y']]])
            elif isinstance(input_point, np.ndarray):
                point_array = input_point
            else:
                raise ValueError(f"Unexpected input_point type: {type(input_point)}")

            # Set the image in predictor first
            self.predictor.set_image(frame)

            masks, scores, logits = self.predictor.predict(
                point_coords=point_array,
                point_labels=self.input_label,
                multimask_output=True,
            )

            sorted_ind = np.argsort(scores)[::-1]
            masks = masks[sorted_ind]
            scores = scores[sorted_ind]
            logits = logits[sorted_ind]

            self.logger.debug(f"Processed {len(masks)} masks")
            return masks, scores, logits

        except Exception as e:
            self.logger.error(f"Error getting masks: {e}")
            raise e  # Re-raise to ensure proper error handling upstream


class SegmentationMessenger:
    def __init__(self, 
                 segmentation_model: SegmentationModel=None, 
                 message_broker: MessageBroker=None):
        try:
            self.logger = logging.getLogger(self.__class__.__name__)
            self.segmentation_model = segmentation_model
            self.message_broker = message_broker

        except Exception as e:
            self.logger.error(f"Error initializing SegmentationMessenger: {e}")

    async def start(self):
        self.logger.info("Starting SegmentationMessenger")
        await self.message_broker.subscribe("ModuleController/segment_frame", self.handle_segment_frame)

    async def handle_segment_frame(self, topic, message):
        try:
            self.logger.info(f"Received message on topic {topic}")

            image = message["frame"]
            input_point = message["point"]

            try:
                masks, scores, logits = await self.segmentation_model.get_masks(image, input_point)
                
                # Convert masks to boolean type and serialize - process all masks
                masks_list = []
                for mask in masks:
                    mask_info = await self.encode_mask(mask)
                    masks_list.append(mask_info)
                    
                self.logger.debug(f"Processed {len(masks_list)} masks")

                # Create message payload
                message = {
                    "frame": image,
                    "masks_info": masks_list,
                    "scores": scores.tolist(),
                    "logits": logits.tolist()
                }

                self.logger.debug("Publishing segmentation results")
                await self.message_broker.publish("SegmentationMessenger/segmentation_result", message)
                await self.message_broker.publish("SegmentationMessenger/segmentation_complete", {"success": True})
                
            except Exception as e:
                self.logger.error(f"Error in segmentation process: {str(e)}")
                self.logger.error(f"Error details: {str(e)}")
                await self.message_broker.publish("SegmentationMessenger/segmentation_complete", {"success": False})
                raise e
                
        except Exception as e:
            self.logger.error(f"Error handling message: {str(e)}")
            raise e
    
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

        return mask_info

    async def stop(self):
        self.message_broker.stop()



async def main(enable_logging):
    try:
        setup_logging(enable_logging)

        segmentation_model = SegmentationModel()

        message_broker = MessageBroker(1024 * 2)
        messenger = SegmentationMessenger(message_broker=message_broker, segmentation_model=segmentation_model)

        await messenger.start()

        # Keep the main coroutine running
        while True:
            await asyncio.sleep(1)

    except Exception as e:
        logging.error(f"Error starting message parser: {e}")

    finally:
        await messenger.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true',
                        help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))