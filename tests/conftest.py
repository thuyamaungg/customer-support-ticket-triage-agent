import pytest
from sqlalchemy.orm import sessionmaker

from app.db import init_db, make_engine


@pytest.fixture
def engine(tmp_path):
    # A real file (not :memory:) so several threads can share it in the concurrency tests.
    eng = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_db(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture
def client(session_factory):
    """API client wired to the temporary test database (the lifespan's init_db is not run)."""
    from fastapi.testclient import TestClient

    from app.db import get_session, get_session_factory
    from app.main import app
    from app.webhooks import kiosk_limiter

    def _test_session():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _test_session
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    kiosk_limiter.reset()
    yield TestClient(app)
    app.dependency_overrides.clear()
    kiosk_limiter.reset()


class FakeLLM:
    """Stands in for the real model in every test, so tests never call the API (or cost money).

    fake_llm.output = {...}   -> what the model "returns"
    fake_llm.error = Exc(...) -> make the call fail
    fake_llm.prompts          -> every prompt the model received
    """

    DEFAULT_OUTPUT = {
        "issue_type": "hardware_repair",
        "urgency": "high",
        "confidence": 0.92,
        "detected_language": "en",
        "summary": "Laptop will not turn on.",
        "draft_reply": "Sorry to hear that! Please bring it in today.",
        "reasoning": "Device will not power on.",
    }

    def __init__(self):
        from pydantic_ai import Agent
        from pydantic_ai.messages import ModelResponse, ToolCallPart
        from pydantic_ai.models.function import FunctionModel

        from app.triage import TriageOutput

        self.output = dict(self.DEFAULT_OUTPUT)
        self.error = None
        self.prompts = []

        def respond(messages, info):
            self.prompts.append(messages[-1].parts[-1].content)
            if self.error:
                raise self.error
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, self.output)])

        self.agent = Agent(FunctionModel(respond), output_type=TriageOutput)


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    from app import triage

    fake = FakeLLM()
    # Patch the module function (not a context variable) so it also works inside
    # background tasks, which run on other threads.
    monkeypatch.setattr(triage, "get_agent", lambda: fake.agent)
    return fake
