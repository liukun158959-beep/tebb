import json
import unittest

from desk_companion import web_sources as sources
from desk_companion.feedback_review import material


class ToolURLParsingTests(unittest.TestCase):
    def test_actual_flask_log_json_keeps_escape_sequences_out_of_url(self):
        # Same boundary as the real project_log post-processing failure (#208).
        log=' * Running on http://172.17.0.57:5000\n\x1b[33mPress CTRL+C to quit\x1b[0m\n'
        links=sources.urls(json.dumps(dict(text=log)))
        self.assertEqual(links,['http://172.17.0.57:5000'])
        self.assertEqual([sources.url_key(link)for link in links],['http://172.17.0.57:5000/'])
        self.assertEqual(sources.urls(log),links)
        self.assertEqual(sources.evidence_sources([dict(ok=True,text=log)]),[])

    def test_malformed_urls_do_not_abort_catalog_or_citation_review(self):
        valid=dict(url='https://example.org/read',level='body',text='Actual original sentence.')
        invalid=[dict(url=url,level='body',text='untrusted')for url in
            ('http://[broken','http://[not-ipv6]/','http://example.org:invalid/','http://example.org/\x1b[0m')]
        catalog=sources.source_catalog([*invalid,valid]);self.assertEqual(len(catalog),1)
        audit=sources.check_citations('[source](https://example.org/read)',[*invalid,valid])
        self.assertTrue(audit['ok']);self.assertEqual(audit['pages_read'],1)
        rows=material('goal','draft',[*invalid,valid],[])
        bodies=[row for row in rows if row['level']=='body'];self.assertEqual(bodies[0]['segments'],[valid['text']])
        self.assertEqual(sources.urls('http://[broken http://[not-ipv6]/'),[])

    def test_real_ipv6_brackets_and_markdown_boundaries_are_preserved(self):
        links=sources.urls('[local](http://[::1]) [public](https://[2001:db8::1]/docs) https://example.org/last]')
        self.assertEqual(links,['http://[::1]','https://[2001:db8::1]/docs','https://example.org/last'])
        self.assertTrue(all(sources.url_key(link)for link in links))

    def test_bad_requested_alias_does_not_promote_an_unread_url(self):
        source=dict(url='https://example.org/read',requested_url='http://[broken',level='snippet',text='summary')
        self.assertTrue(sources.check_citations('https://example.org/read',[source])['ok'])
        self.assertFalse(sources.check_citations('https://example.org/not-read',[source])['ok'])
