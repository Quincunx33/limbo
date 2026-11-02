import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from limbo import Limbo, Request, UploadFile
from limbo.uploads import parse_multipart


class UploadLifecycleTests(unittest.TestCase):
    def multipart(self, *parts):
        body = bytearray()
        for headers, payload in parts:
            body.extend(b"--BOUND\r\n")
            body.extend(headers)
            body.extend(b"\r\n\r\n")
            body.extend(payload)
            body.extend(b"\r\n")
        body.extend(b"--BOUND--\r\n")
        return bytes(body)

    def test_file_and_field_limits_are_enforced(self):
        body = self.multipart(
            (b'Content-Disposition: form-data; name="a"', b"one"),
            (b'Content-Disposition: form-data; name="b"', b"two"),
            (b'Content-Disposition: form-data; name="f"; filename="x.txt"', b"1234"),
            (b'Content-Disposition: form-data; name="g"; filename="y.txt"', b"5678"),
        )
        with self.assertRaisesRegex(ValueError, "field count"):
            parse_multipart("multipart/form-data; boundary=BOUND", body, max_fields=1)
        with self.assertRaisesRegex(ValueError, "file count"):
            parse_multipart("multipart/form-data; boundary=BOUND", body, max_files=1)
        with self.assertRaisesRegex(ValueError, "file size"):
            parse_multipart("multipart/form-data; boundary=BOUND", body, max_file_bytes=3)

    def test_save_uses_chunks_and_enforces_limit_without_overwrite(self):
        upload = UploadFile("report.bin", "application/octet-stream", b"abcdefghij")
        self.assertEqual(list(upload.iter_chunks(3)), [b"abc", b"def", b"ghi", b"j"])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                upload.save(directory, max_bytes=9)
            saved = upload.save(directory, chunk_size=2)
            self.assertEqual(saved.read_bytes(), b"abcdefghij")
            with self.assertRaises(FileExistsError):
                upload.save(directory)

    def test_failed_save_removes_partially_written_destination(self):
        upload = UploadFile("interrupted.bin", "application/octet-stream", b"abcdefgh")

        def fail_after_first_chunk(self, chunk_size):
            yield b"abc"
            raise OSError("simulated disk write failure")

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(UploadFile, "iter_chunks", fail_after_first_chunk):
                with self.assertRaisesRegex(OSError, "simulated disk write failure"):
                    upload.save(directory, chunk_size=3)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_safe_filename_and_chunk_size_validation(self):
        upload = UploadFile("../bad\\name?.txt", "text/plain", b"ok")
        with tempfile.TemporaryDirectory() as directory:
            saved = upload.save(directory)
            self.assertEqual(saved.name, "name_.txt")
            with self.assertRaises(ValueError):
                list(upload.iter_chunks(0))
            with self.assertRaises(ValueError):
                upload.save(directory, "other.txt", chunk_size=0)


class ConfiguredUploadLimitTests(unittest.TestCase):
    def test_application_upload_file_and_body_limits_map_to_413(self):
        body = (b'--BOUND\r\nContent-Disposition: form-data; name="file"; filename="x.bin"\r\n'
                b'Content-Type: application/octet-stream\r\n\r\n1234\r\n--BOUND--\r\n')
        app = Limbo(max_body_bytes=1024, max_upload_file_bytes=3)
        @app.post("/upload")
        def upload(request):
            return {"size": request.files["file"][0].size}
        response = app.dispatch(Request("POST", "/upload", {
            "content-type": "multipart/form-data; boundary=BOUND"
        }, body))
        self.assertEqual(response.status, 413)

        small = Limbo(max_body_bytes=2)
        @small.post("/upload")
        def upload_small(request):
            return "never"
        self.assertEqual(small.dispatch(Request("POST", "/upload", {}, b"123")).status, 413)

    def test_application_enforces_file_count_and_malformed_multipart_status(self):
        body = (b'--BOUND\r\nContent-Disposition: form-data; name="a"; filename="a.txt"\r\n\r\na\r\n'
                b'--BOUND\r\nContent-Disposition: form-data; name="b"; filename="b.txt"\r\n\r\nb\r\n'
                b'--BOUND--\r\n')
        app = Limbo(max_upload_files=1)
        @app.post("/upload")
        def upload(request):
            return {"count": len(request.files)}
        self.assertEqual(app.dispatch(Request("POST", "/upload", {
            "content-type": "multipart/form-data; boundary=BOUND"
        }, body)).status, 413)

        malformed = app.dispatch(Request("POST", "/upload", {
            "content-type": "multipart/form-data; boundary=BOUND"
        }, b"not multipart"))
        self.assertEqual(malformed.status, 400)


if __name__ == "__main__":
    unittest.main()
