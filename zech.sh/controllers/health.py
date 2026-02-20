from litestar import Controller, Response, get


class HealthController(Controller):
    """Unauthenticated health check for k8s probes."""

    path = "/healthz"

    @get("/")
    async def health(self) -> Response:
        return Response(content={"ok": True}, status_code=200)
