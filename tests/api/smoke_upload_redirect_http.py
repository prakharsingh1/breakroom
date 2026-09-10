"""Local socket regression: urllib must not forward bearer keys on redirects."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading

from breakroom.agents import corrected
from breakroom.evaluator import run_in_process
from breakroom.scenarios import load_case
from breakroom.uploads import prepare_upload, save_prepared_upload, upload_prepared_file


def main():
    observed = {"source_requests": 0, "destination_requests": 0}

    class Destination(BaseHTTPRequestHandler):
        def do_POST(self):
            observed["destination_requests"] += 1
            self.send_response(500)
            self.end_headers()
        do_GET = do_POST
        def log_message(self, *args):
            pass

    destination = ThreadingHTTPServer(("127.0.0.1", 0), Destination)

    class Redirect(BaseHTTPRequestHandler):
        def do_POST(self):
            observed["source_requests"] += 1
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/credential-trap")
            self.send_header("Content-Length", "0")
            self.end_headers()
        def log_message(self, *args):
            pass

    source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (source, destination)]
    for thread in threads:
        thread.start()
    token = "brk_" + secrets.token_urlsafe(32)
    os.environ["BREAKROOM_REDIRECT_SMOKE_KEY"] = token
    try:
        with tempfile.TemporaryDirectory() as folder:
            report = run_in_process(load_case("normal-refund"), corrected)
            assert report["verdict"] == "PASS"
            path = save_prepared_upload(prepare_upload(report, redaction_key=secrets.token_bytes(32)), Path(folder) / "reviewed.json")
            try:
                upload_prepared_file(path, server=f"http://127.0.0.1:{source.server_port}", project_id="test_project",
                                     token_env="BREAKROOM_REDIRECT_SMOKE_KEY", allow_localhost_http=True)
            except ValueError as error:
                assert "302" in str(error) and token not in str(error)
            else:
                raise AssertionError("Redirect must fail the upload")
        assert observed == {"source_requests": 1, "destination_requests": 0}, observed
        print(json.dumps({**observed, "redirect_rejected": True, "credentials_not_forwarded": True}))
    finally:
        os.environ.pop("BREAKROOM_REDIRECT_SMOKE_KEY", None)
        for server in (source, destination):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


if __name__ == "__main__":
    main()
