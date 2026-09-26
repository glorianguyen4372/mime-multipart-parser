# MIME Multipart Parser

Parses RFC 2046 multipart MIME bodies into part dictionaries with headers and decoded content. Standard library only, zero dependencies.

## Usage

```python
from mime_multipart_parser import MultipartParser

parser = MultipartParser()
body = (
    b'--boundary\r\n'
    b'Content-Type: text/plain; charset=utf-8\r\n'
    b'\r\n'
    b'hello\r\n'
    b'--boundary--\r\n'
)
parts = parser.parse(body, 'boundary')
# parts[0]['headers']  -> {'content-type': 'text/plain; charset=utf-8'}
# parts[0]['body']    -> 'hello'
```

Exported names: `MultipartParser`, `ParseError`.

`MultipartParser.parse(body: bytes, boundary: str) -> list[dict]` where each dict has keys `headers` (dict[str, str], names lowercased) and `body` (str, decoded per charset or ASCII with surrogateescape).

## Why this exists

The standard library's `email` module can parse multipart messages, but it is heavy, pulls in a lot of machinery, and is oriented around parsing a complete message (headers plus body). When you already have the body bytes and the boundary parameter and just want the parts split out, there is no focused tool. This library is that tool.

The trade-off: the library does not decode transfer encodings (base64, quoted-printable). It returns the raw body bytes of each part decoded to text using the declared charset (or ASCII with surrogateescape if none). If you need transfer-encoding handling, decode the body yourself afterwards — `base64.b64decode` is one call.

## The awkward edge

The parser accepts bodies with no closing delimiter (`--boundary--`). RFC 2046 requires one, but real-world data — truncated captures, partial streams, incomplete writes — often lacks it. Rather than raise, the parser returns whatever parts it found, including a final part if there was content after the last opening delimiter. If this matters to you, check whether your source guarantees a closing delimiter and validate accordingly.

Header line folding (RFC 822 continuation lines starting with whitespace) is handled. Repeated headers with the same name are joined with `", "` rather than last-wins.
