import asyncio
import cv2
import pytesseract
import numpy as np
import re
from PIL import Image
from message_broker import MessageBroker
import logging

class OCRModule:
    def __init__(self, message_broker: MessageBroker):
        self.message_broker = message_broker
        self.logger = logging.getLogger('OCRModule')

    async def start(self):
        self.logger.info("OCRModule started")
        # Subscribe to receive frames for OCR processing
        await self.message_broker.subscribe("ModuleController/start_ocr", self.handle_start_ocr)

    async def handle_start_ocr(self, topic, message):
        try:
            # Assuming we receive a frame for OCR
            frame_data = message.get("frame", None)
            if frame_data is None:
                raise ValueError("No frame data provided for OCR")

            np_arr = np.frombuffer(frame_data, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            # Perform OCR
            latitude, longitude = self.perform_ocr(frame)
            if latitude and longitude:
                self.logger.info(f"OCR Result - Latitude: {latitude}, Longitude: {longitude}")

                # Publish the OCR results
                await self.message_broker.publish("OCRModule/ocr_results", {
                    "latitude": latitude,
                    "longitude": longitude
                })
            else:
                self.logger.info("No GPS information found in OCR result.")

        except Exception as e:
            self.logger.error(f"Error handling OCR: {str(e)}")

    def perform_ocr(self, frame):
        # Crop the region of interest for OCR
        cropped_image = frame[267:444, 844:1190]  # Example coordinates
        img1 = np.array(cropped_image)
        text = pytesseract.image_to_string(img1)

        # Regular expression to match the GPS coordinates
        pattern = r"(\d+\.\d+ [NS]), (\d+\.\d+ [EW])"
        match = re.search(pattern, text)

        if match:
            latitude = match.group(1)
            longitude = match.group(2)
            return latitude, longitude
        else:
            return None, None

    async def stop(self):
        self.message_broker.stop()


# To run the OCR module, instantiate and call start()
# Example:
# ocr_module = OCRModule(message_broker)
# asyncio.run(ocr_module.start())

