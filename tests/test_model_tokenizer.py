import json
import tempfile
import unittest
from pathlib import Path

from desk_companion import model_tokenizer as mt


class RealTokenizerTests(unittest.TestCase):
    def setUp(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        from transformers import PreTrainedTokenizerFast
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        vocab={'[UNK]':0,'user':1,'assistant':2,'system':3,'Hello':4,'world':5,'工具':6}
        raw=Tokenizer(models.WordLevel(vocab,unk_token='[UNK]'));raw.pre_tokenizer=pre_tokenizers.Whitespace()
        self.tokenizer=PreTrainedTokenizerFast(tokenizer_object=raw,unk_token='[UNK]')
        self.tokenizer.chat_template="{% for m in messages %}{{m.role}} {{m.content}} {% endfor %}{% if tools %}{{tools|tojson}}{% endif %} assistant"
        self.tokenizer.save_pretrained(self.temp.name)
        self.binding=mt.binding(self.temp.name)
        self.request=dict(messages=[dict(role='user',content='Hello world')])

    def test_real_template_count_includes_tools_but_not_http_parameters(self):
        request={**self.request,'tools':[dict(type='function',function=dict(name='工具',parameters=dict(type='object')))]}
        expected=len(self.tokenizer.apply_chat_template(request['messages'],tools=request['tools'],tokenize=True,add_generation_prompt=True,return_dict=False))
        value=mt.count(request,self.binding)
        self.assertEqual(value['tokens'],expected);self.assertEqual(value['source'],'tokenizer')
        self.assertEqual(mt.count({**request,'temperature':.1,'max_tokens':9000},self.binding)['tokens'],expected)
        self.assertGreater(expected,mt.count(self.request,self.binding)['tokens'])

    def test_media_unbound_and_changed_files_are_unknown(self):
        self.assertIsNone(mt.count(self.request,{})['tokens'])
        media=dict(messages=[dict(role='user',content=[dict(type='image_url',image_url={'url':'https://example.org/x.png'})])])
        self.assertIsNone(mt.count(media,self.binding)['tokens'])
        (Path(self.temp.name)/'chat_template.jinja').write_text('changed','utf-8')
        self.assertIsNone(mt.count(self.request,self.binding)['tokens'])

    def test_execution_admission_recounts_the_actual_offline_template_with_tools(self):
        from desk_companion.execution_context import prepare_request
        import copy
        messages=[dict(role='system',content='Permission read only'),dict(role='user',content='Current goal')]
        for i,body in enumerate([dict(ok=True,source_id='original',text='Hello world '*2000),dict(recent=1),dict(recent=2)]):
            messages.extend([dict(role='assistant',content='',tool_calls=[dict(id=str(i),type='function',function=dict(name='read_local_source',arguments='{}'))]),
                             dict(role='tool',tool_call_id=str(i),content=json.dumps(body))])
        request=dict(model='fixture',messages=messages,tools=[dict(type='function',function=dict(name='工具',parameters=dict(type='object')))])
        before=copy.deepcopy(request);capacity={**self.binding,'window':1024,'output_reserve':128}
        projected,report=prepare_request(request,capacity)
        self.assertTrue(report['compacted']);self.assertEqual(report['input_source'],'tokenizer')
        self.assertGreater(report['input_tokens_before'],896)
        self.assertEqual(mt.count(projected,capacity)['tokens'],report['input_tokens'])
        self.assertLessEqual(report['input_tokens'],896);self.assertEqual(request,before)

    def test_missing_tool_template_or_untrusted_loader_fails_without_guessing(self):
        self.tokenizer.chat_template="{% for m in messages %}{{m.role}} {{m.content}}{% endfor %}"
        self.tokenizer.save_pretrained(self.temp.name);config=mt.binding(self.temp.name)
        self.assertIsNone(mt.count({**self.request,'tools':[{'name':'tool'}]},config)['tokens'])
        with self.assertRaises((ValueError,OSError,FileNotFoundError)):mt.binding(str(Path(self.temp.name)/'missing'))
