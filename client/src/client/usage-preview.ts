// Generated from isolated synthetic metadata; not measured user usage.
export default {
  "ok": true,
  "version": 1,
  "filters": {
    "period": "30",
    "model": "*",
    "project": "*",
    "channel": "*",
    "kind": "model"
  },
  "range": {
    "since": 1790870400.0,
    "until": 1793415600.0,
    "timezone": "UTC+8"
  },
  "totals": {
    "model_calls": 2,
    "tool_calls": 1,
    "network_attempts": 2,
    "network_unobserved_calls": 0,
    "model_states": {
      "failed": 1,
      "returned": 1
    },
    "tool_states": {
      "returned": 1
    },
    "cost_cny_known": 0.00248,
    "cost_unknown_calls": 1,
    "input_tokens_known": 1000,
    "input_tokens_unknown_calls": 1,
    "output_tokens_known": 120,
    "output_tokens_unknown_calls": 1,
    "cached_tokens_known": 800,
    "cached_tokens_unknown_calls": 1,
    "reasoning_tokens_known": 0,
    "reasoning_tokens_unknown_calls": 2,
    "total_tokens_known": 1120,
    "usage_incomplete_calls": 1,
    "tasks": 1,
    "diagnostics": 0,
    "physical_attempts": 1,
    "task_states": {
      "failed": 1
    },
    "physical_states": {
      "timed_out": 1
    }
  },
  "by_model": [
    {
      "key": "sample-model:sample-route",
      "model": "示例模型",
      "model_id": "sample-model",
      "route": "sample-route",
      "model_calls": 2,
      "tool_calls": 0,
      "network_attempts": 2,
      "network_unobserved_calls": 0,
      "model_states": {
        "failed": 1,
        "returned": 1
      },
      "tool_states": {},
      "cost_cny_known": 0.00248,
      "cost_unknown_calls": 1,
      "input_tokens_known": 1000,
      "input_tokens_unknown_calls": 1,
      "output_tokens_known": 120,
      "output_tokens_unknown_calls": 1,
      "cached_tokens_known": 800,
      "cached_tokens_unknown_calls": 1,
      "reasoning_tokens_known": 0,
      "reasoning_tokens_unknown_calls": 2,
      "total_tokens_known": 1120,
      "usage_incomplete_calls": 1
    }
  ],
  "by_day": [
    {
      "day": "2026-10-30",
      "tasks": 1,
      "model_calls": 1,
      "tool_calls": 1,
      "network_attempts": 1,
      "network_unobserved_calls": 0,
      "model_states": {
        "returned": 1
      },
      "tool_states": {
        "returned": 1
      },
      "cost_cny_known": 0.00248,
      "cost_unknown_calls": 0,
      "input_tokens_known": 1000,
      "input_tokens_unknown_calls": 0,
      "output_tokens_known": 120,
      "output_tokens_unknown_calls": 0,
      "cached_tokens_known": 800,
      "cached_tokens_unknown_calls": 0,
      "reasoning_tokens_known": 0,
      "reasoning_tokens_unknown_calls": 1,
      "total_tokens_known": 1120,
      "usage_incomplete_calls": 0
    },
    {
      "day": "2026-10-31",
      "tasks": 0,
      "model_calls": 1,
      "tool_calls": 0,
      "network_attempts": 1,
      "network_unobserved_calls": 0,
      "model_states": {
        "failed": 1
      },
      "tool_states": {},
      "cost_cny_known": 0,
      "cost_unknown_calls": 1,
      "input_tokens_known": 0,
      "input_tokens_unknown_calls": 1,
      "output_tokens_known": 0,
      "output_tokens_unknown_calls": 1,
      "cached_tokens_known": 0,
      "cached_tokens_unknown_calls": 1,
      "reasoning_tokens_known": 0,
      "reasoning_tokens_unknown_calls": 1,
      "total_tokens_known": 0,
      "usage_incomplete_calls": 1
    }
  ],
  "options": {
    "models": [
      {
        "key": "sample-model:sample-route",
        "model": "示例模型",
        "model_id": "sample-model",
        "route": "sample-route"
      }
    ],
    "projects": [
      "value:sample-project"
    ],
    "channels": [
      "value:local"
    ]
  },
  "items": [
    {
      "root": "task:sample-goal",
      "task": "sample-worker",
      "project_id": "sample-project",
      "channel": "local",
      "source": "task",
      "model": "示例模型",
      "model_id": "sample-model",
      "model_key": "sample-model:sample-route",
      "route": "sample-route",
      "version": 1,
      "id": "sample-call-2",
      "kind": "model",
      "started": 1793412000.0,
      "updated": 1793460169.111176,
      "state": "failed",
      "usage": {
        "input_tokens": null,
        "output_tokens": null,
        "cached_tokens": null,
        "reasoning_tokens": null,
        "total_tokens": null,
        "usage_quality": "unknown"
      },
      "cost_cny": null,
      "network_attempts": [
        {
          "id": "sample-call-2:1",
          "state": "failed",
          "status": null
        }
      ]
    },
    {
      "root": "task:sample-goal",
      "task": "sample-worker",
      "project_id": "sample-project",
      "channel": "local",
      "source": "task",
      "model": "示例模型",
      "model_id": "sample-model",
      "model_key": "sample-model:sample-route",
      "route": "sample-route",
      "version": 1,
      "id": "sample-call-1",
      "kind": "model",
      "started": 1793325600.0,
      "updated": 1793460169.0925367,
      "state": "returned",
      "usage": {
        "input_tokens": 1000,
        "output_tokens": 120,
        "cached_tokens": 800,
        "reasoning_tokens": null,
        "total_tokens": 1120,
        "usage_quality": "complete"
      },
      "cost_cny": 0.00248,
      "network_attempts": [
        {
          "id": "sample-call-1:1",
          "state": "headers",
          "status": 200
        }
      ]
    }
  ],
  "next_cursor": "",
  "legacy": {
    "ok": true,
    "records": 0,
    "input_tokens_known": 0,
    "output_tokens_known": 0,
    "input_unknown_records": 0,
    "output_unknown_records": 0,
    "cost_cny_known": 0.0,
    "cost_unknown_records": 0,
    "merged": false
  },
  "coverage": {
    "model": "adapter_attempts",
    "tool": "actual_adapter_attempts",
    "history_merged": false,
    "model_filter_scope": "model_calls_only",
    "task_time": "first_submission",
    "attempt_time": "adapter_start",
    "payloads": false,
    "first_observation": 1793325600.0,
    "unobserved_model_admissions": 0,
    "admission_scope": "matched_task_roots_not_physical_calls"
  }
};
