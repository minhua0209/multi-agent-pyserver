from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname


def local_file_uri_to_path(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file" or parsed.netloc:
        return None
    return Path(url2pathname(parsed.path))
