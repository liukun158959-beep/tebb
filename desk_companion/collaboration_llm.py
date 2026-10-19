"""Product policies for collaboration; Atlas owns transport and stream assembly."""
from atlas.llm import LLM, ChatCallbacks, ChatOptions
from openai import OpenAI


class CollaborationLLM:
    def __init__(self, model, sampling, timeout, client=None, purpose='task'):
        self.model = model
        self.sampling = {**sampling, **({'reasoning_effort': 'low'} if purpose == 'route' else {})}
        self.preserve_reasoning = model['model'].lower().startswith(('glm-4.7', 'glm-5'))
        self.timeout = timeout
        self.client = client
        self.owned = client is None
        self._llm = None
        self.on_reasoning = None
        self.on_progress = None
        self.on_tool_delta = None

    def __enter__(self): return self

    def __exit__(self, *args): self.close()

    def close(self):
        if self._llm is not None:
            self._llm.close()
            self._llm = None
        if self.owned and self.client is not None:
            self.client.close()
            self.client = None

    def chat(self, messages, tools=None, on_delta=None, *, should_cancel=None):
        if self._llm is None:
            if self.client is None:
                from .model_network import http_client
                self.client = http_client(self.timeout)
            sdk = OpenAI(api_key=self.model['api_key'], base_url=self.model['base_url'],
                         timeout=self.client.timeout, max_retries=0, http_client=self.client)
            self._llm = LLM(model=self.model['model'], client=sdk)
        extra = {'thinking': {'clear_thinking': False}, 'tool_stream': True} if self.preserve_reasoning else None
        try:
            return self._llm.chat(messages, tools, options=ChatOptions(
                sampling=self.sampling, extra_body=extra, stream=True,
                preserve_reasoning=self.preserve_reasoning, should_cancel=should_cancel,
            ), callbacks=ChatCallbacks(on_delta=on_delta, on_reasoning=self.on_reasoning,
                on_progress=self.on_progress, on_tool_delta=self.on_tool_delta))
        except Exception:
            self.close()
            raise
