"""
Authors: Claire Liu, Yu-Jing Wei
Description: Pytest configuration that stubs heavy third-party modules (YOLO, torch,
confluent_kafka, reid_extractor) before kafka_worker is imported, so unit tests for
the pure helpers don't need GPU/network dependencies installed.
"""
import sys
import types


def _install_stub(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# ── confluent_kafka ────────────────────────────────────────────────────────
class _FakeProducer:
    def __init__(self, *a, **kw): pass
    def produce(self, *a, **kw): pass
    def poll(self, *a, **kw): pass
    def flush(self, *a, **kw): pass


class _FakeKafkaError(Exception):
    pass


_install_stub(
    "confluent_kafka",
    Producer=_FakeProducer,
    KafkaError=_FakeKafkaError,
    KafkaException=_FakeKafkaError,
)


# ── ultralytics ────────────────────────────────────────────────────────────
class _FakeYOLO:
    def __init__(self, *a, **kw): pass
    def track(self, *a, **kw): return []


_install_stub("ultralytics", YOLO=_FakeYOLO)


# ── reid_extractor ─────────────────────────────────────────────────────────
_install_stub("reid_extractor", extract_embedding=lambda *a, **kw: [])
