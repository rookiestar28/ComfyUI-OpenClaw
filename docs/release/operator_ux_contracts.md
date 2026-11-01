# Operator UX Acceleration Bundle Contracts

**Version**: 1.0.0 (Baseline)
**Date**: 260216
**Status**: DRAFT -> FROZEN

This document defines the interface contracts for the Operator UX Acceleration Bundle (F49, F51, F52, F50).

## 1. Banner Status (F49)

Used by `QueueMonitor` and other UI components to display transient status or recovery guidance.

### Schema (TypeScript)

```typescript
type BannerSeverity = 'info' | 'success' | 'warning' | 'error';

interface BannerStatus {
  /** Unique identifier for deduplication (e.g., 'backpressure_123') */
  id: string;

  /** Visual severity level */
  severity: BannerSeverity;

  /** Display message */
  message: string;

  /** Source of the banner (e.g., 'system', 'queue', 'connectivity') */
  source: string;

  /** Time-to-live in milliseconds. If missing, persists until dismissed or replaced. */
  ttl_ms?: number;

  /** Whether the user can manually dismiss the banner */
  dismissible?: boolean;

  /** Optional clickable action */
  action?: {
    label: string;
    /** Target type: 'url' | 'tab' | 'action' */
    type: string;
    /** Target value (URL, tab ID, or action name) */
    payload: string;
  };
}
```

### F49 Baseline (Current Behavior)

- **Monitoring**: Polls `/health` every 10s.
- **Triggers**: Checks `stats.observability.total_dropped > 0`.
- **Display**: Simple DOM injection of canonical `.openclaw-banner` markup; legacy `.moltbot-banner` compatibility selectors remain available through centralized runtime aliasing.
- **Connectivity posture**: Queue-monitor disconnect handling separates telling the operator from recording the incident. The first failed check raises a transient, non-persisted warning so the operator learns immediately; a durable error is written only after bounded repeated failure. Bootstrap stays silent in both respects, because the sidebar can legitimately start before the backend does, and consecutive-failure counting resets on any healthy observation so isolated blips cannot accumulate. A transient startup miss, or a host restart that completes within the confirmation window, therefore leaves no durable incident record.
- **Connectivity resolution**: When connectivity is restored, the queue monitor retires the durable connectivity entry rather than leaving it to be cleared by hand; the high-load warning is retired the same way once the host reports no dropped events. A persisted alert describes an active condition, so a condition that has ended does not keep a row.
- **Limitations**: No 'info'/'success' states, simplistic dedupe.

## 1.1 Notification Center (F66)

Persistent operator notifications are the durable counterpart to transient banners and toasts.

### Schema (TypeScript)

```typescript
interface NotificationEntry {
  id: string;
  severity: BannerSeverity;
  message: string;
  source: string;
  created_at: string;
  updated_at: string;
  count: number;
  acknowledged_at?: string | null;
  dismissed_at?: string | null;
  action?: {
    label: string;
    type: 'url' | 'tab' | 'action';
    payload: string;
  };
  metadata?: Record<string, unknown>;
}
```

### F66 Baseline

- Warning/error banners and selected operator toasts are mirrored into the in-app notification center.
- Entries are deduplicated by source-specific keys and persisted in local storage across reloads.
- `Dismiss` hides an entry from the active list without deleting the historical record from storage. While that record stands, an identical alert from the same source is suppressed rather than resurrected, so a polling producer cannot undo the operator's dismissal while the condition is still ongoing.
- Producers must therefore resolve a condition that has *ended*, which removes the entry outright. Resolution is what allows the same alert to appear again the next time the condition genuinely occurs; without it, one dismissal would silence that alert for the life of the browser profile.
- `Acknowledge` clears unread state while keeping the entry visible.
- Notification `message` / `source` fields are treated as untrusted text at the render sink and must stay escaped before DOM insertion; notification content is not a supported HTML surface.
- Sources with jump targets should attach a tab/action deep link so operators can navigate directly to the affected surface.
- Canonical `openclaw-*` DOM/class ownership should be authored once in the shell/templates; any retained `moltbot-*` class compatibility must come from shared runtime alias helpers instead of duplicated markup.

## 2. Context Actions (F51)

Defines quick actions available in the node context menu (via ComfyUI extension hooks).

### Schema (TypeScript)

```typescript
interface ContextAction {
  /** Unique action ID */
  id: string;

  /** Display label */
  label: string;

  /** Optional icon class or emoji */
  icon?: string;

  /** Primary target category */
  target: 'explorer' | 'jobs' | 'settings' | 'doctor' | 'url';

  /** Context data required for the action */
  payload?: {
    node_type?: string;
    node_id?: string;
    widget_name?: string;
    [key: string]: any;
  };

  /** Filter function to determine availability (frontend-side) */
  condition?: (node: any) => boolean;
}
```

## 3. Parameter Lab

Contracts for bounded parameter sweeps and experiment orchestration.

### Sweep Request Schema (JSON)

```json
{
  "workflow_json": "...",
  "params": [
    {
      "node_id": "10",
      "widget_name": "cfg",
      "values": [6.0, 7.0, 8.0],
      "strategy": "grid"
    },
    {
      "node_id": "loader-alpha",
      "widget_name": "seed",
      "values": [41, 42]
    }
  ]
}
```

Contract notes:

- `node_id` preserves the complete host graph identifier. Numeric text such as `"10"`,
  non-numeric IDs, and IDs containing colons must not be coerced or split when an exact node
  exists. Path interpretation is used only after exact-ID resolution.
- Shared subgraph definitions require an unambiguous instance path. Definition-object identity
  alone cannot select an execution instance. Duplicate IDs or unresolved target collisions
  reject an edit or replay before widget values are written.
- Experiment parameter keys such as `"10.cfg"` are display/storage keys derived from the original `node_id` plus `widget_name`; they are not a separate numeric node contract.
- Sweep values are limited to bounded strings, booleans, integers, and finite numbers. Null,
  arrays, objects, non-finite numbers, overlong strings, presentation-ambiguous duplicates, and
  unsupported strategies fail validation instead of being coerced.
- Sweep creation supports `grid` strategy only. The backend policy is authoritative and limits a
  request to 5 MiB, workflow text to 4 MiB, eight dimensions, 50 values per dimension, and 50
  generated combinations. Compare creation accepts at most eight scalar items.

### Queue ownership receipt

- The coordinator observes the host's reviewed `promptQueueing` and `promptQueued` request
  boundaries, correlating their integer `requestId` and `batchCount` fields.
- It writes a transient UUID receipt only into the matching serialized workflow and returns the
  exact `promptId` / `requestId` pair used to route bounded lifecycle event metadata.
- Unsupported event APIs, malformed or missing boundaries, pre-existing unobserved host queue
  activity, receipt collisions, timeouts, and ambiguous batch ownership fail explicitly. There is
  no fallback to a globally recent prompt ID.

### Experiment Result Schema (JSON)

```json
{
  "experiment_id": "exp_abc123",
  "run_id": "run_xyz789",
  "timestamp": 1234567890,
  "params": {
    "10.cfg": 7.0,
    "3.seed": 42
  },
  "status": "completed",
  "outputs": {
    "9.image": ["filename_1.png"]
  },
  "error": null
}
```

## 4. Model Compare (F50)

Contracts for multi-model side-by-side comparison.

### Compare Request Schema (JSON)

```json
{
  "prompt": "User input text...",
  "candidates": [
    { "provider": "openai", "model": "gpt-4o" },
    { "provider": "anthropic", "model": "claude-3-5-sonnet" }
  ],
  "config": {
    "temperature": 0.7,
    "max_tokens": 1000
  },
  "timeout_ms": 30000
}
```

### Compare Result Schema (JSON)

```json
{
  "run_id": "cmp_def456",
  "candidates": [
    {
      "provider": "openai",
      "model": "gpt-4o",
      "output": "Result A...",
      "latency_ms": 1200,
      "cost_usd": 0.001,
      "error": null
    },
    {
      "provider": "anthropic",
      "model": "claude-3-5-sonnet",
      "output": "Result B...",
      "latency_ms": 1400,
      "cost_usd": 0.003,
      "error": null
    }
  ]
}
```
