import asyncio
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Queue
from threading import Thread
from websocket import EufySecurityWebSocket
import json

# Constants
RECV_CHUNK_SIZE = 4096

# Camera Stream Handler
class CameraStreamHandler:
    def __init__(self, serial_number, start_port):
        self.serial_number = serial_number
        self.start_port = start_port
        self.video_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.audio_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
        self.audio_sock.bind(("0.0.0.0", self.start_port + 1))
        self.video_sock.listen(1)
        self.audio_sock.listen(1)

    def start_stream(self, websocket):
        asyncio.run(self._start_stream_async(websocket))

    async def _start_stream_async(self, websocket):
        await websocket.send(json.dumps(self.start_livestream_msg))
        video_conn, _ = self.video_sock.accept()
        audio_conn, _ = self.audio_sock.accept()

        while True:
            video_data = await websocket.recv()
            audio_data = await websocket.recv()
            video_conn.sendall(video_data)
            audio_conn.sendall(audio_data)

    def stop_stream(self, websocket):
        asyncio.run(self._stop_stream_async(websocket))

    async def _stop_stream_async(self, websocket):
        await websocket.send(json.dumps(self.stop_livestream_msg))
        self.video_sock.close()
        self.audio_sock.close()


# Multi-Camera Manager
class MultiCameraManager:
    def __init__(self, cameras, base_port):
        self.cameras = cameras
        self.base_port = base_port
        self.stream_handlers = {}

    def initialize_streams(self):
        current_port = self.base_port
        for serial in self.cameras:
            handler = CameraStreamHandler(serial, current_port)
            handler.setup_sockets()
            self.stream_handlers[serial] = handler
            current_port += 2  # Allocate two ports per camera

    def start_all_streams(self, websocket):
        for handler in self.stream_handlers.values():
            Thread(target=handler.start_stream, args=(websocket,)).start()

    def stop_all_streams(self, websocket):
        for handler in self.stream_handlers.values():
            handler.stop_stream(websocket)


if __name__ == "__main__":
    # Example usage
    CAMERA_SERIALS = ["CAM1_SERIAL", "CAM2_SERIAL", "CAM3_SERIAL"]
    BASE_PORT = 8000

    manager = MultiCameraManager(CAMERA_SERIALS, BASE_PORT)
    manager.initialize_streams()

    # Assuming the WebSocket connection is established here
    websocket = EufySecurityWebSocket()  # Replace with actual WebSocket connection setup

    try:
        manager.start_all_streams(websocket)
    except KeyboardInterrupt:
        manager.stop_all_streams(websocket)
