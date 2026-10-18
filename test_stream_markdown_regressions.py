"""Focused static checks for message-flow scrolling and Markdown rhythm."""
from pathlib import Path
import unittest


class StreamMarkdownRegressions(unittest.TestCase):
    source = Path("templates/index.html").read_text(encoding="utf-8")

    def test_stream_image_load_does_not_steal_history_scroll(self):
        self.assertIn("const liveDist = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight;", self.source)
        self.assertIn("if (liveDist < 100) scroll.scrollTop = scroll.scrollHeight;", self.source)
        self.assertNotIn("img.addEventListener('load', () => { scroll.scrollTop = scroll.scrollHeight; }", self.source)

    def test_markdown_has_balanced_edge_spacing_and_blocks(self):
        for rule in (
            ".markdown-content > :first-child { margin-top: 0; }",
            ".markdown-content > :last-child { margin-bottom: 0; }",
            ".markdown-content p { margin: 8px 0; }",
            ".markdown-content blockquote {",
            ".markdown-content table {",
            ".markdown-content hr {",
        ):
            self.assertIn(rule, self.source)


if __name__ == "__main__":
    unittest.main()
