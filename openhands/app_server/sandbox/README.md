# Sandbox Management

Manages sandbox environments for secure agent execution within OpenHands.

## Overview

Since agents can do things that may harm your system, they are typically run inside a sandbox (like a Docker container). This module provides services for creating, managing, and monitoring these sandbox environments.

## Key Components

- **SandboxService**: Abstract service for sandbox lifecycle management
- **DockerSandboxService**: Docker-based sandbox implementation
- **SandboxSpecService**: Manages sandbox specifications and templates
- **SandboxRouter**: FastAPI router for sandbox endpoints
- **sandbox_store**: The `v1_sandbox` table recording who owns each docker and
  E2B sandbox, and the ownership-scoping helper both backends read through.
  `RemoteSandboxService` keeps its own `v1_remote_sandbox` table.

## Features

- Secure containerized execution environments
- Sandbox lifecycle management (create, start, stop, destroy)
- Multiple sandbox backend support (Docker, Remote, Local)
- User-scoped sandbox access control
