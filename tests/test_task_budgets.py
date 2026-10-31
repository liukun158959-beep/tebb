import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desk_companion.task_budgets import Ledger,BudgetExceeded


class RootBudgets(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.ledger=Ledger(Path(self.tmp.name)/'budget.sqlite3')
        self.root=self.ledger.create('goal','research')
        self.ledger.bind(self.root,'parent',root_member=True)
        self.ledger.bind(self.root,'child')

    def test_parallel_children_cannot_overspend_and_crashed_reservations_remain(self):
        with self.ledger.change(self.root)as root:
            root['limits'].update(output_tokens=1000000)
        def reserve(_):
            try:return self.ledger.reserve_call('child',100,phase='control')[0]
            except BudgetExceeded:return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=20)as pool:
            calls=[c for c in pool.map(reserve,range(40))if c]
        self.assertEqual(len(calls),14)  # Root's final two calls cannot be borrowed.
        view=self.ledger.view(self.root)
        self.assertEqual(view['consumed']['model_calls'],14)
        self.assertEqual(view['consumed']['output_unknown_calls'],14)
        self.assertEqual(view['consumed']['output_occupied'],14*8192)
        self.assertIsNone(view['remaining']['input_tokens'])
        parent=self.ledger.reserve_call('parent',100,phase='feedback_review')[0]
        self.ledger.settle('parent',parent,dict(prompt_tokens=100,completion_tokens=20))
        self.assertEqual(self.ledger.view(self.root)['consumed']['model_calls'],15)

    def test_parent_and_resume_retain_spent_while_new_goal_is_separate(self):
        call,_=self.ledger.reserve_call('child',10,phase='control')
        self.ledger.settle('child',call,dict(prompt_tokens=10,completion_tokens=5))
        self.ledger.finish(self.root)
        with self.assertRaises(BudgetExceeded):self.ledger.reserve_call('child',10)
        self.ledger.resume(self.root)
        self.ledger.bind(self.root,'continued',root_member=True)
        self.ledger.reserve_call('continued',10,phase='control')
        self.assertEqual(self.ledger.create('goal','coding'),self.root)
        self.assertEqual(self.ledger.view(self.root)['consumed']['model_calls'],2)
        other=self.ledger.create('new goal','simple')
        self.assertNotEqual(other,self.root)
        self.assertEqual(self.ledger.view(other)['consumed']['model_calls'],0)

    def test_unknown_usage_is_not_refunded_but_zero_usage_is_known(self):
        first,_=self.ledger.reserve_call('child',None,phase='control')
        self.ledger.settle('child',first,None)
        second,_=self.ledger.reserve_call('parent',0,phase='feedback_review')
        self.ledger.settle('parent',second,dict(prompt_tokens=0,completion_tokens=0))
        self.ledger.settle('parent',second,dict(prompt_tokens=100,completion_tokens=100))
        used=self.ledger.view(self.root)['consumed']
        self.assertEqual(used['input_unknown_calls'],1)
        self.assertEqual(used['output_unknown_calls'],1)
        self.assertEqual(used['output_occupied'],8192)
        self.assertEqual(used['input_tokens'],0)

    def test_three_work_calls_without_new_progress_stop_but_review_is_available(self):
        for _ in range(3):
            call,_=self.ledger.reserve_call('child',10)
            self.ledger.settle('child',call,dict(prompt_tokens=10,completion_tokens=10))
        with self.assertRaises(BudgetExceeded)as raised:self.ledger.reserve_call('child',10)
        self.assertEqual(raised.exception.reason['dimension'],'no_progress')
        self.ledger.reserve_call('parent',10,phase='feedback_review')
        self.assertEqual(self.ledger.view(self.root)['extensions'],[])

    def test_distinct_progress_allows_only_one_finite_extension(self):
        self.ledger.progress('child','body','source','v1')
        self.ledger.progress('child','body','source','v1')
        self.assertEqual(len(self.ledger.read(self.root)['progress']),1)
        for _ in range(30):
            try:self.ledger.reserve_tool('child')
            except BudgetExceeded:break
        view=self.ledger.view(self.root)
        self.assertEqual(len(view['extensions']),1)
        self.assertEqual(view['consumed']['tool_calls'],30)
        with self.assertRaises(BudgetExceeded):self.ledger.reserve_tool('child')
        self.assertEqual(self.ledger.view(self.root)['limits']['tool_calls'],30)

    def test_wait_for_user_pauses_only_when_members_have_ended(self):
        with patch('desk_companion.task_budgets.time.time',return_value=100):self.ledger.start('child')
        with patch('desk_companion.task_budgets.time.time',return_value=102):
            self.ledger.waiting(self.root,True)
            self.assertEqual(self.ledger.view(self.root)['consumed']['time_ms'],2000)
            self.ledger.end('child')
        with patch('desk_companion.task_budgets.time.time',return_value=10000):
            self.assertEqual(self.ledger.view(self.root)['consumed']['time_ms'],2000)
            self.ledger.waiting(self.root,False)
        with patch('desk_companion.task_budgets.time.time',return_value=10001):
            self.assertEqual(self.ledger.view(self.root)['consumed']['time_ms'],3000)

    def test_hard_time_limit_never_extended_and_provider_violation_blocks(self):
        root=self.ledger.create('hard','simple',1000);self.ledger.bind(root,'hard')
        with patch('desk_companion.task_budgets.time.time',return_value=100):self.ledger.start('hard')
        self.ledger.progress('hard','body','s','1')
        with patch('desk_companion.task_budgets.time.time',return_value=102):
            with self.assertRaises(BudgetExceeded):self.ledger.reserve_call('hard',10)
        self.assertEqual(self.ledger.view(root)['limits']['time_ms'],1000)
        call,cap=self.ledger.reserve_call('child',10,enforced=True)
        self.ledger.settle('child',call,dict(prompt_tokens=10,completion_tokens=cap+1))
        with self.assertRaises(BudgetExceeded):self.ledger.reserve_tool('parent')

    def test_physical_task_cannot_switch_root_or_gain_finish_membership(self):
        other=self.ledger.create('other')
        with self.assertRaises(ValueError):self.ledger.bind(other,'child',root_member=True)
        self.ledger.bind(self.root,'child',root_member=True)
        self.assertFalse(self.ledger.member('child')['root_member'])

    def test_small_integer_quota_extension_never_exceeds_twenty_five_percent(self):
        root=self.ledger.create('clarify-limits','clarify');self.ledger.bind(root,'clarify')
        self.ledger.progress('clarify','body','source','first')
        for _ in range(5):self.ledger.reserve_tool('clarify')
        view=self.ledger.view(root)
        self.assertEqual(view['limits']['model_calls'],7)
        self.assertEqual(view['limits']['tool_calls'],5)
        self.assertEqual(len(view['extensions']),1)

    def test_seen_progress_remains_deduplicated_after_eighty_observations(self):
        for index in range(90):self.ledger.progress('child','body','source',str(index))
        self.ledger.progress('child','body','source','0')
        self.assertEqual(len(self.ledger.read(self.root)['progress']),90)
