import zmq
import logging
import os
from datetime import datetime
import argparse


def setup_logging(enable_logging):
    if enable_logging:
        log_dir = "logs"
        os.makedirs(log_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = os.path.join(log_dir, f"app_log_{timestamp}.log")

        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
    else:
        logging.basicConfig(level=logging.ERROR)



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--enable-logging', action='store_true', help='Enable logging')
    args = parser.parse_args()

    setup_logging(args.enable_logging)

    logging.info("Starting ZeroMQ proxy")

    context = zmq.Context()

    frontend = context.socket(zmq.XSUB)
    frontend.bind("tcp://*:5559")
    logging.info("Frontend bound to tcp://*:5559")

    backend = context.socket(zmq.XPUB)
    backend.bind("tcp://*:5560")
    logging.info("Backend bound to tcp://*:5560")

    zmq.proxy(frontend, backend)

    frontend.close()
    backend.close()
    context.term()
