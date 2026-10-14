# Frozen fixture for transcripts/code-reviewer-drill.json: the drill transcript cites lines of this file.
# Do not edit; the cited line numbers must keep pointing at the same content.
import unittest


def add(a, b):
    return a + b


class TestGolden(unittest.TestCase):
    def test_add(self):
        self.assertEqual(add(1, 2), 3)


class TestBad(unittest.TestCase):
    def test_add_wrong_type(self):
        with self.assertRaises(TypeError):
            add(1, "2")


class TestTranscript(unittest.TestCase):
    def test_empty_final_fails(self):
        self.assertFalse("")


if __name__ == "__main__":
    unittest.main()
