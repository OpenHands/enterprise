# Event Callbacks

Manages webhooks and event callbacks for external system integration.

## Overview

This module provides webhook and callback functionality, allowing external systems to receive notifications when specific events occur within OpenHands conversations.

## Key Components

- **EventCallbackService**: Abstract service for callback CRUD operations
- **SqlEventCallbackService**: SQL-based callback storage implementation
- **EventWebhookRouter**: FastAPI router for webhook endpoints

## Features

- Webhook registration and management
- Event filtering by type and conversation
- Callback result tracking and status monitoring
- Retry logic for failed webhook deliveries
- Secure webhook authentication

## Acknowledged event replay

With `durable_events` enabled in the SDK webhook spec, the agent-server sends
persisted events in their EventLog sequence to `/event-stream/{conversation_id}`.
Both methods use the usual sandbox session API key and conversation authorization.

- GET returns the highest contiguous sequence stored, or `-1` before any events.
- POST accepts `{"events": [{"seq": 0, "event": {...}}]}`. Sequences in each batch
  must be contiguous. The acknowledgement covers the submitted range through
  its last sequence. A lost response can be retried with the same batch.
- GCS event objects retain their existing JSON paths. Their `event-seq` metadata
  records the SDK sequence. The `.event-stream/checkpoint` object under the
  conversation prefix stores `acked_through`. Objects beyond the checkpoint can
  belong to a batch whose write failed partway through.

Deploy a GCS-backed receiver and a compatible agent-server image before setting
`AGENT_SANDBOX_DURABLE_EVENT_WEBHOOKS=1`. An existing event ID is accepted for
backfill if its payload matches; a conflicting event blocks the cursor.
Synthetic subscriber snapshots are outside the EventLog. The acknowledgement
covers event storage, while callbacks still run on a best-effort basis.
Replay needs the sandbox's persisted EventLog and a current session API key.
