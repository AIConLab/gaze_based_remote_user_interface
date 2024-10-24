import zmq
import msgpack
import logging
import os
import asyncio
from datetime import datetime

from message_broker import MessageBroker

"""
https://docs.pupil-labs.com/core/terminology/

Surface (AOI) Coordinate System

Surfaces - also known as areas of interest or AOIs - define their own local coordinate system. For each scene image that includes sufficient surface markers, the Surface Tracker plugin calculates the respective transformation function between the scene camera and surface coordinate system. The surface preview (red rectangle overlay) uses its inner triangle to indicate up/top within the local surface coordinate system. Pupil Capture and Player automatically map gaze and fixation data to surface coordinates if a valid surface transformation is available.

The surface-normalized coordinates of mapped gaze/fixations have the following properties:

    origin: bottom left (pay attention to red triangle indicating surface up/top side)
    unit: surface width/height
    bounds (if gaze/fixation is on AOI):
        x: [0, 1], y: [0, 1]
    example: (0.5, 0.5) (surface center)

The lens distortion (camera intrinsics) are compensated for during the process of mapping data from the scene image space to the surface coordinate system. In other words, the surface coordinate system is not affected by scene camera lens distortion.
"""


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



class PupilMessageParser:
    def __init__(self, surface_name="Surface 1", pupil_ip='127.0.0.1', pupil_port=50020, message_broker: MessageBroker = None):
        self.logger = logging.getLogger(__name__)
        self.surface_name = surface_name
        self.pupil_ip = pupil_ip
        self.pupil_port = pupil_port
        
        # Setup ZMQ context for Pupil
        self.pupil_context = zmq.Context()
        self.pupil_remote = None
        self.pupil_subscriber = None
        
        # Setup async message broker for distributed system
        self.message_broker = message_broker         
        self.running = False

    async def start(self):
        """Start the message parser"""
        try:
            # Connect to Pupil
            self._connect_to_pupil()
            
            # Start processing
            self.running = True
            await self._process_messages()

            self.logger.info("Message parser started")

        except Exception as e:
            self.logger.error(f"Error starting parser: {e}")
            await self.stop()

    async def stop(self):
        """Stop the message parser"""
        self.running = False
        self._close_pupil_connections()
        await self.message_broker.stop()

        self.logger.info("Message parser stopped")

    def _connect_to_pupil(self):
        """Establish connection to Pupil Capture"""
        try:
            # Connect to Pupil Remote
            self.pupil_remote = self.pupil_context.socket(zmq.REQ)
            self.pupil_remote.connect(f'tcp://{self.pupil_ip}:{self.pupil_port}')

            # Get subscriber port
            self.pupil_remote.send_string('SUB_PORT')
            sub_port = self.pupil_remote.recv_string()

            # Connect subscriber
            self.pupil_subscriber = self.pupil_context.socket(zmq.SUB)
            self.pupil_subscriber.connect(f'tcp://{self.pupil_ip}:{sub_port}')
            self.pupil_subscriber.subscribe('surfaces')

            self.logger.info(f"Connected to Pupil Capture on ports {self.pupil_port}/{sub_port}")
        except Exception as e:
            self.logger.error(f"Failed to connect to Pupil: {e}")
            raise

    async def _process_messages(self):
        """Main message processing loop"""
        while self.running:
            try:
                # Use non-blocking receive to allow for clean shutdown
                if self.pupil_subscriber.poll(timeout=100):
                    topic = self.pupil_subscriber.recv_string()
                    payload = msgpack.unpackb(self.pupil_subscriber.recv(), raw=False)
                    
                    # Process surface data
                    if payload.get('name') == self.surface_name:
                        await self._publish_gaze_data(payload)
                        await self._publish_fixation_data(payload)
                
                await asyncio.sleep(0.01)  # Small delay to prevent CPU spinning
                
            except Exception as e:
                self.logger.error(f"Error processing message: {e}")
                await asyncio.sleep(1)  # Delay before retry

    async def _publish_gaze_data(self, payload):
        """Extract and publish gaze data"""
        try:
            gaze_data = payload.get('gaze_on_surfaces', [])
            if gaze_data:
                latest_gaze = gaze_data[-1]
                gaze_x, gaze_y = latest_gaze.get('norm_pos', (None, None))
                
                # Process gaze data
                if gaze_x is not None and gaze_y is not None:

                    # Filter nonnormalized gaze data
                    if not (gaze_x < 0 or gaze_x > 1 or gaze_y < 0 or gaze_y > 1):
                        message = {
                            'timestamp': payload.get('timestamp'),
                            'x': gaze_x,
                            'y': gaze_y
                        }
                        await self.message_broker.publish('PupilMessageParser/normalized_gaze_data', message)

                        self.logger.debug(f"Published gaze data: {message}")

                    else:
                        self.logger.debug(f"Invalid gaze data: {gaze_x}, {gaze_y}")

        except Exception as e:
            self.logger.error(f"Error publishing gaze data: {e}")

    async def _publish_fixation_data(self, payload):
        """Extract and publish fixation data"""
        try:
            fixation_data = payload.get('fixations_on_surfaces', [])

            if fixation_data:
                latest_fixation = fixation_data[-1]
                fix_x, fix_y = latest_fixation.get('norm_pos', (None, None))
                
                # Process fixation data
                if fix_x is not None and fix_y is not None:

                    # Filter nonnormalized fixation data
                    if not (fix_x < 0 or fix_x > 1 or fix_y < 0 or fix_y > 1):
                        message = {
                            'timestamp': payload.get('timestamp'),
                            'x': fix_x,
                            'y': fix_y
                        }
                        await self.message_broker.publish('PupilMessageParser/normalized_fixation_data', message)

                        self.logger.debug(f"Published fixation data: {message}")

                    else:
                        self.logger.debug(f"Invalid fixation data: {fix_x}, {fix_y}")

        except Exception as e:
            self.logger.error(f"Error publishing fixation data: {e}")

    def _close_pupil_connections(self):
        """Close ZMQ connections to Pupil"""
        if self.pupil_remote:
            self.pupil_remote.close()
        if self.pupil_subscriber:
            self.pupil_subscriber.close()
        self.pupil_context.term()


async def main(enable_logging):
    try:
        setup_logging(enable_logging)
        message_broker = MessageBroker(1024)
        parser = PupilMessageParser(message_broker=message_broker)
        await parser.start()

    except Exception as e:
        logging.error(f"Error starting message parser: {e}")


    finally:
        await parser.stop()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true',
                        help='Enable logging')
    args = parser.parse_args()

    asyncio.run(main(args.enable_logging))