import pytest

from paperpipe import fetch


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code
        self.text = content.decode("utf-8", "replace")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_sha256_and_name(tmp_path):
    target = tmp_path / "f.bin"
    target.write_bytes(b"abc")
    assert fetch.sha256_file(target) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert fetch.pdf_name("math/0301234") == "math_0301234.pdf"


def test_download_rejects_html_under_http_200(tmp_path):
    session = FakeSession([FakeResponse(b"<html>nope</html>")])
    with pytest.raises(fetch.FetchError) as exc:
        fetch.download_pdf("2401.1", tmp_path, session=session, delay=0, attempts=1)
    assert "did not return a PDF" in str(exc.value)
    assert not list(tmp_path.glob("*.pdf"))


def test_download_writes_pdf_atomically(tmp_path):
    session = FakeSession([FakeResponse(b"%PDF-1.7\nbody")])
    info = fetch.download_pdf("2401.1", tmp_path, session=session, delay=0)
    assert info["bytes"] == 13 and info["skipped"] is False
    assert (tmp_path / "2401.1.pdf").read_bytes().startswith(b"%PDF-")
    assert not list(tmp_path.glob("*.part"))


def test_download_skips_existing_file(tmp_path):
    (tmp_path / "2401.1.pdf").write_bytes(b"%PDF-1.7\nexisting")
    session = FakeSession([])
    info = fetch.download_pdf("2401.1", tmp_path, session=session, delay=0)
    assert info["skipped"] is True and session.calls == []
