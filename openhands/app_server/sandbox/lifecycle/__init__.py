"""The app pauses idle sandboxes, and deletes ones that have not run for days.

This covers every backend that subclasses ``ManagedSandboxServiceInjector``:
Docker, E2B and k8s agent-sandbox. runtime-api manages the remote backend's
sandboxes itself.

The background worker (``openhands.app_server.worker``) runs two jobs:

- ``sandbox_lifecycle:sweep`` runs every minute. It finds the sandboxes that
  may be due for a pause or a delete, from the sandbox table alone, and queues
  a check for each.
- ``sandbox_lifecycle:check`` checks one sandbox against the provider and its
  agent server, and pauses or deletes it when a rule says so. ``rules.decide``
  holds the rules.
"""
