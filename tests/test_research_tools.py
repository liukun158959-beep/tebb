import unittest

from desk_companion.research_tools import ResearchTools


class ResearchToolsTests(unittest.TestCase):
    def create(self,**kwargs):
        self.events=[];self.sources=[];self.discovered={'https://example.org/read'}
        return ResearchTools(sources=self.sources,discovered=self.discovered,emit=lambda k,d:self.events.append((k,d)),
            search_limit=2,fetch_limit=2,**kwargs)

    def test_denied_or_malformed_url_does_not_spend_actual_read_allowance(self):
        reads=[];source=dict(url='https://example.org/read',level='body',text='Actual body',links=['http://[broken'])
        tools=self.create(fetch=lambda url:(reads.append(url)or source))
        for url in ('http://[broken','https://example.org/not-discovered'):
            self.assertFalse(tools.invoke('web_fetch',dict(url=url)).ok)
        self.assertEqual(tools.fetch_count,0);self.assertEqual(reads,[])
        self.assertTrue(tools.invoke('web_fetch',dict(url=source['url'])).ok)
        self.assertEqual(reads,[source['url']]);self.assertEqual(self.sources,[source]);self.assertNotIn('http://[broken',self.discovered)

    def test_source_budget_applies_to_same_batch_without_hidden_attempts(self):
        queries=[];tools=self.create(search=lambda query:(queries.append(query)or 'Actual snippet https://example.org/read'))
        for i in range(3):tools.invoke('web_search',dict(query='query '+str(i)))
        self.assertEqual(len(queries),2);self.assertEqual(tools.search_count,2);self.assertEqual(tools.successes,2)

    def test_untrusted_failure_is_redacted_and_not_a_source(self):
        def fetch(url):raise ConnectionError('request failed credential-only-for-test')
        tools=self.create(fetch=fetch,redact='credential-only-for-test')
        outcome=tools.invoke('web_fetch',dict(url='https://example.org/read'))
        self.assertFalse(outcome.ok);self.assertNotIn('credential-only-for-test',str(self.events)+outcome.text)
        self.assertEqual(self.sources,[]);self.assertEqual(tools.successes,0)

    def test_malformed_provider_result_cannot_become_body_evidence(self):
        tools=self.create(fetch=lambda url:dict(url='http://[broken',level='body',text='Untrusted body'))
        self.assertFalse(tools.invoke('web_fetch',dict(url='https://example.org/read')).ok)
        self.assertEqual(self.sources,[]);self.assertEqual(tools.successes,0)
