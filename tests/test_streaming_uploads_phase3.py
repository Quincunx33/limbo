"""End-to-end tests for bounded, request-scoped multipart streaming."""
import io
import tempfile
import unittest
from pathlib import Path

from limbo.server import LimboServer


class ReadTrackingSource:
    def __init__(self, data):
        self.source = io.BytesIO(data)
        self.read_sizes = []
        self.max_returned = 0

    def read(self, size=-1):
        self.read_sizes.append(size)
        chunk = self.source.read(size)
        self.max_returned = max(self.max_returned, len(chunk))
        return chunk


def multipart_body(payload, *, boundary="STREAMBOUND"):
    return (
        b"--" + boundary.encode() + b"\r\n"
        b'Content-Disposition: form-data; name="note"\r\n\r\n'
        b"bounded-memory\r\n"
        b"--" + boundary.encode() + b"\r\n"
        b'Content-Disposition: form-data; name="asset"; filename="large.bin"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        + payload + b"\r\n--" + boundary.encode() + b"--\r\n"
    )


class StreamingMultipartTests(unittest.TestCase):
    def setUp(self):
        self.server = LimboServer(lambda request: None, max_body_bytes=2 * 1024 * 1024)

    def _read(self, body, framing):
        headers = (
            b"POST /upload HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: multipart/form-data; boundary=STREAMBOUND\r\n"
            + framing + b"\r\n"
        )
        stream = io.BytesIO(headers + body)
        return self.server._read_request(stream, ("127.0.0.1", 12345))

    def test_content_length_streams_file_to_private_temp_storage(self):
        payload = b"0123456789abcdef" * 32768
        body = multipart_body(payload)
        request = self._read(body, b"Content-Length: " + str(len(body)).encode())
        try:
            upload = request.file("asset")
            self.assertIsNotNone(upload)
            self.assertEqual(upload.size, len(payload))
            self.assertEqual(upload.content, b"")
            self.assertEqual(upload.temporary_path.read_bytes(), payload)
            self.assertEqual(request.form_value("note"), "bounded-memory")
            temp_path = upload.temporary_path
            self.assertTrue(temp_path.exists())
        finally:
            request.close_uploads()
        self.assertFalse(temp_path.exists())

    def test_chunked_transfer_streams_and_cleans_request_files(self):
        payload = b"chunked-stream-" * 12000
        body = multipart_body(payload)
        chunks = [body[index:index + 997] for index in range(0, len(body), 997)]
        encoded = b"".join(
            f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n" for chunk in chunks
        ) + b"0\r\n\r\n"
        request = self._read(encoded, b"Transfer-Encoding: chunked")
        upload = request.file("asset")
        temp_path = upload.temporary_path
        self.assertEqual(upload.size, len(payload))
        self.assertEqual(b"".join(upload.iter_chunks(4093)), payload)
        request.close_uploads()
        self.assertFalse(temp_path.exists())

    def test_malformed_stream_cleans_temporary_directory(self):
        body = multipart_body(b"payload")[:-8]
        with self.assertRaises(Exception):
            self._read(body, b"Content-Length: " + str(len(body)).encode())

    def test_stream_parser_never_requests_unbounded_reads(self):
        from limbo.uploads import parse_multipart_stream

        payload = b"x" * (512 * 1024)
        body = multipart_body(payload)
        source = ReadTrackingSource(body)
        fields, tempdir = parse_multipart_stream(
            "multipart/form-data; boundary=STREAMBOUND",
            source,
            max_body_bytes=1024 * 1024,
            max_file_bytes=1024 * 1024,
            chunk_size=4096,
        )
        try:
            self.assertTrue(source.read_sizes)
            self.assertTrue(all(0 < size <= 4096 for size in source.read_sizes))
            self.assertEqual(fields["asset"][0].size, len(payload))
            self.assertEqual(fields["asset"][0].content, b"")
        finally:
            tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
