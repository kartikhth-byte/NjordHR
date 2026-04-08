import sys
import types
import unittest


def _stub_ai_dependencies():
    if "fitz" not in sys.modules:
        sys.modules["fitz"] = types.ModuleType("fitz")

    if "PIL" not in sys.modules:
        pil_module = types.ModuleType("PIL")
        image_module = types.ModuleType("PIL.Image")
        pil_module.Image = image_module
        sys.modules["PIL"] = pil_module
        sys.modules["PIL.Image"] = image_module

    if "pinecone" not in sys.modules:
        pinecone_module = types.ModuleType("pinecone")

        class DummyPinecone:
            def __init__(self, *_args, **_kwargs):
                pass

        class DummyServerlessSpec:
            def __init__(self, *_args, **_kwargs):
                pass

        pinecone_module.Pinecone = DummyPinecone
        pinecone_module.ServerlessSpec = DummyServerlessSpec
        sys.modules["pinecone"] = pinecone_module


_stub_ai_dependencies()
from ai_analyzer import RAGPrepper  # noqa: E402


class _FakeConfig:
    embedding_model = "text-embedding-004"
    gemini_api_key = "test-key"


class RAGChunkingTests(unittest.TestCase):
    def setUp(self):
        self.prepper = RAGPrepper(_FakeConfig())

    def test_chunk_text_preserves_paragraph_boundaries_before_fallback(self):
        paragraph_one = " ".join(f"alpha{i}" for i in range(260))
        paragraph_two = " ".join(f"beta{i}" for i in range(220))
        text = f"{paragraph_one}\n\n{paragraph_two}"

        chunks = self.prepper.chunk_text(text, "resume-1", "Master")

        self.assertGreaterEqual(len(chunks), 2)
        self.assertIn("alpha0", chunks[0]["text"])
        self.assertNotIn("beta0", chunks[0]["text"])
        self.assertIn("beta0", chunks[1]["text"])
        self.assertEqual(chunks[0]["metadata"]["resume_id"], "resume-1")
        self.assertEqual(chunks[0]["metadata"]["rank"], "Master")

    def test_chunk_text_keeps_table_like_lines_together_when_block_fits(self):
        table_lines = [
            "Certificate No Certificate Type Issue Authority Issue Date Expiry Date",
            "IF0017969 Master(FG) India 28-Feb-2025 27-Feb-2030 02DL4857",
            "CoC0097228 First Mate (FG) UK 14-Jun-2022 18-May-2027 07NL1786",
            "IF39438 Second Mate (FG) Indian 23-Apr-2023 19-May-2028 10NL3975",
        ]
        long_paragraph = " ".join(f"gamma{i}" for i in range(430))
        text = "\n".join(table_lines) + "\n\n" + long_paragraph

        chunks = self.prepper.chunk_text(text, "resume-2", "Chief Officer")

        self.assertGreaterEqual(len(chunks), 2)
        first_chunk = chunks[0]["text"]
        for line in table_lines:
            self.assertIn(line, first_chunk)
        self.assertNotIn("gamma0", first_chunk)

    def test_chunk_text_splits_oversized_block_without_dropping_text(self):
        text = " ".join(f"token{i}" for i in range(950))

        chunks = self.prepper.chunk_text(text, "resume-3", "2nd Engineer")

        self.assertGreater(len(chunks), 2)
        self.assertIn("token0", chunks[0]["text"])
        self.assertIn("token949", chunks[-1]["text"])
        self.assertTrue(all(chunk["metadata"]["raw_text"] for chunk in chunks))
        self.assertTrue(all(len(chunk["text"].split()) <= 450 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
