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
from ai_analyzer import PineconeManager  # noqa: E402


class _FakeIndex:
    def __init__(self, has_matches, stats_counts=None, list_pages=None, list_error=None):
        self.has_matches = has_matches
        self.stats_counts = list(stats_counts or [0])
        self.list_pages = list(list_pages or [[]])
        self.list_error = list_error
        self._stats_pos = 0
        self._query_pos = 0
        self._list_pos = 0
        self.upsert_calls = []

    def describe_index_stats(self):
        pos = min(self._stats_pos, len(self.stats_counts) - 1)
        count = self.stats_counts[pos]
        self._stats_pos += 1
        return {"namespaces": {"Master": {"vector_count": count}}}

    def query(self, **_kwargs):
        has_matches = self.has_matches
        if isinstance(self.has_matches, list):
            pos = min(self._query_pos, len(self.has_matches) - 1)
            has_matches = self.has_matches[pos]
        self._query_pos += 1
        return {"matches": [{"id": "resume-1"}] if has_matches else []}

    def list(self, **_kwargs):
        if self.list_error is not None:
            raise self.list_error
        pos = min(self._list_pos, len(self.list_pages) - 1)
        page = self.list_pages[pos]
        self._list_pos += 1
        return iter([page])

    def upsert(self, **kwargs):
        self.upsert_calls.append(kwargs)


class _FakeIndexList:
    def __init__(self, names):
        self._names = names

    def names(self):
        return self._names


class _FakePinecone:
    def __init__(self):
        self.indexes = {
            "elegant-dogwood-v2": _FakeIndex(False),
            "elegant-dogwood-v2-d768": _FakeIndex(False),
            "elegant-dogwood-v2-d3072": _FakeIndex(True, list_pages=[["resume-1"]]),
        }

    def list_indexes(self):
        return _FakeIndexList(list(self.indexes.keys()))

    def Index(self, name):
        return self.indexes[name]


class PineconeManagerTests(unittest.TestCase):
    def test_namespace_vector_count_checks_alternate_dimension_indexes(self):
        manager = PineconeManager.__new__(PineconeManager)
        manager.pc = _FakePinecone()
        manager.base_index_name = "elegant-dogwood-v2"
        manager.index_name = "elegant-dogwood-v2-d768"
        manager.embedding_dimension = 768
        manager._index = manager.pc.Index("elegant-dogwood-v2-d768")
        manager.last_error = None

        self.assertEqual(manager.namespace_vector_count("Master"), 1)

    def test_namespace_vector_count_checks_legacy_base_index(self):
        manager = PineconeManager.__new__(PineconeManager)
        manager.pc = _FakePinecone()
        manager.pc.indexes["elegant-dogwood-v2"] = _FakeIndex(True, list_pages=[["resume-1"]])
        manager.pc.indexes["elegant-dogwood-v2-d3072"] = _FakeIndex(False)
        manager.base_index_name = "elegant-dogwood-v2"
        manager.index_name = "elegant-dogwood-v2-d768"
        manager.embedding_dimension = 768
        manager._index = manager.pc.Index("elegant-dogwood-v2-d768")
        manager.last_error = None

        self.assertEqual(manager.namespace_vector_count("Master"), 1)

    def test_upsert_chunks_resolves_target_index_before_writing(self):
        manager = PineconeManager.__new__(PineconeManager)
        manager.pc = _FakePinecone()
        manager.base_index_name = "elegant-dogwood-v2"
        manager.index_name = "elegant-dogwood-v2-d768"
        manager.embedding_dimension = 768
        manager._index = manager.pc.Index("elegant-dogwood-v2-d768")
        manager.last_error = None

        chunks = [{"metadata": {"resume_id": "resume-1"}}]
        embeddings = [[0.1] * 3072]
        manager.upsert_chunks(chunks, embeddings, "Master")

        self.assertEqual(manager.index_name, "elegant-dogwood-v2-d3072")
        self.assertEqual(len(manager.pc.indexes["elegant-dogwood-v2-d768"].upsert_calls), 0)
        self.assertEqual(len(manager.pc.indexes["elegant-dogwood-v2-d3072"].upsert_calls), 1)

    def test_namespace_vector_count_retries_before_declaring_empty(self):
        manager = PineconeManager.__new__(PineconeManager)
        manager.pc = _FakePinecone()
        manager.pc.indexes["elegant-dogwood-v2-d768"] = _FakeIndex(False, stats_counts=[0, 0, 0])
        manager.pc.indexes["elegant-dogwood-v2-d3072"] = _FakeIndex(
            [False, True], stats_counts=[0, 0, 0], list_pages=[[], ["resume-1"]]
        )
        manager.pc.indexes["elegant-dogwood-v2"] = _FakeIndex(False, stats_counts=[0, 0, 0])
        manager.base_index_name = "elegant-dogwood-v2"
        manager.index_name = "elegant-dogwood-v2-d768"
        manager.embedding_dimension = 768
        manager._index = manager.pc.Index("elegant-dogwood-v2-d768")
        manager.last_error = None
        manager.EMPTY_NAMESPACE_RETRY_COUNT = 2
        manager.EMPTY_NAMESPACE_RETRY_DELAY_SECONDS = 0

        self.assertEqual(manager.namespace_vector_count("Master"), 1)

    def test_namespace_probe_falls_back_to_query_when_list_unavailable(self):
        manager = PineconeManager.__new__(PineconeManager)
        manager.pc = _FakePinecone()
        manager.pc.indexes["elegant-dogwood-v2-d3072"] = _FakeIndex(
            True, list_error=RuntimeError("list unsupported")
        )
        manager.base_index_name = "elegant-dogwood-v2"
        manager.index_name = "elegant-dogwood-v2-d768"
        manager.embedding_dimension = 768
        manager._index = manager.pc.Index("elegant-dogwood-v2-d768")
        manager.last_error = None

        self.assertTrue(
            manager.namespace_has_vectors("Master", index_name="elegant-dogwood-v2-d3072")
        )


if __name__ == "__main__":
    unittest.main()
