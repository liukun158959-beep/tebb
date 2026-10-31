import json
import unittest

from desk_companion import feedback
from desk_companion.web_sources import source_catalog,render_citations,check_citations


class FeedbackTests(unittest.TestCase):
    def test_json_citations_preserve_document_and_code_examples(self):
        sources=[dict(url='https://example.org/actual',level='body',text='The launch was in 2020.')]
        ident=source_catalog(sources)[0]['id']
        original=dict(year=2020,citation=f'[{ident}]',code='curl https://api.example.org/not-read')
        rendered,unknown=render_citations('```json\n'+json.dumps(original)+'\n```',sources,structured=True)
        value=feedback.json_answer(rendered)
        self.assertEqual(set(value),set(original));self.assertEqual(value['code'],original['code'])
        self.assertIn('https://example.org/actual',value['citation']);self.assertEqual(unknown,[])
        self.assertTrue(check_citations(rendered,sources,structured=True)['ok'])
        # An ordinary fenced JSON example remains code unless explicitly a JSON answer.
        self.assertFalse(check_citations('```json\n{"url":"https://example.org/actual"}\n```',sources)['ok'])

    def test_json_contract_does_not_accept_extra_prose_or_change_types(self):
        goal='请计算。最终只返回相同JSON。'
        schema=dict(type='object',properties=dict(total=dict(type='integer'),checked=dict(type='boolean')),required=['total','checked'],additionalProperties=False)
        contract=dict(format='json',schema=schema)
        good=feedback.check_output('{"total":18,"checked":true}',goal,contract)
        self.assertTrue(all(x['status']=='pass'for x in good))
        self.assertEqual(feedback.check_output('# 报告\n{"total":18,"checked":true}',goal)[0]['status'],'fail')
        self.assertEqual(feedback.check_output('{"total":true,"checked":true}',goal,contract)[-1]['status'],'fail')
        self.assertIsNone(feedback.output_contract('解释“只返回JSON”是什么意思，请用普通段落。'))
        self.assertIsNone(feedback.output_contract('解释“A，最终只返回JSON，B”这句示例。'))
        self.assertEqual(feedback.check_output('{"total":NaN}',goal)[0]['status'],'fail')
        self.assertEqual(feedback.check_output('{"checked":false,"checked":true}',goal)[0]['status'],'fail')
        self.assertIsNone(feedback.output_contract('只输出JSON。\n最终只返回Markdown。'))
        self.assertEqual(feedback.check_output('# report\n{}','Return only JSON object.')[0]['status'],'fail')
        self.assertEqual(feedback.check_output('not JSON','【输出格式】只返回一个合法 JSON 对象，包含数据。')[0]['status'],'fail')

    def test_remote_schema_reference_is_rejected_without_network(self):
        with self.assertRaises(ValueError):
            feedback.output_contract('x',dict(format='json',schema={'$ref':'https://example.org/schema'}))

    def test_receipt_success_does_not_prove_artifact_or_test_coverage(self):
        checks=feedback.check_receipts([dict(tool='workspace_run',call_id='call1',result=dict(state='succeeded',exit_code=0))],
            [dict(id='build',kind='command',call_id='call1'),dict(id='bundle',kind='artifact',receipt_id='file1'),dict(id='coverage',kind='test_coverage',receipt_id='tests1')])
        by_id={x['id']:x for x in checks}
        self.assertEqual(by_id['build']['status'],'pass')
        self.assertEqual(by_id['bundle']['status'],'unknown')
        self.assertEqual(by_id['coverage']['status'],'unknown')
        self.assertEqual(feedback.report(checks)['completion'],'unverified')

    def test_known_missing_artifact_fails_even_after_exit_zero(self):
        checks=feedback.check_receipts([dict(tool='artifact_check',call_id='file1',result=dict(exists=False))],
            [dict(id='bundle',kind='artifact',receipt_id='file1')])
        self.assertEqual(checks[0]['status'],'fail')
        self.assertEqual(feedback.report(checks)['completion'],'incomplete')

    def test_model_assessment_is_not_deterministic_verification(self):
        checks=[feedback.check('claim','claim_support','pass','审阅认为支持',basis='model_assessment')]
        self.assertEqual(feedback.report(checks)['completion'],'assessed')
        self.assertIn('不代表',feedback.report(checks)['scope'])

    def test_artifact_binds_actual_file_read_and_not_command_claim(self):
        receipts=[dict(tool='workspace_run',call_id='command',arguments=dict(command='echo created'),result=dict(exit_code=0)),
            dict(tool='project_read',call_id='file',arguments=dict(path='dist/index.html'),result=dict(ok=True,kind='file',sha256='a'*64))]
        checks=feedback.check_receipts(receipts,[dict(id='bundle',kind='artifact',path='dist/index.html'),dict(id='wrong',kind='artifact',path='dist/missing.html')])
        self.assertEqual(checks[0]['status'],'pass');self.assertEqual(checks[0]['receipt_id'],'file')
        self.assertEqual(checks[1]['status'],'unknown')

    def test_unknown_operation_identity_survives_feedback(self):
        receipts=[dict(call_id='call',result=dict(operation=dict(id='original',state='unknown'),log_id='log'))]
        record=feedback.report([],receipts=receipts)['unknown_operations'][0]
        self.assertEqual(record['operation_id'],'original');self.assertEqual(record['state'],'unknown')
        self.assertEqual(record['action'],'reconcile_original_receipt_do_not_replay')

    def test_same_path_in_another_environment_cannot_prove_requested_artifact(self):
        rows=[dict(tool='project_read',call_id='file_'+name,arguments=dict(path='dist/index.html',environment=name),result=dict(ok=True,sha256='a'*64))for name in ['A','B']]
        checks=feedback.check_receipts(rows,[dict(id='ambiguous',kind='artifact',path='dist/index.html'),dict(id='bound',kind='artifact',path='dist/index.html',environment='A')])
        self.assertEqual(checks[0]['status'],'unknown');self.assertEqual(checks[1]['receipt_id'],'file_A')

    def test_only_receipt_for_the_same_operation_resolves_unknown(self):
        first=dict(tool='project_prepare',read_only=False,call_id='first',result=dict(operation=dict(id='original',state='unknown')))
        other=dict(tool='project_status',read_only=True,call_id='other',result=dict(operation=dict(id='unrelated',state='succeeded')))
        self.assertEqual(feedback.report([],receipts=[first,other])['completion'],'unverified')
        self.assertEqual(len(feedback.report([],receipts=[first,other])['unknown_operations']),1)
        resolved=dict(tool='project_status',read_only=True,call_id='resolved',result=dict(operation=dict(id='original',state='succeeded')))
        self.assertEqual(feedback.report([],receipts=[first,resolved])['unknown_operations'],[])

    def test_calendar_unconfirmed_and_board_request_ids_use_original_receipts(self):
        calendar=dict(call_id='write',read_only=False,result=dict(operation=dict(id='calendar-op',state='unconfirmed'),change_state='unconfirmed'))
        board=dict(call_id='board',read_only=False,result=dict(request_id='board-op',change_state='unconfirmed'))
        unknown=feedback.pending_operations([calendar,board])
        self.assertEqual({x['operation_id']for x in unknown},{'calendar-op','board-op'})
        observed=dict(call_id='query',read_only=True,result=dict(operation=dict(id='calendar-op',state='synced')))
        loaded=dict(call_id='load',read_only=True,result=dict(ok=True,receipt=dict(request_id='board-op')))
        self.assertEqual(feedback.pending_operations([calendar,board,observed,loaded]),[])

    def test_opaque_write_failure_remains_unknown_but_known_preflight_does_not(self):
        opaque=dict(call_id='attempt',read_only=False,status='failed',result='Error: connection closed')
        self.assertTrue(feedback.pending_operations([opaque])[0]['write_attempted'])
        known=dict(opaque,result=dict(ok=False,change_state='not_changed'))
        self.assertEqual(feedback.pending_operations([known]),[])

    def test_failed_or_in_progress_query_is_not_resolution_of_original_write(self):
        first=dict(call_id='write',read_only=False,result=dict(operation=dict(id='original',state='unknown')))
        for result in [dict(ok=False,request_id='original',error='query failed'),dict(ok=True,operation=dict(id='original',state='running')),
                       dict(ok=False,request_id='original',change_state='not_changed',error='query did not run')]:
            query=dict(call_id='query',read_only=True,result=result)
            pending=feedback.pending_operations([first,query])
            self.assertEqual(len(pending),1);self.assertEqual(pending[0]['operation_id'],'original')

    def test_repair_is_once_and_blocks_mutations_even_when_model_requests_them(self):
        repair=feedback.RepairBudget()
        self.assertTrue(repair.begin());self.assertFalse(repair.begin())
        self.assertFalse(repair.allows('project_create',dict(read_only=False)))
        self.assertFalse(repair.allows('calendar_change',dict(read_only=False)))
        self.assertTrue(repair.allows('project_status',dict(read_only=True)))
        self.assertTrue(repair.allows('build_chart',dict(read_only=False)))

    def test_review_separates_real_source_and_supported_assertion(self):
        from desk_companion.feedback_review import material,interpret
        sources=[dict(url='https://example.org/actual',level='body',text='The launch was in 2020.')]
        rows=material('When was launch?','It launched in 2019.',sources,[])
        ident=rows[-1]['id']
        item=dict(requirement='launch year',dimension='claim_support',status='contradicted',reason='2019 conflicts with the read source',evidence=[dict(source_id=ident,quote='The launch was in 2020.')])
        checks=interpret(dict(requirements=[item]),rows)
        self.assertEqual(checks[0]['status'],'fail');self.assertEqual(checks[0]['basis'],'model_assessment')
        item.update(status='supported',evidence=[dict(source_id=ident,quote='The launch was in 2019.')])
        self.assertEqual(interpret(dict(requirements=[item]),rows)[0]['status'],'unknown')

    def test_review_cannot_promote_snippet_or_quote_across_unread_gap(self):
        from desk_companion.feedback_review import material,interpret
        sources=[dict(url='https://example.org/actual',level='body',text='first part',snapshot_id='snap',characters=100,read_ranges=[[0,10]],truncated=True),
                 dict(url='https://example.org/actual',level='body',text='last part',snapshot_id='snap',characters=100,read_ranges=[[90,100]],truncated=True)]
        rows=material('read whole','claim',sources,[]);ident=rows[-1]['id']
        item=dict(requirement='claim',dimension='claim_support',status='supported',reason='claimed support',evidence=[dict(source_id=ident,quote='first part\nlast part')])
        self.assertEqual(interpret(dict(requirements=[item]),rows)[0]['status'],'unknown')
        sources[0].update(level='snippet',text='summary')
        rows=material('read whole','claim',sources[:1],[])
        item['evidence']=[dict(source_id=ident,quote='summary')]
        self.assertEqual(interpret(dict(requirements=[item]),rows)[0]['status'],'unknown')

    def test_whole_candidate_json_equivalence_is_not_external_quote_fuzziness(self):
        from desk_companion.feedback_review import material,interpret
        rows=material('JSON only','{"n": 18, "checked": true}',[],[])
        item=dict(requirement='JSON shape',dimension='task_acceptance',status='supported',reason='matching structured candidate',evidence=[dict(source_id='draft',quote='{"checked":true,"n":18}')])
        result=interpret(dict(requirements=[item]),rows)[0]
        self.assertEqual(result['status'],'pass');self.assertEqual(result['evidence'][0]['match_kind'],'json_value_equivalent')
        for bad in ['{"checked":1,"n":18}','{"checked":true,"n":19}','{"checked":true}', '{"n":18,"checked":false,"checked":true}']:
            item['evidence'][0]['quote']=bad
            self.assertEqual(interpret(dict(requirements=[item]),rows)[0]['status'],'unknown')

    def test_reason_alias_and_level_metadata_cannot_override_source_ownership(self):
        from desk_companion.feedback_review import material,interpret
        rows=material('check','claim',[dict(level='snippet',url='https://example.org/source',text='summary')],[])
        item=dict(requirement='claim',dimension='claim_support',status='supported',status_reason='model explanation',evidence=[dict(source_id=rows[-1]['id'],quote='summary',level='body')])
        result=interpret(dict(requirements=[item]),rows)[0]
        self.assertEqual(result['status'],'unknown');self.assertEqual(result['evidence'][0]['level'],'snippet')
        item['reason']='conflicting canonical field'
        with self.assertRaises(ValueError):interpret(dict(requirements=[item]),rows)

    def test_actual_receipts_can_ground_observations_but_handoffs_are_not_body(self):
        from desk_companion.feedback_review import material,interpret
        rows=material('run preview','ready',[],[dict(call_id='start',result=dict(service_ready=True))],
            [dict(id='child',answer='ready')])
        receipt=next(r for r in rows if r['level']=='actual_receipt')
        item=dict(requirement='preview observed',dimension='claim_support',status='supported',reason='actual service receipt',evidence=[dict(source_id=receipt['id'],quote=receipt['segments'][0])])
        self.assertEqual(interpret(dict(requirements=[item]),rows)[0]['status'],'pass')
        handoff=rows[-1];item['evidence']=[dict(source_id=handoff['id'],quote=handoff['segments'][0])]
        self.assertEqual(interpret(dict(requirements=[item]),rows)[0]['status'],'unknown')

    def test_candidate_equivalence_does_not_normalize_external_body_quotes(self):
        from desk_companion.feedback_review import material,interpret
        sources=[dict(level='body',url='https://example.org/source',text='{"n": 18}')]
        rows=material('read','{"n": 18}',sources,[])
        item=dict(requirement='quoted body',dimension='claim_support',status='supported',reason='model claim',evidence=[dict(source_id=rows[-1]['id'],quote='{"n":18}')])
        self.assertEqual(interpret(dict(requirements=[item]),rows)[0]['status'],'unknown')

    def test_verified_project_observation_is_not_research_body(self):
        from desk_companion.feedback_review import material,interpret
        from desk_companion.delivery_feedback import assess
        source=dict(kind='project_preview',level='service_preview',url='http://127.0.0.1:12345/',title='verified preview',scope='startup HTTP observation, not research body')
        rows=material('verify startup','verified',[source],[]);row=rows[-1]
        item=dict(requirement='startup observed',dimension='claim_support',status='supported',reason='recorded observation',evidence=[dict(source_id=row['id'],quote=row['segments'][0])])
        check=interpret(dict(requirements=[item]),rows)[0]
        self.assertEqual(check['status'],'pass');self.assertEqual(check['evidence'][0]['level'],'service_preview')
        _,checks,_=assess(dict(text='read research',collab_run='test',required_sources=['web']),'[preview](http://127.0.0.1:12345/)',[source],None,[])
        self.assertTrue(any(c['id']=='required_source_web'and c['status']=='fail'for c in checks))

    def test_child_goal_does_not_inherit_root_output_format(self):
        from desk_companion.delivery_feedback import assess
        req=dict(text='只输出JSON对象。',objective='最终用Markdown段落。',parent_run='actual-parent',stage='deliver',collab_run='child')
        _,checks,_=assess(req,'plain text',[],dict(complete=True,missing=[]),[])
        self.assertTrue(any(c['id']=='output_format'and c['status']=='fail'for c in checks))
        self.assertEqual(feedback.current_goal({**req,'parent_run':None}),req['objective'])

    def test_rule_reference_keeps_canonical_scope_and_cannot_replace_semantics(self):
        from desk_companion.feedback_review import material,interpret
        rows=material('check facts','candidate',[],[])
        known=[feedback.check('citation_provenance','source_url','pass','仅核对URL来源。')]
        item=dict(requirement='overbroad model claim',dimension='source_url',rule_id='citation_provenance',status='supported',reason='matches',evidence=[])
        result=interpret(dict(requirements=[item]),rows,verified_checks=known)
        self.assertEqual(result[0]['status'],'pass');self.assertEqual(result[0]['verified_by'],'citation_provenance')
        self.assertEqual(result[0]['requirement'],known[0]['message'])
        self.assertFalse(feedback.report(result)['ok'])
        for changes in [dict(rule_id='invented'),dict(dimension='reading'),dict(dimension='claim_support')]:
            invalid=interpret(dict(requirements=[{**item,**changes}]),rows,verified_checks=known)
            self.assertEqual(invalid[0]['status'],'unknown');self.assertFalse(feedback.report(invalid)['ok'])
        self.assertEqual(interpret(dict(requirements=[item]),rows,verified_checks=[{**known[0],'status':'unknown'}])[0]['status'],'unknown')


if __name__=='__main__':unittest.main()
