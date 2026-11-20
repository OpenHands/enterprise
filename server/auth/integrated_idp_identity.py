"""Shared identity helpers for the integrated password IDP."""

from __future__ import annotations

import uuid

# Fixed namespace for deterministic user-id derivation from email. Required by
# ``UserStore.create_user``'s identity-preservation contract.
_IDP_NAMESPACE = uuid.UUID('a1b2c3d4-e5f6-7890-abcd-ef1234567890')


def derive_idp_user_id(email: str) -> str:
    """Derive the stable user UUID used for an integrated-IDP email."""
    normalized = email.strip().lower()
    return str(uuid.uuid5(_IDP_NAMESPACE, normalized))
