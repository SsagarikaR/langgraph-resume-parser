# FastAPI Interview Guidelines

Interviewers should focus on:
- Request validation and response models with Pydantic.
- Dependency injection (Depends) for auth, DB sessions, and config.
- async vs sync endpoints and when blocking calls hurt throughput.
- Error handling, status codes, and API versioning.
- Testing endpoints with TestClient / httpx.

Red flags: calls blocking I/O inside async endpoints without knowing the impact.
