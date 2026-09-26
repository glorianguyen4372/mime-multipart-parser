import unittest

from mime_multipart_parser import MultipartParser, ParseError


class TestMultipartParser(unittest.TestCase):
    def setUp(self):
        self.parser = MultipartParser()

    def make_body(self, parts, boundary='boundary', closing=True, crlf=True):
        """Helper to build a multipart body from a list of part dicts.

        Each part dict has optional 'headers' (list of (name, value) tuples)
        and 'body' (bytes).
        """
        sep = b'\r\n' if crlf else b'\n'
        delimiter = b'--' + boundary.encode()
        chunks = []
        for part in parts:
            chunks.append(delimiter + sep)
            for name, value in part.get('headers', []):
                chunks.append(name.encode() + b': ' + value.encode() + sep)
            chunks.append(sep)
            body = part.get('body', b'')
            if body:
                chunks.append(body + sep)
        if closing:
            chunks.append(delimiter + b'--' + sep)
        return b''.join(chunks)

    def test_single_part(self):
        body = self.make_body([
            {'headers': [('Content-Type', 'text/plain')], 'body': b'hello'}
        ])
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['headers']['content-type'], 'text/plain')
        self.assertEqual(parts[0]['body'], 'hello')

    def test_multiple_parts(self):
        body = self.make_body([
            {'headers': [('Content-Type', 'text/plain')], 'body': b'first'},
            {'headers': [('Content-Type', 'text/html')], 'body': b'<p>second</p>'},
        ])
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0]['body'], 'first')
        self.assertEqual(parts[1]['body'], '<p>second</p>')

    def test_empty_body_returns_empty_list(self):
        # A body that is just the closing delimiter, no parts.
        body = b'--boundary--\r\n'
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(parts, [])

    def test_part_with_no_headers(self):
        body = self.make_body([{'body': b'just body'}])
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['headers'], {})
        self.assertEqual(parts[0]['body'], 'just body')

    def test_part_with_no_body(self):
        body = (
            b'--boundary\r\n'
            b'Content-Type: text/plain\r\n'
            b'\r\n'
            b'--boundary--\r\n'
        )
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['headers']['content-type'], 'text/plain')
        self.assertEqual(parts[0]['body'], '')

    def test_charset_decoding(self):
        body = self.make_body([
            {
                'headers': [('Content-Type', 'text/plain; charset=utf-8')],
                'body': 'héllo'.encode('utf-8'),
            }
        ])
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(parts[0]['body'], 'héllo')

    def test_unknown_charset_falls_back_to_surrogateescape(self):
        body = self.make_body([
            {
                'headers': [('Content-Type', 'text/plain; charset=nonexistent-charset')],
                'body': b'\xff\xfe binary',
            }
        ])
        parts = self.parser.parse(body, 'boundary')
        # Should not raise; body is losslessly decoded via surrogateescape.
        self.assertEqual(parts[0]['body'].encode('ascii', errors='surrogateescape'), b'\xff\xfe binary')

    def test_no_charset_uses_ascii_surrogateescape(self):
        body = self.make_body([
            {'headers': [('Content-Type', 'text/plain')], 'body': b'plain \xff'}
        ])
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(parts[0]['body'].encode('ascii', errors='surrogateescape'), b'plain \xff')

    def test_header_folding(self):
        body = (
            b'--boundary\r\n'
            b'Content-Type: text/plain;\r\n'
            b' charset=utf-8\r\n'
            b'\r\n'
            b'folded\r\n'
            b'--boundary--\r\n'
        )
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(parts[0]['headers']['content-type'], 'text/plain; charset=utf-8')

    def test_repeated_headers_joined(self):
        body = (
            b'--boundary\r\n'
            b'X-Custom: one\r\n'
            b'X-Custom: two\r\n'
            b'\r\n'
            b'body\r\n'
            b'--boundary--\r\n'
        )
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(parts[0]['headers']['x-custom'], 'one, two')

    def test_preamble_ignored(self):
        body = (
            b'This is a preamble.\r\n'
            b'Ignore me.\r\n'
            b'--boundary\r\n'
            b'\r\n'
            b'real\r\n'
            b'--boundary--\r\n'
        )
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['body'], 'real')

    def test_epilogue_ignored(self):
        body = (
            b'--boundary\r\n'
            b'\r\n'
            b'real\r\n'
            b'--boundary--\r\n'
            b'This is an epilogue.\r\n'
            b'Ignore me.\r\n'
        )
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['body'], 'real')

    def test_trailing_whitespace_on_delimiter(self):
        body = (
            b'--boundary   \r\n'
            b'\r\n'
            b'body\r\n'
            b'--boundary--\r\n'
        )
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['body'], 'body')

    def test_lf_line_endings(self):
        body = (
            b'--boundary\n'
            b'Content-Type: text/plain\n'
            b'\n'
            b'lf body\n'
            b'--boundary--\n'
        )
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['body'], 'lf body')

    def test_truncated_body_no_closing_delimiter(self):
        body = (
            b'--boundary\r\n'
            b'\r\n'
            b'incomplete\r\n'
        )
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]['body'], 'incomplete')

    def test_quoted_charset(self):
        body = self.make_body([
            {
                'headers': [('Content-Type', 'text/plain; charset="utf-8"')],
                'body': 'café'.encode('utf-8'),
            }
        ])
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(parts[0]['body'], 'café')

    def test_empty_boundary_raises(self):
        with self.assertRaises(ParseError):
            self.parser.parse(b'--\r\nbody\r\n----\r\n', '')

    def test_no_delimiter_found_returns_empty(self):
        body = b'just some text with no boundaries'
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(parts, [])

    def test_header_names_lowercased(self):
        body = (
            b'--boundary\r\n'
            b'CONTENT-TYPE: text/plain\r\n'
            b'X-MY-HEADER: value\r\n'
            b'\r\n'
            b'body\r\n'
            b'--boundary--\r\n'
        )
        parts = self.parser.parse(body, 'boundary')
        self.assertIn('content-type', parts[0]['headers'])
        self.assertIn('x-my-header', parts[0]['headers'])
        self.assertEqual(parts[0]['headers']['content-type'], 'text/plain')

    def test_body_with_delimiter_like_text(self):
        # Text that contains the delimiter substring but not as a full line
        # should not be mistaken for a real delimiter.
        body = (
            b'--boundary\r\n'
            b'\r\n'
            b'--boundaryish text\r\n'
            b'not a delimiter\r\n'
            b'--boundary--\r\n'
        )
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 1)
        self.assertIn('not a delimiter', parts[0]['body'])

    def test_multiple_parts_with_mixed_headers(self):
        body = self.make_body([
            {'headers': [('Content-Disposition', 'form-data; name="field1"')], 'body': b'value1'},
            {'headers': [('Content-Disposition', 'form-data; name="field2"'), ('Content-Type', 'text/plain')], 'body': b'value2'},
        ])
        parts = self.parser.parse(body, 'boundary')
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0]['headers']['content-disposition'], 'form-data; name="field1"')
        self.assertEqual(parts[1]['headers']['content-type'], 'text/plain')
        self.assertEqual(parts[1]['body'], 'value2')


if __name__ == '__main__':
    unittest.main()
