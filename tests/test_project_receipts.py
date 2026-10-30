import unittest
from desk_companion.project_receipts import preview_source,receipt,checkout_source
from desk_companion.web_sources import check_citations


class ProjectReceiptTests(unittest.TestCase):
    def ready(self):
        return dict(ok=True,service_ready=True,environment=dict(id='env'),service=dict(id='service',url='http://127.0.0.1:12345/',host_port=12345,health=dict(ok=True,status=200)))

    def test_verified_preview_is_link_evidence_without_becoming_research_body(self):
        source=preview_source(self.ready());self.assertIsNotNone(source)
        audit=check_citations('[预览](http://127.0.0.1:12345/)',[source])
        self.assertTrue(audit['ok']);self.assertEqual(audit['pages_read'],0)
        self.assertFalse(check_citations('[未验证](http://127.0.0.1:12346/)',[source])['ok'])

    def test_unverified_or_arbitrary_endpoint_cannot_be_whitelisted(self):
        for changes in [dict(ok=False),dict(service_ready=False)]:
            value=self.ready();value.update(changes);self.assertIsNone(preview_source(value))
        for url in ['https://example.com/','http://127.0.0.1:12346/','http://user@127.0.0.1:12345/','http://127.0.0.1:12345/path']:
            value=self.ready();value['service']['url']=url;self.assertIsNone(preview_source(value))

    def test_log_receipt_accepts_environment_id_without_copying_log_body(self):
        value=receipt('project_log','call',dict(environment='env'),dict(ok=True,environment='env',base64='not-in-event'))
        self.assertEqual(value['environment']['id'],'env');self.assertNotIn('base64',value)

    def test_checkout_links_are_provenance_and_do_not_count_as_read_research(self):
        result=dict(ok=True,environment=dict(repository='https://github.com/example/demo',commit='a'*40))
        source=checkout_source(result)
        audit=check_citations('[仓库](https://github.com/example/demo)',[source])
        self.assertTrue(audit['ok']);self.assertEqual(audit['pages_read'],0)
        result['ok']=False;self.assertIsNone(checkout_source(result))


if __name__=='__main__':unittest.main()
