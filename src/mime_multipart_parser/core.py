"""RFC 2046 multipart MIME body parser.

Scope and interpretation
------------------------

This library parses the *body* of a multipart MIME entity — that is, the bytes
that come after the blank line separating the entity's headers from its
content. It does not parse the top-level MIME headers to discover the
boundary parameter; the caller supplies the boundary explicitly. This keeps
the parser focused on body structure and lets the caller obtain the boundary
by whatever means is appropriate (e.g. `email.message.Message.get_param`),
including from an outer protocol that does not use RFC 2046 framing.

Encoding: bodies are returned as strings. If a part declares a charset in its
Content-Type, that charset is used to decode the bytes. Otherwise the bytes
are decoded as ASCII with surrogateescape, which round-trips arbitrary byte
values losslessly and matches the behaviour of Python's `email` package for
the default case. No transfer-encoding decoding (base64, quoted-printable) is
performed; the raw body bytes of each part are decoded per the charset only.
This is a deliberate trade-off: the library's job is structure, not full MIME
semantics.

The awkward edge
----------------

RFC 2046 says a boundary in the body is a line that begins with "--" followed
by the boundary string. Two subtleties bite here:

1. A boundary line may have trailing whitespace (RFC 2046 §5.1.1: "Linear
   whitespace ... may be removed"). We strip trailing whitespace before
   comparing.

2. The delimiter that ends the whole multipart body is "--<boundary>--". We
   detect this and stop parsing; anything after the closing delimiter is
   ignored. If no closing delimiter is present, the last part is still
   returned (tolerant of truncated bodies, as commonly seen in streaming or
   truncated captures).

This is the one place the library deviates from strict RFC behaviour: a strict
parser would reject a body without a closing delimiter. We accept it, because
real-world data is often truncated.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


__all__ = ["MultipartParser", "ParseError"]


class ParseError(Exception):
    """Raised when the multipart body is structurally invalid.

    Currently raised only when the boundary parameter is empty, since an empty
    boundary would match every line and produce nonsense.
    """


class MultipartParser:
    """Parses RFC 2046 multipart bodies into part dictionaries.

    Each part is returned as a dict with keys:
        headers : dict[str, str]  Header name (lower-cased) to value.
        body   : str             Decoded body text.

    Example
    -------
    >>> body = (
    ...     b'--boundary\r\n'
    ...     b'Content-Type: text/plain; charset=utf-8\r\n'
    ...     b'\r\n'
    ...     b'hello\r\n'
    ...     b'--boundary--\r\n'
    ... )
    >>> parts = MultipartParser().parse(body, 'boundary')
    >>> len(parts)
    1
    >>> parts[0]['headers']['content-type']
    'text/plain; charset=utf-8'
    >>> parts[0]['body']
    'hello'
    """

    def parse(self, body: bytes, boundary: str) -> List[Dict[str, str]]:
        """Parse a multipart body, returning a list of part dictionaries.

        Parameters
        ----------
        body :
            The raw bytes of the multipart body (everything after the blank
            line that separates the entity's headers from its content).
        boundary :
            The boundary string *without* the leading "--". Extract this from
            the Content-Type ``boundary`` parameter yourself.

        Raises
        ------
        ParseError
            If ``boundary`` is empty.
        """
        if not boundary:
            raise ParseError("boundary must be a non-empty string")

        # The delimiter as it appears on the wire: "--" + boundary.
        # We compare the stripped line against this, and separately detect the
        # closing form "--" + boundary + "--".
        delimiter = b'--' + boundary.encode('ascii')
        closing = delimiter + b'--'

        # Split the body on CRLF boundaries while preserving the separator so
        # we can reconstruct header lines and detect empty lines. We operate on
        # the raw bytes rather than decoding first, because headers are ASCII
        # but bodies may be any encoding.
        #
        # We use splitlines(keepends=True) which handles CRLF, LF, and CR
        # line endings uniformly. This matters: some producers emit bare LF.
        lines = body.splitlines(keepends=True)

        parts: List[Dict[str, str]] = []
        i = 0
        n = len(lines)

        # Skip the optional preamble: everything before the first delimiter.
        # RFC 2046 permits leading data that is not part of any part and should
        # be ignored by MIME-compliant parsers.
        while i < n:
            stripped = lines[i].rstrip(b'\r\n')
            # Trailing whitespace after the delimiter is permitted and removed
            # per RFC 2046 §5.1.1, so we also strip spaces/tabs on the right.
            if stripped.rstrip() == delimiter or stripped.rstrip() == closing:
                break
            i += 1

        # If we never found a delimiter, there are no parts. This is arguably
        # malformed, but returning an empty list is the least surprising
        # behaviour for a caller inspecting the result.
        if i >= n:
            return parts

        # Check whether the very first delimiter is actually the closing one
        # (empty multipart body).
        first_stripped = lines[i].rstrip(b'\r\n').rstrip()
        if first_stripped == closing:
            return parts

        # Move past the opening delimiter.
        i += 1

        current_headers: List[Tuple[str, str]] = []
        current_body_lines: List[bytes] = []
        in_headers = True

        while i < n:
            raw = lines[i]
            stripped = raw.rstrip(b'\r\n')
            rstripped = stripped.rstrip()

            # Detect a delimiter line. We compare the right-stripped version
            # so that "--boundary   \r\n" matches "--boundary".
            if rstripped == delimiter:
                # End of current part.
                parts.append(self._finalize(current_headers, current_body_lines))
                current_headers = []
                current_body_lines = []
                in_headers = True
                i += 1
                continue

            if rstripped == closing:
                # Closing delimiter: finalize the current part and stop.
                # Anything after the closing delimiter is the epilogue and is
                # ignored.
                parts.append(self._finalize(current_headers, current_body_lines))
                break

            # Not a delimiter: this line belongs to the current part.
            if in_headers:
                if stripped == b'':
                    # Blank line separates headers from body.
                    in_headers = False
                else:
                    current_headers.append(self._parse_header_line(raw, current_headers))
            else:
                current_body_lines.append(raw)

            i += 1
        else:
            # We ran off the end of the body without seeing a closing delimiter.
            # Finalize the last part if it has content. This tolerates
            # truncated bodies, which appear in real-world data.
            if current_headers or current_body_lines:
                parts.append(self._finalize(current_headers, current_body_lines))

        return parts

    @staticmethod
    def _parse_header_line(
        raw: bytes, existing: List[Tuple[str, str]]
    ) -> Tuple[str, str]:
        """Parse a single header line, handling RFC 822 line folding.

        If the line begins with whitespace, it is a continuation of the
        previous header's value ("folding"). We append it to the last header.
        """
        # Decode header bytes as ASCII with surrogateescape. Headers are
        # required to be ASCII; surrogateescape lets us handle non-ASCII bytes
        # without raising, matching the email package's tolerance.
        text = raw.decode('ascii', errors='surrogateescape').rstrip('\r\n')

        if text[:1] in (' ', '\t') and existing:
            # Folded continuation: append to the previous header value.
            name, value = existing.pop()
            # Normalize the folding whitespace to a single space, per RFC 822.
            value = value.rstrip() + ' ' + text.strip()
            return (name, value)

        colon = text.find(':')
        if colon == -1:
            # Malformed header line; treat the whole thing as a header with no
            # value rather than crashing. This is more forgiving than strict
            # MIME but avoids data loss.
            name = text.strip().lower()
            return (name, '')

        name = text[:colon].strip().lower()
        value = text[colon + 1:].strip()
        return (name, value)

    @staticmethod
    def _finalize(
        headers: List[Tuple[str, str]], body_lines: List[bytes]
    ) -> Dict[str, str]:
        """Build a part dictionary from accumulated headers and body lines."""
        header_dict: Dict[str, str] = {}
        for name, value in headers:
            # If a header name repeats, join with ", " as RFC 822 suggests for
            # general headers. This preserves all values rather than
            # last-wins, which would silently drop information.
            if name in header_dict:
                header_dict[name] = header_dict[name] + ', ' + value
            else:
                header_dict[name] = value

        raw_body = b''.join(body_lines)
        # Strip the trailing line ending that preceded the delimiter line.
        # Only the single separator before the delimiter belongs to framing,
        # not to body content.
        if raw_body.endswith(b'\r\n'):
            raw_body = raw_body[:-2]
        elif raw_body.endswith(b'\n'):
            raw_body = raw_body[:-1]
        charset = MultipartParser._extract_charset(header_dict.get('content-type', ''))
        try:
            body_text = raw_body.decode(charset) if charset else raw_body.decode(
                'ascii', errors='surrogateescape'
            )
        except (LookupError, UnicodeDecodeError):
            # Unknown charset or undecodable bytes: fall back to
            # surrogateescape so we never lose data.
            body_text = raw_body.decode('ascii', errors='surrogateescape')

        return {"headers": header_dict, "body": body_text}

    @staticmethod
    def _extract_charset(content_type: str) -> Optional[str]:
        """Extract the charset parameter from a Content-Type header value.

        Returns None if no charset is declared. We do a lightweight parse
        rather than using email.message because we want to avoid pulling in
        the full email machinery for such a small need.
        """
        # Find "charset=" case-insensitively. We search in the lowercased
        # string but slice from the original to preserve the value's case.
        lower = content_type.lower()
        idx = lower.find('charset=')
        if idx == -1:
            return None
        # Move past "charset=".
        start = idx + len('charset=')
        remainder = content_type[start:]
        # The value may be quoted.
        if remainder.startswith('"'):
            end = remainder.find('"', 1)
            if end == -1:
                return remainder[1:]  # Unterminated quote; take what we have.
            return remainder[1:end]
        # Unquoted: value ends at semicolon or end of string.
        semi = remainder.find(';')
        if semi == -1:
            return remainder.strip()
        return remainder[:semi].strip()
