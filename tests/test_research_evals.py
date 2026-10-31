import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from desk_companion.agent_eval_research import URLS,YEARS,prepare_corpus,load_corpus,judge,fixture_delta


class ResearchEvalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def test_frozen_corpus_checks_actual_hashes_paths_and_live_scope(self):
        result=prepare_corpus(self.root);folder=self.root/'memory/eval_research_corpus'
        self.assertEqual(result['scope'],'synthetic_fixture');self.assertEqual(len(load_corpus(folder)[1]),2)
        with self.assertRaises(ValueError):prepare_corpus(self.root/'live',folder,live=True)
        with self.assertRaises(ValueError):prepare_corpus(self.root/'no-source',live=True)
        (folder/'rfc8259.txt').write_text('tampered',encoding='utf-8')
        with self.assertRaises(ValueError):load_corpus(folder)

    def test_corpus_cannot_use_an_outside_file_even_with_matching_hash(self):
        prepare_corpus(self.root);folder=self.root/'memory/eval_research_corpus'
        manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        outside=self.root/'outside.txt';outside.write_text('outside',encoding='utf-8')
        manifest['sources'][0].update(file=str(outside),characters=7,sha256=hashlib.sha256(b'outside').hexdigest())
        (folder/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        with self.assertRaises(ValueError):load_corpus(folder)

    def successful(self):
        root=dict(id='root',state='succeeded');children=[];answers={};hashes={};assignments={}
        for number,url in URLS.items():
            text=f'Request for Comments: {number}\nPublication year: {YEARS[number]}';digest=hashlib.sha256(text.encode()).hexdigest()
            answer=dict(number=number,year=YEARS[number],citation='[source]('+url+')');answers['rfc'+str(number)]=answer
            agent='reader-'+str(number);assignments[agent]=url;hashes[url]=digest
            children.append(dict(id=agent,parent='root',agent=agent,state='succeeded',dependencies=[],execution_runtime='atlas_readonly',answer=json.dumps(answer),
                feedback_check=dict(ok=True),evidence=[dict(kind='page',level='body',url=url,text=text,sha256=digest)]))
        root['answer']=json.dumps(answers)
        proof=dict(accepted_delivery=True,workers_exited_before_shutdown=True,research_mode='atlas_parallel',worker_overlap=True,child_intervals_complete=True,execution_strategy='delegate',actual_requested_strategy='delegate',
            reader_assignments=assignments,corpus_hashes=hashes)
        return root,children,proof

    def test_judge_requires_actual_source_runtime_citations_and_settled_workers(self):
        root,children,proof=self.successful();self.assertTrue(judge(root,children,proof))
        for change in ('failed','wrong-runtime','no-body','wrong-hash','fake-citation','pending-worker','missing-intervals','missing-strategy','no-overlap'):
            r,c,p=copy.deepcopy((root,children,proof))
            if change=='failed':c[0]['state']='failed'
            elif change=='wrong-runtime':c[0]['execution_runtime']='legacy'
            elif change=='no-body':c[0]['evidence']=[]
            elif change=='wrong-hash':c[0]['evidence'][0]['sha256']='0'*64
            elif change=='fake-citation':
                answer=json.loads(c[0]['answer']);answer['citation']='https://example.org/not-read';c[0]['answer']=json.dumps(answer)
            elif change=='pending-worker':p['workers_exited_before_shutdown']=False
            elif change=='missing-intervals':p.update(research_mode='serial',child_intervals_complete=False,worker_overlap=False)
            elif change=='missing-strategy':p['actual_requested_strategy']='auto'
            else:p['worker_overlap']=False
            with self.subTest(change=change):self.assertFalse(judge(r,c,p))

    def test_fixture_model_extracts_year_from_actual_tool_body_not_expected_constant(self):
        source=dict(url=URLS[8259],level='body',text='Request for Comments: 8259\nPublication year: 2019')
        request=dict(tools=[dict(function=dict(name='web_fetch'))],messages=[dict(role='tool',content=json.dumps(source))])
        value=fixture_delta(request,{},lambda *a:None)
        self.assertEqual(json.loads(value['content'])['year'],2019)
