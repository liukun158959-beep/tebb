import unittest

from atlas.testing.llm_contract import StreamContract
from desk_companion.collaboration_llm import CollaborationLLM


class CollaborationStreamContract(StreamContract, unittest.TestCase):
    def make_model(self, transport):
        return CollaborationLLM(dict(model='fixture', api_key='fixture', base_url='https://example.invalid/v1'),
                                dict(temperature=.5, top_p=1, reasoning_effort='low'), 20, transport)

    def call_model(self, model, on_delta=None, should_cancel=None):
        return model.chat([], on_delta=on_delta, should_cancel=should_cancel)
