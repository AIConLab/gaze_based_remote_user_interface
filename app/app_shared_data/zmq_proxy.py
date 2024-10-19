import zmq

def main():
    context = zmq.Context()

    frontend = context.socket(zmq.XSUB)
    frontend.bind("tcp://*:5559")

    backend = context.socket(zmq.XPUB)
    backend.bind("tcp://*:5560")

    zmq.proxy(frontend, backend)

if __name__ == "__main__":
    main()