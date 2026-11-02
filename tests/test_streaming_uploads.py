import io
import socket
import tempfile
import unittest
from pathlib import Path

from limbo.server import LimboServer
from limbo.uploads import UploadFile, parse_multipart_stream


def multipart_body(payload: bytes) -> bytes:
    return (
        b"--BOUND\r\n"
        b'Content-Disposition: form-data; name="note"\r\n\r\nhello\r\n'
        b"--BOUND\r\n"
        b'Content-Disposition: form-data; name="asset"; filename="large.bin"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        + payload
        + b"\r\n--BOUND--\r\n"
    )


class CountingReader:
    def __init__(self, data: bytes):
        self.source = io.BytesIO(data)
        self.max_read = 0
        self.total_read = 0

    def read(self, size=-1):
        self.max_read = max(self.max_read, size)
        chunk = self.source.read(size)
        self.total_read += len(chunk)
        return chunk


class StreamingMultipartTests(unittest.TestCase):
    def test_large_file_is_spooled_incrementally_and_tempdir_is_cleanable(self):
        payload = b"0123456789abcdef" * 131072
        source = CountingReader(multipart_body(payload))
        parsed, tempdir = parse_multipart_stream(
            "multipart/form-data; boundary=BOUND",
            source,
            max_body_bytes=3 * 1024 * 1024,
            max_file_bytes=3 * 1024 * 1024,
            chunk_size=4096,
        )
        try:
            upload = parsed["asset"][0]
            self.assertIsInstance(upload, UploadFile)
            self.assertEqual(upload.content, b"")
            self.assertIsNotNone(upload.temporary_path)
            self.assertEqual(upload.size, len(payload))
            self.assertLessEqual(source.max_read, 4096)
            self.assertEqual(b"".join(upload.iter_chunks(8192)), payload)
            self.assertEqual(parsed["note"], ["hello"])
            path = upload.temporary_path
            self.assertTrue(path.exists())
        finally:
            tempdir.cleanup()
        self.assertFalse(path.exists())

    def test_socket_request_streams_multipart_without_populating_request_body(self):
        payload = b"upload-data-" * 10000
        body = multipart_body(payload)
        client, server_socket = socket.socketpair()
        try:
            client.sendall(
                b"POST /upload HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Type: multipart/form-data; boundary=BOUND\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
                + body
            )
            client.shutdown(socket.SHUT_WR)
            server = LimboServer(lambda request: None, max_body_bytes=2 * 1024 * 1024)
            stream = server_socket.makefile("rb")
            request = server._read_request(stream, ("127.0.0.1", 12345))
            try:
                self.assertEqual(request.body, b"")
                upload = request.files["asset"][0]
                self.assertEqual(upload.size, len(payload))
                self.assertEqual(b"".join(upload.iter_chunks()), payload)
                path = upload.temporary_path
                self.assertTrue(path.exists())
            finally:
                request.close_uploads()
                stream.close()
            self.assertFalse(path.exists())
        finally:
            client.close()
            server_socket.close()

    def test_chunked_multipart_body_is_streamed_and_decoded(self):
        body = multipart_body(b"chunked-payload" * 1000)
        chunks = [body[i:i + 97] for i in range(0, len(body), 97)]
        wire = b"".join(
            f"{len(chunk):X}\r\n".encode("ascii") + chunk + b"\r\n"
            for chunk in chunks
        ) + b"0\r\n\r\n"
        client, server_socket = socket.socketpair()
        try:
            client.sendall(
                b"POST /upload HTTP/1.1\r\nHost: localhost\r\n"
                b"Content-Type: multipart/form-data; boundary=BOUND\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n" + wire
            )
            client.shutdown(socket.SHUT_WR)
            server = LimboServer(lambda request: None, max_body_bytes=1024 * 1024)
            stream = server_socket.makefile("rb")
            request = server._read_request(stream, ("127.0.0.1", 12345))
            try:
                self.assertEqual(request.body, b"")
                self.assertEqual(request.files["asset"][0].size, len(b"chunked-payload" * 1000))
            finally:
                request.close_uploads()
                stream.close()
        finally:
            client.close()
            server_socket.close()


if __name__ == "__main__":
    unittest.main()
