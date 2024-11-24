import os
import signal
import sys
import threading
from aiohttp import ClientSession
import asyncio
import select
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Queue
from threading import Thread
import argparse
from websocket import EufySecurityWebSocket
import json

# Variables
camera_handlers = {}
run_event = threading.Event()

# Constants
RECV_CHUNK_SIZE = 4096

EVENT_CONFIGURATION: dict = {
    "livestream video data": {
        "name": "video_data",
        "value": "buffer",
        "type": "event",
    },
    "livestream audio data": {
        "name": "audio_data",
        "value": "buffer",
        "type": "event",
    },
}

START_P2P_LIVESTREAM_MESSAGE = {
    "messageId": "start_livestream",
    "command": "device.start_livestream",
    "serialNumber": None,
}

STOP_P2P_LIVESTREAM_MESSAGE = {
    "messageId": "stop_livestream",
    "command": "device.stop_livestream",
    "serialNumber": None,
}

START_TALKBACK = {
    "messageId": "start_talkback",
    "command": "device.start_talkback",
    "serialNumber": None,
}

SEND_TALKBACK_AUDIO_DATA = {
    "messageId": "talkback_audio_data",
    "command": "device.talkback_audio_data",
    "serialNumber": None,
    "buffer": None,
}

STOP_TALKBACK = {
    "messageId": "stop_talkback",
    "command": "device.stop_talkback",
    "serialNumber": None,
}

SET_API_SCHEMA = {
    "messageId": "set_api_schema",
    "command": "set_api_schema",
    "schemaVersion": 13,
}


P2P_LIVESTREAMING_STATUS = "p2pLiveStreamingStatus"

START_LISTENING_MESSAGE = {"messageId": "start_listening", "command": "start_listening"}

TALKBACK_RESULT_MESSAGE = {
    "messageId": "talkback_audio_data",
    "errorCode": "device_talkback_not_running",
}

DRIVER_CONNECT_MESSAGE = {"messageId": "driver_connect", "command": "driver.connect"}


def exit_handler(signum, frame):
    print(f"Signal handler called with signal {signum}")
    run_event.set()


# Install signal handler
signal.signal(signal.SIGINT, exit_handler)


def exit_handler(signum, frame):
    print(f"Signal handler called with signal {signum}")
    run_event.set()


# Install signal handler
signal.signal(signal.SIGINT, exit_handler)


class ClientAcceptThread(threading.Thread):
    def __init__(self, socket, run_event, name, ws, serialno):
        threading.Thread.__init__(self)
        self.socket = socket
        self.queues = []
        self.run_event = run_event
        self.name = name
        self.ws = ws
        self.serialno = serialno
        self.my_threads = []

    def update_threads(self):
        my_threads_before = len(self.my_threads)
        for thread in self.my_threads:
            if not thread.is_alive():
                self.queues.remove(thread.queue)
        self.my_threads = [t for t in self.my_threads if t.is_alive()]
        if self.ws and my_threads_before > 0 and len(self.my_threads) == 0:
            if self.name == "BackChannel":
                print("All clients died (BackChannel): ", self.name)
                sys.stdout.flush()
            else:
                print("All clients died. Stopping Stream: ", self.name)
                sys.stdout.flush()

                msg = STOP_P2P_LIVESTREAM_MESSAGE.copy()
                msg["serialNumber"] = self.serialno
                asyncio.run(self.ws.send_message(json.dumps(msg)))

    def run(self):
        print("Accepting connection for ", self.name)
        msg = STOP_TALKBACK.copy()
        msg["serialNumber"] = self.serialno
        asyncio.run(self.ws.send_message(json.dumps(msg)))
        while not self.run_event.is_set():
            self.update_threads()
            sys.stdout.flush()
            try:
                client_sock, client_addr = self.socket.accept()
                print("New connection added: ", client_addr, " for ", self.name)
                sys.stdout.flush()

                if self.name == "BackChannel":
                    client_sock.setblocking(True)
                    print("Starting BackChannel")
                    thread = ClientRecvThread(
                        client_sock, run_event, self.name, self.ws, self.serialno
                    )
                    thread.start()
                else:
                    client_sock.setblocking(False)
                    thread = ClientSendThread(
                        client_sock, run_event, self.name, self.ws, self.serialno
                    )
                    self.queues.append(thread.queue)
                    if self.ws:
                        msg = START_P2P_LIVESTREAM_MESSAGE.copy()
                        msg["serialNumber"] = self.serialno
                        asyncio.run(self.ws.send_message(json.dumps(msg)))
                    self.my_threads.append(thread)
                    thread.start()
            except socket.timeout:
                pass


class ClientSendThread(threading.Thread):
    def __init__(self, client_sock, run_event, name, ws, serialno):
        threading.Thread.__init__(self)
        self.client_sock = client_sock
        self.queue = Queue(100)
        self.run_event = run_event
        self.name = name
        self.ws = ws
        self.serialno = serialno

    def run(self):
        print("Thread running: ", self.name)
        sys.stdout.flush()

        try:
            while not self.run_event.is_set():
                ready_to_read, ready_to_write, in_error = select.select(
                    [], [self.client_sock], [self.client_sock], 2
                )
                if len(in_error):
                    print("Exception in socket", self.name)
                    sys.stdout.flush()
                    break
                if not len(ready_to_write):
                    print("Socket not ready to write ", self.name)
                    sys.stdout.flush()
                    break
                if not self.queue.empty():
                    self.client_sock.sendall(bytearray(self.queue.get(True)["data"]))
        except socket.error as e:
            print("Connection lost", self.name, e)
            pass
        except socket.timeout:
            print("Timeout on socket for ", self.name)
            pass
        try:
            self.client_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            print("Error shutdown socket: ", self.name)
        self.client_sock.close()
        print("Thread stopping: ", self.name)
        sys.stdout.flush()


class ClientRecvThread(threading.Thread):
    def __init__(self, client_sock, run_event, name, ws, serialno):
        threading.Thread.__init__(self)
        self.client_sock = client_sock
        self.run_event = run_event
        self.name = name
        self.ws = ws
        self.serialno = serialno

    def run(self):
        msg = START_TALKBACK.copy()
        msg["serialNumber"] = self.serialno
        asyncio.run(self.ws.send_message(json.dumps(msg)))
        try:
            curr_packet = bytearray()
            no_data = 0
            while not self.run_event.is_set():
                try:
                    ready_to_read, ready_to_write, in_error = select.select(
                        [
                            self.client_sock,
                        ],
                        [],
                        [self.client_sock],
                        2,
                    )
                    if len(in_error):
                        print("Exception in socket", self.name)
                        sys.stdout.flush()
                        break
                    if len(ready_to_read):
                        data = self.client_sock.recv(RECV_CHUNK_SIZE)
                        curr_packet += bytearray(data)
                        if len(data) > 0:  # and len(data) <= RECV_CHUNK_SIZE:
                            msg = SEND_TALKBACK_AUDIO_DATA.copy()
                            msg["serialNumber"] = self.serialno
                            msg["buffer"] = list(bytes(curr_packet))
                            asyncio.run(self.ws.send_message(json.dumps(msg)))
                            curr_packet = bytearray()
                            no_data = 0
                        else:
                            no_data += 1
                    else:
                        no_data += 1
                    if no_data >= 15:
                        print("15x in a row no data in socket ", self.name)
                        sys.stdout.flush()
                        break
                except BlockingIOError:
                    # Resource temporarily unavailable (errno EWOULDBLOCK)
                    pass
        except socket.error as e:
            print("Connection lost", self.name, e)
            pass
        except socket.timeout:
            print("Timeout on socket for ", self.name)
            pass
        except select.error:
            print("Select error on socket ", self.name)
            pass
        sys.stdout.flush()
        try:
            self.client_sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            print("Error shutdown socket: ", self.name)
        sys.stdout.flush()
        self.client_sock.close()
        msg = STOP_TALKBACK.copy()
        msg["serialNumber"] = self.serialno
        asyncio.run(self.ws.send_message(json.dumps(msg)))


# Camera Stream Handler
class CameraStreamHandler:
    def __init__(self, serial_number, start_port, run_event):
        print(
            f" - CameraStreamHandler - __init__ - serial_number: {serial_number} - video_port: {start_port} - audio_port: {start_port + 1} - backchannel_port: {start_port + 2}"
        )
        self.serial_number = serial_number
        self.start_port = start_port
        self.run_event = run_event
        self.ws = None
        self.video_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.audio_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.backchannel_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.start_livestream_msg = {
            "messageId": "start_livestream",
            "command": "device.start_livestream",
            "serialNumber": self.serial_number,
        }
        self.stop_livestream_msg = {
            "messageId": "stop_livestream",
            "command": "device.stop_livestream",
            "serialNumber": self.serial_number,
        }

    def setup_sockets(self):
        self.video_sock.bind(("0.0.0.0", self.start_port))
        self.video_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.video_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.audio_sock.bind(("0.0.0.0", self.start_port + 1))
        self.audio_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.audio_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.backchannel_sock.bind(("0.0.0.0", self.start_port + 2))
        self.backchannel_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.backchannel_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        self.backchannel_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.video_sock.listen(1)
        self.audio_sock.listen(1)
        self.backchannel_sock.listen(1)

    def start_stream(self):
        self.video_thread = ClientAcceptThread(
            self.video_sock, self.run_event, "Video", self.ws, self.serial_number
        )
        self.audio_thread = ClientAcceptThread(
            self.audio_sock, self.run_event, "Audio", self.ws, self.serial_number
        )
        self.backchannel_thread = ClientAcceptThread(
            self.backchannel_sock,
            self.run_event,
            "BackChannel",
            self.ws,
            self.serial_number,
        )
        self.audio_thread.start()
        self.video_thread.start()
        self.backchannel_thread.start()

    def setWs(self, ws: EufySecurityWebSocket):
        self.ws = ws

        # asyncio.run(self._start_stream_async(websocket))

    async def _start_stream_async(self, websocket):
        await websocket.send(json.dumps(self.start_livestream_msg))
        video_conn, _ = self.video_sock.accept()
        audio_conn, _ = self.audio_sock.accept()
        backchannel_conn, _ = self.backchannel_sock.accept()

        while True:
            video_data = await websocket.recv()
            audio_data = await websocket.recv()
            video_conn.sendall(video_data)
            audio_conn.sendall(audio_data)
            backchannel_conn.sendall(
                b""
            )  # Send empty data to keep the backchannel alive

    def stop_stream(self, websocket):
        asyncio.run(self._stop_stream_async(websocket))

    async def _stop_stream_async(self, websocket):
        await websocket.send(json.dumps(self.stop_livestream_msg))
        self.video_sock.close()
        self.audio_sock.close()
        self.backchannel_sock.close()


# On Open Callback
async def on_open():
    print(f" on_open - executed")


# On Close Callback
async def on_close():
    print(f" on_close - executed")


#    self.run_event.set()
#    self.ws = None
#    stop()
#    os._exit(-1)


# On Error Callback
async def on_error(message):
    print(f" on_error - executed - {message}")


# On Message Callback
async def on_message(message):
    payload = message.json()
    message_type: str = payload["type"]
    if message_type == "result":
        message_id = payload["messageId"]
        if message_id != SEND_TALKBACK_AUDIO_DATA["messageId"]:
            # Avoid spamming of TALKBACK_AUDIO_DATA logs
            print(f"on_message result: {payload}")
            sys.stdout.flush()
        if message_id == START_LISTENING_MESSAGE["messageId"]:
            print(f"Listening started: {payload}")
            message_result = payload[message_type]
            states = message_result["state"]
            for state in states["devices"]:
                serialno = state["serialNumber"]
                if serialno in camera_handlers:
                    camera_handlers[serialno].start_stream()
                else:
                    print(f"Found unknown Eufy camera with serial number {serialno}.")

            # self.video_thread = ClientAcceptThread(video_sock, run_event, "Video", self.ws, self.serialno)
            # self.audio_thread = ClientAcceptThread(audio_sock, run_event, "Audio", self.ws, self.serialno)
            # self.backchannel_thread = ClientAcceptThread(backchannel_sock, run_event, "BackChannel", self.ws, self.serialno)
            # self.audio_thread.start()
            # self.video_thread.start()
            # self.backchannel_thread.start()
        if (
            message_id == TALKBACK_RESULT_MESSAGE["messageId"]
            and "errorCode" in payload
        ):
            error_code = payload["errorCode"]
            print(f"Talkback error: {error_code}")
            # if error_code == "device_talkback_not_running":
                # msg = START_TALKBACK.copy()
                # msg["serialNumber"] = self.serialno
                # await self.ws.send_message(json.dumps(msg))

    if message_type == "event":
        message = payload[message_type]
        event_type = message["event"]
        sys.stdout.flush()
        if message["event"] == "livestream audio data":
            # print(f"on_audio - {payload}")
            event_value = message[EVENT_CONFIGURATION[event_type]["value"]]
            event_data_type = EVENT_CONFIGURATION[event_type]["type"]
            if event_data_type == "event":
                print(f"##################################################################")
                print(f"on_audio - {payload['source']['serialNumber']}")
                serialno = payload["source"]["serialNumber"]
                if serialno in camera_handlers:
                    for queue in camera_handlers[serialno].audio_thread.queues:
                        if queue.full():
                            print("Audio queue full.")
                            queue.get(False)
                        queue.put(event_value)
            #     for queue in self.audio_thread.queues:
            #         if queue.full():
            #             print("Audio queue full.")
            #             queue.get(False)
            #         queue.put(event_value)
        if message["event"] == "livestream video data":
            event_value = message[EVENT_CONFIGURATION[event_type]["value"]]
            event_data_type = EVENT_CONFIGURATION[event_type]["type"]
            if event_data_type == "event":
                print(f"##################################################################")
                print(f"on_video - {payload['source']['serialNumber']}")
                serialno = payload["source"]["serialNumber"]
                if serialno in camera_handlers:
                    for queue in camera_handlers[serialno].video_thread.queues:
                        if queue.full():
                            print("Video queue full.")
                            queue.get(False)
                        queue.put(event_value)
                # for queue in self.video_thread.queues:
            #         if queue.full():
            #             print("Video queue full.")
            #             queue.get(False)
            #         queue.put(event_value)
        if message["event"] == "livestream error":
            print(f"##################################################################")
            print(f"Livestream Error! - {payload}")
            # if self.ws and len(self.video_thread.queues) > 0:
            #     msg = START_P2P_LIVESTREAM_MESSAGE.copy()
            #     msg["serialNumber"] = self.serialno
            #     await self.ws.send_message(json.dumps(msg))


async def init_websocket(ws_security_port):
    websocket = EufySecurityWebSocket(
        "402f1039-eufy-security-ws",
        ws_security_port,
        ClientSession(),
        on_open,
        on_message,
        on_close,
        on_error,
    )
    # Set the websocket for all camera handlers.
    for handler in camera_handlers.values():
        handler.setWs(websocket)

    try:
        await websocket.connect()
        await websocket.send_message(json.dumps(START_LISTENING_MESSAGE))
        await websocket.send_message(json.dumps(SET_API_SCHEMA))
        await websocket.send_message(json.dumps(DRIVER_CONNECT_MESSAGE))
        while not run_event.is_set():
            await asyncio.sleep(1000)
    except Exception as ex:
        print(ex)
        print("init_websocket failed. Exiting.")
    os._exit(-1)


if __name__ == "__main__":
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Stream video and audio from multiple Eufy cameras."
    )
    parser.add_argument(
        "--camera_serials",
        nargs="+",
        required=True,
        help="List of camera serial numbers (e.g., --serials CAM1_SERIAL CAM2_SERIAL).",
    )
    parser.add_argument(
        "--ws_security_port",
        type=int,
        default=3000,
        help="Base port number for streaming (default: 3000).",
    )
    args = parser.parse_args()
    print(f"WS Security Port: {args.ws_security_port}")
    print(f"Camera Serial Numbers: {args.camera_serials}")

    # Define constants.
    BASE_PORT = 63336
    # Create one Camera Stream Handler per camera.
    for i, serial in enumerate(args.camera_serials):
        if serial != "null":
            handler = CameraStreamHandler(serial, BASE_PORT + i * 3, run_event)
            handler.setup_sockets()
            camera_handlers[serial] = handler

    # Loop forever.
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_websocket(args.ws_security_port))
