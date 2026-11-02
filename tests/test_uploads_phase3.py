import tempfile
import unittest
from pathlib import Path

from limbo import UploadFile
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
        )
        with self.assertRaisesRegex(ValueError, "field count"):
            parse_multipart("multipart/form-data; boundary=BOUND", body, max_fields=1)
        with self.assertRaisesRegex(ValueError, "file count"):
            parse_multipart("multipart/form-data; boundary=BOUND", body, max_files=0)
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

    def test_safe_filename_and_chunk_size_validation(self):
        upload = UploadFile("../bad\\name?.txt", "text/plain", b"ok")
        with tempfile.TemporaryDirectory() as directory:
            saved = upload.save(directory)
            self.assertEqual(saved.name, "name_.txt")
            with self.assertRaises(ValueError):
                list(upload.iter_chunks(0))
            with self.assertRaises(ValueError):
                upload.save(directory, "other.txt", chunk_size=0)


if __name__ == "__main__":
    unittest.main()
