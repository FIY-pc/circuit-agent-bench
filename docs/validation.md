# Validation status

This file records observed evidence rather than planned capabilities. It is
updated when integration checks finish. Machine-local logs and provider records
remain in ignored run directories.

- Harbor 0.25.0, Python 3.12, Linux Docker: candidate image starts the actual host;
  candidate image has no benchmark grader or reference circuits.
- Independent verifier container uses Docker Compose `network_mode: none`.
  Empty-artifact lifecycle completed with a functional failure and no Harbor
  exception. This is infrastructure evidence, not a model result.
- Real-model integration runs and expanded task/native checks are in progress.
- Windows/WSL2, external collaborator setup and repeat-run capability estimates
  have not yet been validated.
